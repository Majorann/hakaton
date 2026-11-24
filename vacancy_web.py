import argparse
from email.utils import formatdate
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import json
import logging
from os import fstat
from pathlib import Path
import re
from secrets import compare_digest, token_urlsafe
from threading import Condition, Thread
from time import time
from datetime import datetime, timezone
import tomllib
from urllib.parse import urlsplit

from vacancy_followup import FOLLOWUP_MINUTES, PENDING_STATUSES, VacancyArchive, follow_up, next_check
from vacancy_report import (
    DEFAULT_CONFIG, MAX_RESPONSE, MAX_UPLOAD, MAX_VACANCIES, ReportError, generate_report, load_local,
    load_upload, read_config, refresh_saved_findings, report_path, request_json, write_atomic,
)


ROOT = Path(__file__).resolve().parent
MODE_NAMES = ("api", "file")
REMINDER_HOURS = (0, 12, 24, 72)
ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/mark.svg": ("mark.svg", "image/svg+xml"),
}


def editable_settings(config):
    web = config.get("web", {})
    enabled = web.get("followup_enabled", False)
    minutes = web.get("followup_minutes", 1440)
    if type(enabled) is not bool or type(minutes) is not int or minutes not in FOLLOWUP_MINUTES:
        raise ReportError("Некорректные настройки отслеживания вакансий.")
    return {
        "max_vacancies": config["api"]["max_vacancies"],
        "model": config["ollama"]["model"],
        "archive_model": config["ollama"].get("archive_model", ""),
        "reminder_hours": web.get("reminder_hours", 24),
        "followup_enabled": enabled,
        "followup_minutes": minutes,
    }


def installed_models():
    response = request_json("http://127.0.0.1:11434/api/tags", "Ollama", 3)
    return sorted({
        row["name"] for row in response.get("models", [])
        if isinstance(row, dict) and isinstance(row.get("name"), str)
        and not row["name"].endswith((":cloud", "-cloud"))
    })


def update_toml(text, section, key, value):
    # Сохраняем остальную часть TOML пользователя, включая комментарии и другие разделы.
    header = re.search(rf"^\[{re.escape(section)}\][ \t]*(?:#[^\n]*)?$", text, re.MULTILINE)
    encoded = json.dumps(value, ensure_ascii=False)
    if header is None:
        return f"{text.rstrip()}\n\n[{section}]\n{key} = {encoded}\n"
    following = re.search(r"^\s*\[", text[header.end():], re.MULTILINE)
    end = header.end() + following.start() if following else len(text)
    body = text[header.end():end]
    updated, count = re.subn(
        rf"""^([ \t]*{re.escape(key)}[ \t]*=[ \t]*)(?:"(?:\\.|[^"\\\n])*"|'[^'\n]*'|[0-9_]+|true|false)([ \t]*(?:#[^\n]*)?)$""",
        lambda match: match[1] + encoded + match[2], body, count=1, flags=re.MULTILINE,
    )
    if not count:
        updated = f"\n{key} = {encoded}" + body
    return text[:header.end()] + updated + text[end:]


