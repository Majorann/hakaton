import json
import logging
import tomllib
from datetime import datetime, timezone
from io import BytesIO
from os import fstat
from pathlib import Path
from secrets import token_urlsafe
from threading import Condition, Thread
from time import time

from vacancy_analysis import analyze_fields
from vacancy_fields import ReportError
from vacancy_followup import (
    FOLLOWUP_MINUTES, PENDING_STATUSES, VacancyArchive, follow_up, next_check,
)
from vacancy_report import generate_report
from vacancy_settings import REMINDER_HOURS, editable_settings, installed_models, update_toml
from vacancy_sources import MAX_VACANCIES, load_local, load_upload, ollama_chat
from vacancy_storage import (
    has_review_issues, read_config, refresh_saved_findings, report_path, report_revision, write_atomic, write_report,
)


ROOT = Path(__file__).resolve().parent
MODE_NAMES = ("api", "file")


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
        self.running_archive_model = None  # Empty string uses the main agent. None means idle.
        self.followup_retry_at = 0
        self.editor_check = False
        self.auto_refill = False
        self.refill_at = None
        self.stopped = False

    def followup_blocks_main(self):
        return self.running_archive_model is not None and not (
            self.running_archive_model and self.preferences["archive_model"]
        )

    def main_busy(self):
        return self.active is not None or self.editor_check or self.followup_blocks_main()

    def state(self):
        revisions = {}
        portal_updated = None
        for mode, path in self.reports.items():
            try:
                info = path.stat()
                revisions[mode] = report_revision(info)
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
                "followup_running": self.running_archive_model is not None,
                "followup_blocks_main": self.followup_blocks_main(),
                "editor_check": self.editor_check,
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

    def review_vacancy(self, values):
        if not isinstance(values, dict):
            raise ReportError("Не удалось прочитать решение редактора.")
        mode, vacancy_id, decision = (values.get(key) for key in ("source", "id", "decision"))
        comment = values.get("comment", "")
        if (mode not in MODE_NAMES or not isinstance(vacancy_id, str) or not vacancy_id
                or decision not in ("confirm", "problem") or not isinstance(comment, str) or len(comment) > 2000):
            raise ReportError("Не удалось прочитать решение редактора.")
        path = self.reports[mode]
        with self.lock:
            if self.main_busy():
                raise ReportError("Дождитесь окончания текущей проверки.")
            with path.open(encoding="utf-8") as report:
                revision = report_revision(fstat(report.fileno()))
                saved = json.load(report)
            if revision != values.get("revision"):
                raise ReportError("Отчёт обновился. Откройте актуальную карточку и повторите действие.")
            refresh_saved_findings(saved)
            row = next((item for item in saved["vacancies"] if item["id"] == vacancy_id), None)
            if row is None or row["findings"] or (mode == "api" and vacancy_id in self.archived_ids):
                raise ReportError("Карточка недоступна для этой отметки.")
            fields = row.get("source_fields")
            previous = row.get("editor_review") or {}
            if decision == "confirm":
                row["editor_review"] = {"status": "confirmed"}
                write_report(path, saved)
                return row
            if not fields:
                raise ReportError("В отчёте нет исходных полей вакансии. Сначала обновите отчёт.")
            if previous.get("status") == "problem" and not previous.get("error"):
                raise ReportError("Карточка уже отмечена как проблемная.")
            self.editor_check = True
        comment = comment.strip()
        review = {"status": "problem", "comment": comment}
        try:
            try:
                model, chat = ollama_chat(read_config(self.config_path))
                review["model"] = model
                row["findings"] = analyze_fields(vacancy_id, fields, model, chat, strict=True,
                                                 editor_comment=comment)
            except (ReportError, OSError) as problem:
                logging.warning("Проверка по отметке редактора, вакансия %s: %s", vacancy_id, problem)
                review["error"] = str(problem)
            with self.lock:
                if report_revision(path.stat()) != revision:
                    raise ReportError("Отчёт обновился. Откройте актуальную карточку и повторите действие.")
                row["editor_review"] = review
                write_report(path, saved)
                return row
        finally:
            with self.lock:
                self.editor_check = False
                self.lock.notify_all()

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

    def run_scheduler(self):
        while True:
            with self.lock:
                if self.stopped:
                    return
                now = time()
                wake_at = self.schedule_followup(now)
                if self.refill_at is not None and not self.main_busy():
                    if self.refill_at <= now:
                        self.start("api", None)
                        continue
                    wake_at = self.refill_at if wake_at is None else min(wake_at, self.refill_at)
                self.lock.wait(None if wake_at is None else max(0, wake_at - time()))

    def schedule_followup(self, now):
        archive_model = self.preferences["archive_model"]
        if (not self.preferences["followup_enabled"] or self.running_archive_model is not None
                or (not archive_model and self.main_busy())):
            return None
        minutes = self.preferences["followup_minutes"]
        pending = (entry for entry in self.archive.entries if entry["status"] in PENDING_STATUSES)
        entry = min(pending, key=lambda entry: next_check(entry, minutes), default=None)
        if entry is None:
            return None
        due = max(next_check(entry, minutes), self.followup_retry_at)
        if due > now:
            return due
        self.running_archive_model = archive_model
        try:
            Thread(target=self.run_followup, args=(entry, archive_model), daemon=True).start()
        except RuntimeError:
            logging.exception("Не удалось запустить повторную проверку вакансии %s", entry["original"]["id"])
            self.running_archive_model = None
            self.followup_retry_at = now + 60
            return self.followup_retry_at
        return None

    def run_followup(self, entry, archive_model):
        try:
            config = read_config(self.config_path)
            config["ollama"]["archive_model"] = archive_model
            result = follow_up(entry, config)
        except (ReportError, OSError, ValueError, KeyError, TypeError) as error:
            logging.warning("Повторная проверка вакансии %s: %s", entry["original"]["id"], error)
            result = {**entry, "attempted_at": time(), "error": str(error)}
        except Exception:
            logging.exception("Ошибка повторной проверки вакансии %s", entry["original"]["id"])
            result = {**entry, "attempted_at": time(), "error": "Подробности в консоли локальной программы."}
        with self.lock:
            try:
                self.archive.update(result)
            except OSError as error:
                logging.error("Не удалось сохранить архив вакансий: %s", error)
                self.followup_retry_at = time() + 60
            finally:
                self.running_archive_model = None
                self.lock.notify_all()

    def stop(self):
        with self.lock:
            self.stopped = True
            self.lock.notify_all()

    def start(self, mode, upload, *, autoflow=False):
        with self.lock:
            if self.main_busy():
                return False
            if mode == "api":
                if autoflow:
                    self.auto_refill = True
                self.refill_at = None
            self.active = mode
            self.jobs[mode] = {
                "status": "running", "stage": "loading", "current": 0, "total": None,
                "enabled": True, "paused": False, "results": [],
                "vacancies": [], "cached": 0, "new_with_findings": 0, "failed": 0,
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
                    has_review_issues(row) and row["id"] not in self.archived_ids for row in rows
                ) < self.preferences["max_vacancies"]

        def progress(next_stage, current, total, vacancies=None, processing=None, result=None, cache_hit=False,
                     failure=None):
            nonlocal stage
            stage = next_stage
            with self.lock:
                job = self.jobs[mode]
                job.update(stage=stage, current=current, total=total, processing=processing)
                if failure is not None:
                    job["failed"] += 1
                    for row in job["vacancies"]:
                        if row["id"] == failure["id"]:
                            row.update(outcome="failed", error=failure["error"])
                            break
                if result is not None:
                    if has_review_issues(result):
                        job["results"].append(result)
                    job["cached"] += cache_hit
                    vacancy_id = result["id"]
                    if not has_review_issues(result):
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
            result = generate_report(config, mode, self.reports[mode], vacancies, progress, before_work,
                                     **refill_options)
            shortage = refill and result
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
