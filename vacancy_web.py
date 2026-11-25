import argparse
import json
import logging
from email.utils import formatdate
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from os import fstat
from pathlib import Path
from secrets import compare_digest, token_urlsafe
from threading import Thread
from urllib.parse import urlsplit

from vacancy_dashboard import Dashboard, MODE_NAMES, ROOT
from vacancy_fields import ReportError
from vacancy_settings import update_toml
from vacancy_sources import MAX_RESPONSE, MAX_UPLOAD
from vacancy_storage import DEFAULT_CONFIG, refresh_saved_findings


ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/mark.svg": ("mark.svg", "image/svg+xml"),
    "/modules/api.js": ("modules/api.js", "text/javascript; charset=utf-8"),
    "/modules/detail.js": ("modules/detail.js", "text/javascript; charset=utf-8"),
    "/modules/display.js": ("modules/display.js", "text/javascript; charset=utf-8"),
    "/modules/queue-view.js": ("modules/queue-view.js", "text/javascript; charset=utf-8"),
    "/modules/report-data.js": ("modules/report-data.js", "text/javascript; charset=utf-8"),
    "/modules/report-view.js": ("modules/report-view.js", "text/javascript; charset=utf-8"),
    "/modules/settings-dialog.js": ("modules/settings-dialog.js", "text/javascript; charset=utf-8"),
    "/modules/source-picker.js": ("modules/source-picker.js", "text/javascript; charset=utf-8"),
    "/modules/vacancy-actions.js": ("modules/vacancy-actions.js", "text/javascript; charset=utf-8"),
}


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
        if self.path in ("/api/settings", "/api/archive", "/api/complaints", "/api/editor-review"):
            settings = self.path == "/api/settings"
            try:
                length = int(self.headers.get("Content-Length", "0"))
                limit = 4096 if settings else 16384 if self.path == "/api/editor-review" else MAX_RESPONSE
                if not 0 < length <= limit or self.headers.get_content_type() != "application/json":
                    raise ValueError
                values = json.loads(self.rfile.read(length))
                dashboard = self.server.dashboard
                if settings:
                    result = dashboard.save_settings(values)
                elif self.path == "/api/archive":
                    result = dashboard.mark_sent(values)
                elif self.path == "/api/editor-review":
                    result = dashboard.review_vacancy(values)
                else:
                    result = dashboard.save_complaint(values)
            except ReportError as error:
                self.json_reply(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            except (ValueError, TypeError, KeyError, RecursionError):
                self.json_reply(HTTPStatus.BAD_REQUEST, {"error": "Не удалось прочитать запрос."})
            except OSError:
                target = {
                    "/api/settings": "настройки", "/api/complaints": "жалобу",
                    "/api/editor-review": "решение редактора", "/api/archive": "архив заявок",
                }[self.path]
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
            Thread(target=dashboard.run_scheduler, daemon=True).start()
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