class Dashboard:
    def __init__(self, config_path):
        self.config_path = config_path
        config = read_config(config_path)
        self.reports = {mode: report_path(config, config_path, mode) for mode in MODE_NAMES}
        if self.reports["api"].resolve() == self.reports["file"].resolve():
            raise ReportError("рабочий и проверочный отчёты должны быть разными файлами")
        archive_path = self.reports["api"].with_name(f"{self.reports['api'].name}.archive.json")
        if archive_path.resolve() in (config_path.resolve(), self.reports["file"].resolve()):
            raise ReportError("архив должен быть отдельным файлом")
        self.archive = VacancyArchive(archive_path)
        self.archived_ids = {entry["original"]["id"] for entry in self.archive.entries}
        self.titles = {
            row["id"]: row["job-name"]
            for row in load_local(ROOT / "examples" / "api-response.json")
            if isinstance(row.get("job-name"), str)
        }
        self.lock = Condition()
        self.preferences = editable_settings(config)
        self.active = None
        self.jobs = {}
        self.followup_running = False
        self.auto_refill = False
        self.refill_at = None
        self.stopped = False

    def state(self):
        revisions = {}
        portal_updated = None
        for mode, path in self.reports.items():
            try:
                info = path.stat()
                revisions[mode] = f"{info.st_mtime_ns}:{info.st_size}"
                if mode == "api":
                    portal_updated = info.st_mtime
            except FileNotFoundError:
                revisions[mode] = None
        with self.lock:
            return {
                "active": self.active,
                "jobs": {mode: {**job, "results": list(job["results"]), "vacancies": list(job["vacancies"])}
                         for mode, job in self.jobs.items()},
                "revisions": revisions,
                "reference_titles": self.titles,
                "portal_updated": portal_updated,
                "reminder_hours": self.preferences["reminder_hours"],
                "followup_enabled": self.preferences["followup_enabled"],
                "followup_minutes": self.preferences["followup_minutes"],
                "followup_running": self.followup_running,
                "archive_revision": self.archive.revision,
                "queue_limit": self.preferences["max_vacancies"],
                "auto_refill": self.auto_refill,
                "refill_at": self.refill_at,
            }

    def settings(self):
        current = editable_settings(read_config(self.config_path))
        try:
            models = installed_models()
        except ReportError:
            models = None
        return {**current, "models": models, "max_allowed": MAX_VACANCIES}

    def save_settings(self, values):
        if not isinstance(values, dict):
            raise ReportError("Настройки должны быть объектом JSON.")
        limit, model, reminder = (values.get(key) for key in ("max_vacancies", "model", "reminder_hours"))
        if type(limit) is not int or not 1 <= limit <= MAX_VACANCIES:
            raise ReportError(f"Укажите целый лимит от 1 до {MAX_VACANCIES}.")
        if type(reminder) is not int or reminder not in REMINDER_HOURS:
            raise ReportError("Выберите срок напоминания из списка.")
        if not isinstance(model, str) or not model:
            raise ReportError("Выберите локальную модель Ollama.")
        enabled = values.get("followup_enabled")
        minutes = values.get("followup_minutes")
        if type(enabled) is not bool or type(minutes) is not int or minutes not in FOLLOWUP_MINUTES:
            raise ReportError("Выберите допустимый интервал отслеживания вакансий.")

        with self.lock:
            text = self.config_path.read_text(encoding="utf-8")
            current = editable_settings(tomllib.loads(text))
            archive_model = values.get("archive_model", current["archive_model"])
            if not isinstance(archive_model, str):
                raise ReportError("Выберите локальную модель для архива или пункт «Нет».")
            changed_models = {name for key, name in (("model", model), ("archive_model", archive_model))
                              if name and name != current[key]}
            if changed_models and not changed_models.issubset(installed_models()):
                raise ReportError("Выбранной модели нет в списке локальных моделей Ollama.")
            for section, key, value in (("api", "max_vacancies", limit), ("ollama", "model", model),
                                        ("ollama", "archive_model", archive_model),
                                        ("web", "reminder_hours", reminder), ("web", "followup_enabled", enabled),
                                        ("web", "followup_minutes", minutes)):
                if current[key] != value:
                    text = update_toml(text, section, key, value)
            updated = editable_settings(tomllib.loads(text))
            if updated != {"max_vacancies": limit, "model": model, "reminder_hours": reminder,
                           "archive_model": archive_model,
                           "followup_enabled": enabled, "followup_minutes": minutes}:
                raise ReportError("Не удалось сохранить настройки в текущем формате TOML.")
            if current != updated:
                write_atomic(self.config_path, text)
            self.preferences = updated
            if self.auto_refill and current["max_vacancies"] != limit:
                self.refill_at = time()
            self.lock.notify_all()
        return updated

    def archive_state(self):
        with self.lock:
            return {"revision": self.archive.revision, "entries": self.archive.entries}

    def save_complaint(self, values):
        if not isinstance(values, dict) or values.get("source") not in MODE_NAMES:
            raise ReportError("Не удалось прочитать жалобу.")
        row, comment = values.get("vacancy"), values.get("comment")
        if (not isinstance(row, dict) or not isinstance(row.get("id"), str)
                or not row["id"] or not isinstance(row.get("findings"), list)
                or not row["findings"] or not isinstance(comment, str) or len(comment) > 2000):
            raise ReportError("Не удалось прочитать данные вакансии или комментарий.")
        complaint = {"created_at": datetime.now(timezone.utc).isoformat(), "source": values["source"],
                     "comment": comment.strip(), "vacancy": row}
        path = self.reports["api"].parent / "complaints" / f"{token_urlsafe(18)}.json"
        write_atomic(path, json.dumps(complaint, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
        return {"saved": True}

    def mark_sent(self, values):
        if not isinstance(values, dict):
            raise ReportError("Не удалось прочитать отметку об отправке.")
        track = values.get("track")
        vacancy_id = values.get("id")
        findings = values.get("findings")
        if type(track) is not bool or not isinstance(vacancy_id, str) or not isinstance(findings, list) or not findings:
            raise ReportError("Не удалось прочитать отметку об отправке.")
        fingerprint = values.get("content_hash")
        with self.lock:
            previous = next((entry for entry in reversed(self.archive.entries)
                             if entry["original"]["id"] == vacancy_id), None)
            if previous:
                original = previous["original"]
                if original.get("content_hash") == fingerprint and original["findings"] == findings:
                    return previous
                if previous["status"] in PENDING_STATUSES:
                    raise ReportError("Эта вакансия уже находится в архиве.")
            if track and not self.preferences["followup_enabled"]:
                raise ReportError("Включите отслеживание исправлений в настройках.")
            live_rows = self.jobs["api"]["results"] if self.active == "api" else ()
            row = next((row for row in live_rows if row["id"] == vacancy_id), None)
            if row is None:
                try:
                    with self.reports["api"].open(encoding="utf-8") as report:
                        if previous and previous.get("checked_at", 0) >= fstat(report.fileno()).st_mtime:
                            raise ReportError("В архиве уже есть более свежая проверка этой вакансии.")
                        saved = json.load(report)
                        refresh_saved_findings(saved)
                        rows = saved["vacancies"]
                        row = next((row for row in rows if row["id"] == vacancy_id), None)
                except FileNotFoundError:
                    pass
            if row is None or row["findings"] != findings or row.get("content_hash") != fingerprint:
                raise ReportError("Карточка изменилась. Обновите её и повторите отметку.")
            entry = self.archive.mark_sent(row, track)
            self.archived_ids.add(vacancy_id)
            if self.auto_refill:
                self.refill_at = time()
            self.lock.notify_all()
            return entry

    def run_followups(self):
        while True:
            with self.lock:
                if self.stopped:
                    return
                if self.active is not None:
                    self.lock.wait()
                    continue
                minutes = self.preferences["followup_minutes"]
                pending = (entry for entry in self.archive.entries
                           if self.preferences["followup_enabled"] and entry["status"] in PENDING_STATUSES)
                entry = min(pending, key=lambda row: next_check(row, minutes), default=None)
                now = time()
                followup_at = next_check(entry, minutes) if entry else None
                if followup_at is None or followup_at > now:
                    if self.refill_at is not None and self.refill_at <= now:
                        self.start("api", None)
                    else:
                        wake_at = min((due for due in (followup_at, self.refill_at) if due is not None), default=None)
                        self.lock.wait(None if wake_at is None else max(0, wake_at - now))
                    continue
                self.followup_running = True
            try:
                config = read_config(self.config_path)
                result = follow_up(entry, config)
            except (ReportError, OSError, ValueError, KeyError, TypeError) as error:
                logging.warning("Повторная проверка вакансии %s: %s", entry["original"]["id"], error)
                result = {**entry, "attempted_at": time(), "error": str(error)}
            except Exception:
                logging.exception("Ошибка повторной проверки вакансии %s", entry["original"]["id"])
                result = {**entry, "attempted_at": time(), "error": "Подробности в консоли локальной программы."}
            with self.lock:
                self.followup_running = False
                try:
                    self.archive.update(result)
                except OSError as error:
                    logging.error("Не удалось сохранить архив вакансий: %s", error)
                    if not self.stopped:
                        self.lock.wait(60)

    def stop(self):
        with self.lock:
            self.stopped = True
            self.lock.notify_all()

    def start(self, mode, upload, *, autoflow=False):
        with self.lock:
            if self.active is not None or self.followup_running:
                return False
            if mode == "api":
                if autoflow:
                    self.auto_refill = True
                self.refill_at = None
            self.active = mode
            self.jobs[mode] = {
                "status": "running", "stage": "loading", "current": 0, "total": None,
                "enabled": True, "paused": False, "results": [],
                "vacancies": [], "cached": 0, "new_with_findings": 0,
            }
        Thread(target=self.run, args=(mode, upload), daemon=True).start()
        return True

    def set_enabled(self, enabled):
        with self.lock:
            if self.active is None:
                if enabled or not self.auto_refill:
                    return False
                self.auto_refill = False
                self.refill_at = None
            else:
                job = self.jobs[self.active]
                job["enabled"] = enabled
                if self.active == "api":
                    self.auto_refill = enabled
                    self.refill_at = time() if self.auto_refill else None
            self.lock.notify_all()
            return True

    def run(self, mode, upload):
        stage = "loading"
        refill = mode == "api"
        saved_ids = set()
        if mode == "api":
            try:
                with self.reports[mode].open(encoding="utf-8") as saved:
                    saved_ids = {row["id"] for row in json.load(saved)["vacancies"]}
            except (OSError, ValueError, TypeError, KeyError):
                pass

        def before_work():
            if refill:
                return
            with self.lock:
                job = self.jobs[mode]
                if not job["enabled"]:
                    job["paused"] = True
                    self.lock.wait_for(lambda: job["enabled"])
                    job["paused"] = False

        def needs_refill(rows):
            with self.lock:
                return self.jobs[mode]["enabled"] and sum(
                    bool(row["findings"]) and row["id"] not in self.archived_ids for row in rows
                ) < self.preferences["max_vacancies"]

        def progress(next_stage, current, total, vacancies=None, processing=None, result=None, cache_hit=False):
            nonlocal stage
            stage = next_stage
            with self.lock:
                job = self.jobs[mode]
                job.update(stage=stage, current=current, total=total, processing=processing)
                if result is not None:
                    if result["findings"]:
                        job["results"].append(result)
                    job["cached"] += cache_hit
                    vacancy_id = result["id"]
                    if not result["findings"]:
                        outcome = "clean"
                    elif mode == "api" and vacancy_id in self.archived_ids:
                        outcome = "archived"
                    elif mode == "api" and vacancy_id in saved_ids:
                        outcome = "known"
                    else:
                        outcome = "new"
                        job["new_with_findings"] += 1
                    for index, row in enumerate(job["vacancies"]):
                        if row["id"] == vacancy_id:
                            job["vacancies"][index] = {**row, "outcome": outcome, "cached": cache_hit}
                            break
                if vacancies is not None:
                    start = max(0, len(vacancies) - 100) if refill else 0
                    previous = {row["id"]: row for row in job["vacancies"]}
                    job["vacancies"] = [
                        previous.get(row["id"], {"id": row["id"], "title": row.get("job-name"), "index": index})
                        for index, row in enumerate(vacancies[start:], start)
                    ]

        error = None
        shortage = False
        try:
            config = read_config(self.config_path)
            vacancies = load_upload(BytesIO(upload)) if mode == "file" else None
            refill_options = {}
            if refill:
                with self.lock:
                    refill_options = {"needs_refill": needs_refill, "archived_ids": set(self.archived_ids)}
            shortage = generate_report(config, mode, self.reports[mode], vacancies, progress, before_work,
                                       **refill_options)
        except (ReportError, OSError, ValueError, KeyError, TypeError) as problem:
            if str(problem).startswith(("API «Работа России»", "api.")):
                error = str(problem)
            elif stage == "checking":
                error = f"Проверка через Ollama прервалась. {problem}"
            elif stage == "saving":
                error = f"Не удалось сохранить отчёт. {problem}"
            else:
                error = str(problem)
            logging.error("%s", error)
        except Exception:
            error = "Проверка прервалась. Подробности в консоли локальной программы."
            logging.exception("%s", error)
        finally:
            with self.lock:
                self.active = None
                job = self.jobs[mode]
                job["vacancies"] = [row for row in job["vacancies"] if "outcome" in row][-100:]
                job.update(status="error" if error else "success", error=error, processing=None,
                           enabled=False, paused=False, results=[], shortage=bool(shortage))
                if self.auto_refill and refill and (shortage or error) and self.refill_at is None:
                    self.refill_at = time() + 900
                self.lock.notify_all()


class Handler(BaseHTTPRequestHandler):
    timeout = 10

    def reply(self, status, body, content_type, modified=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; frame-ancestors 'none'; object-src 'none'")
        if modified is not None:
            self.send_header("Last-Modified", formatdate(modified, usegmt=True))
        self.end_headers()
        self.wfile.write(body)

    def json_reply(self, status, payload):
        self.reply(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def local_request(self):
        host = f"127.0.0.1:{self.server.server_port}"
        origin = self.headers.get("Origin")
        if self.headers.get("Host") == host and origin in (None, f"http://{host}"):
            return True
        self.json_reply(HTTPStatus.FORBIDDEN, {"error": "Откройте интерфейс по локальному адресу сервера."})
        return False

    def authenticated(self):
        headers = self.headers.get_all("Authorization", [])
        if len(headers) == 1 and compare_digest(headers[0].encode("utf-8"), self.server.authorization):
            return True
        self.json_reply(HTTPStatus.UNAUTHORIZED, {
            "error": "Откройте ссылку доступа из консоли, где запущен сервер.",
        })
        return False

    def do_GET(self):
        if not self.local_request():
            return
        path = urlsplit(self.path).path
        if path.startswith("/api/") and not self.authenticated():
            return
        dashboard = self.server.dashboard
        if path == "/api/state":
            self.json_reply(HTTPStatus.OK, dashboard.state())
            return
        if path == "/api/archive":
            self.json_reply(HTTPStatus.OK, dashboard.archive_state())
            return
        if path == "/api/settings":
            try:
                self.json_reply(HTTPStatus.OK, dashboard.settings())
            except (ReportError, OSError, ValueError, KeyError, TypeError):
                self.json_reply(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Не удалось прочитать настройки."})
            return
        if path in ASSETS:
            filename, content_type = ASSETS[path]
            self.reply(HTTPStatus.OK, (ROOT / "web" / filename).read_bytes(), content_type)
            return
        mode = path.removeprefix("/api/reports/") if path.startswith("/api/reports/") else None
        if mode in dashboard.reports:
            try:
                with dashboard.reports[mode].open("rb") as file:
                    modified = fstat(file.fileno()).st_mtime
                    content = file.read()
            except FileNotFoundError:
                pass
            else:
                try:
                    saved = json.loads(content)
                    if refresh_saved_findings(saved):
                        content = json.dumps(saved, ensure_ascii=False).encode("utf-8")
                except (ValueError, KeyError, TypeError, AttributeError):
                    pass
                self.reply(HTTPStatus.OK, content, "application/json; charset=utf-8", modified)
                return
        self.json_reply(HTTPStatus.NOT_FOUND, {"error": "Запрошенный отчёт или страница отсутствует."})

    def do_POST(self):
        if not self.local_request() or not self.authenticated():
            return
        if self.path in ("/api/settings", "/api/archive", "/api/complaints"):
            settings = self.path == "/api/settings"
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= (4096 if settings else MAX_RESPONSE) or self.headers.get_content_type() != "application/json":
                    raise ValueError
                values = json.loads(self.rfile.read(length))
                dashboard = self.server.dashboard
                if settings:
                    result = dashboard.save_settings(values)
                elif self.path == "/api/archive":
                    result = dashboard.mark_sent(values)
                else:
                    result = dashboard.save_complaint(values)
            except ReportError as error:
                self.json_reply(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            except (ValueError, TypeError, KeyError, RecursionError):
                self.json_reply(HTTPStatus.BAD_REQUEST, {"error": "Не удалось прочитать запрос."})
            except OSError:
                target = "настройки" if settings else "жалобу" if self.path == "/api/complaints" else "архив заявок"
                self.json_reply(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"Не удалось сохранить {target}."})
            else:
                self.json_reply(HTTPStatus.OK, result)
            return
        if self.path in ("/api/queue/pause", "/api/queue/resume"):
            changed = self.server.dashboard.set_enabled(self.path.endswith("/resume"))
            self.json_reply(HTTPStatus.OK if changed else HTTPStatus.CONFLICT, {"changed": changed})
            return
        autoflow = self.path == "/api/runs/api/auto"
        mode = "api" if autoflow else self.path.removeprefix("/api/runs/")
        if mode not in MODE_NAMES:
            self.json_reply(HTTPStatus.NOT_FOUND, {"error": "Неизвестный режим проверки."})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if length < 0 or (mode == "file" and length == 0):
            self.json_reply(HTTPStatus.BAD_REQUEST, {"error": "Выберите JSON-файл."})
            return
        if length > MAX_UPLOAD:
            self.json_reply(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "Размер JSON-файла превышает 16 МБ."})
            return
        if mode == "file" and self.headers.get_content_type() != "application/json":
            self.json_reply(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "Выберите файл в формате JSON."})
            return
        if mode == "api" and length:
            self.json_reply(HTTPStatus.BAD_REQUEST, {"error": "Для загрузки с портала файл не нужен."})
            return
        upload = self.rfile.read(length) if mode == "file" else None
        if upload is not None and len(upload) != length:
            self.json_reply(HTTPStatus.BAD_REQUEST, {"error": "Файл передан не полностью."})
            return
        if not self.server.dashboard.start(mode, upload, autoflow=autoflow):
            self.json_reply(HTTPStatus.CONFLICT, {"error": "Проверка уже выполняется. Дождитесь её окончания."})
            return
        self.json_reply(HTTPStatus.ACCEPTED, {"mode": mode})

    def log_message(self, format, *args):
        pass


def main():
    parser = argparse.ArgumentParser(description="Локальный веб-интерфейс проверки вакансий.")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        dashboard = Dashboard(args.config)
        with ThreadingHTTPServer(("127.0.0.1", args.port), Handler) as server:
            server.dashboard = dashboard
            token = token_urlsafe(32)
            server.authorization = f"Bearer {token}".encode("ascii")
            print(f"http://127.0.0.1:{server.server_port}/#access={token}", flush=True)
            Thread(target=dashboard.run_followups, daemon=True).start()
            try:
                server.serve_forever()
            finally:
                dashboard.stop()
    except KeyboardInterrupt:
        pass
    except (ReportError, OSError, ValueError, KeyError, TypeError) as error:
        logging.error("%s", error)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
