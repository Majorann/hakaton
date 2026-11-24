import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import logging
import os
from pathlib import Path
import re
import tempfile
import tomllib
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from vacancy_analysis import (
    ANALYSIS_VERSION, ReportError, analyze, compatible_citations, schedule_placement_finding,
    source_fields, vacancy_result,
)


DEFAULT_CONFIG = Path(__file__).with_name("config.toml")
MAX_UPLOAD = 16 * 1024 * 1024
MAX_RESPONSE = 4 * 1024 * 1024
MAX_VACANCIES = 1000
NOT_FOUND = object()


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


PORTAL_HTTP = build_opener(NoRedirects())
OLLAMA_HTTP = build_opener(ProxyHandler({}), NoRedirects())


def read_json(stream, limit):
    data = stream.read(limit + 1)
    if len(data) > limit:
        raise ReportError(f"Размер JSON превышает {limit // (1024 * 1024)} МБ.")
    return json.loads(data.decode("utf-8-sig"))


def arguments():
    parser = argparse.ArgumentParser(description="Проверка понятности вакансий «Работы России».")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="Путь к конфигурации TOML.")
    parser.add_argument("--output", type=Path, help="Переопределить путь к отчёту.")
    parser.add_argument("--log-file", type=Path, help="Записать ход работы и ошибки в файл.")
    modes = parser.add_subparsers(dest="mode", required=True)
    modes.add_parser("api", help="Загрузить вакансии с портала.")
    file_mode = modes.add_parser("file", help="Прочитать локальный JSON в формате API.")
    file_mode.add_argument("input", type=Path, help="Путь к сохранённому ответу API.")
    return parser.parse_args()


def request_json(url, label, timeout, payload=None, *, missing_ok=False):
    headers = {"Accept": "application/json", "User-Agent": "TransparentVacancy/1.0"}
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    try:
        opener = OLLAMA_HTTP if urlsplit(url).hostname == "127.0.0.1" else PORTAL_HTTP
        with opener.open(Request(url, data=data, headers=headers), timeout=timeout) as response:
            return read_json(response, MAX_RESPONSE)
    except HTTPError as error:
        if missing_ok and error.code in (404, 410):
            error.close()
            return NOT_FOUND
        detail = ""
        if payload is not None:
            try:
                body = read_json(error, MAX_RESPONSE)
                if isinstance(body, dict) and isinstance(body.get("error"), str):
                    detail = f" ({body['error']})"
            except (OSError, ValueError, ReportError):
                pass
        error.close()
        raise ReportError(f"{label}: HTTP {error.code}{detail}") from error
    except (URLError, TimeoutError, OSError) as error:
        raise ReportError(f"{label}: запрос не выполнен ({error})") from error
    except (ValueError, RecursionError) as error:
        raise ReportError(f"{label}: ответ не является корректным JSON") from error
    except ReportError as error:
        raise ReportError(f"{label}: {error}") from error


def read_vacancies(data, maximum=MAX_VACANCIES):
    if not isinstance(data, dict):
        raise ReportError("входной JSON должен содержать объект ответа API")
    if "status" in data and str(data["status"]) != "200":
        raise ReportError(f"API вернул статус {data['status']} в теле ответа")
    results = data.get("results")
    if not isinstance(results, dict) or not isinstance(results.get("vacancies"), list):
        raise ReportError("во входном JSON нет массива results.vacancies")
    if len(results["vacancies"]) > maximum:
        raise ReportError(f"Во входном JSON больше {maximum} вакансий.")

    vacancies = []
    for index, entry in enumerate(results["vacancies"]):
        vacancy = entry.get("vacancy") if isinstance(entry, dict) else None
        identifier = vacancy.get("id") if isinstance(vacancy, dict) else None
        if not isinstance(identifier, str) or not identifier.strip() or len(identifier) > 256:
            raise ReportError(f"в results.vacancies[{index}] ожидается vacancy.id длиной от 1 до 256 символов")
        identifier.encode("utf-8")
        vacancies.append(vacancy)
    return vacancies


def page_url(base_url, offset, page_size):
    if not isinstance(base_url, str):
        raise ReportError("api.url должен быть HTTPS-адресом портала «Работа России»")
    parts = urlsplit(base_url)
    if (parts.scheme != "https" or parts.hostname != "opendata.trudvsem.ru"
            or parts.username is not None or parts.port not in (None, 443)):
        raise ReportError("api.url должен быть HTTPS-адресом портала «Работа России»")
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.update(offset=offset, limit=page_size)
    return urlunsplit(parts._replace(query=urlencode(query), fragment=""))


def available_count(data):
    meta = data.get("meta")
    value = meta.get("total") if isinstance(meta, dict) else None
    if type(value) is int and value >= 0:
        return value
    if isinstance(value, str) and value.isascii() and value.isdecimal():
        return int(value)
    return None


def remote_batches(settings, before_work=None, *, refill=False):
    maximum = positive_int(settings, "max_vacancies", maximum=MAX_VACANCIES)
    page_size = positive_int(settings, "page_size", maximum=100)
    if not refill:
        page_size = min(page_size, maximum)
    timeout = positive_int(settings, "timeout_seconds")
    vacancies = {}
    page = 0
    scanned = 0
    previous_ids = None
    while refill or len(vacancies) < maximum:
        if page >= (MAX_VACANCIES if refill else maximum):
            if refill:
                break
            raise ReportError("API «Работа России»: достигнут предел числа страниц за запуск.")
        if before_work:
            before_work()
        label = f"API «Работа России», страница {page + 1}"
        data = request_json(page_url(settings["url"], page, page_size), label, timeout)
        try:
            entries = read_vacancies(data, page_size)
        except ReportError as error:
            raise ReportError(f"{label}: {error}") from error

        logging.info("API «Работа России» — страница %d, получено %d", page + 1, len(entries))
        if not entries:
            break
        page_ids = {vacancy["id"] for vacancy in entries}
        if page_ids == previous_ids:
            raise ReportError(f"{label}: API повторил предыдущую страницу")
        previous_ids = page_ids
        batch = []
        for vacancy in entries:
            if vacancy["id"] not in vacancies:
                vacancies[vacancy["id"]] = vacancy
                batch.append(vacancy)
            if not refill and len(vacancies) == maximum:
                break
        if batch:
            yield batch

        scanned += len(entries)
        total = available_count(data)
        if len(entries) < page_size or (total is not None and scanned >= total):
            break
        page += 1


def load_remote(settings):
    return [vacancy for batch in remote_batches(settings) for vacancy in batch]


def load_local(path):
    with path.open("rb") as input_file:
        return load_upload(input_file)


def load_upload(stream):
    try:
        return unique_vacancies(read_json(stream, MAX_UPLOAD))
    except (ValueError, RecursionError) as error:
        raise ReportError(f"Не удалось прочитать файл JSON. {error}") from error


def unique_vacancies(data):
    vacancies = {}
    for vacancy in read_vacancies(data):
        vacancies.setdefault(vacancy["id"], vacancy)
    return list(vacancies.values())


def positive_int(section, key, maximum=None):
    value = section[key]
    if type(value) is not int or value < 1 or (maximum is not None and value > maximum):
        bound = f" от 1 до {maximum}" if maximum is not None else " больше нуля"
        raise ReportError(f"настройка {key} должна быть целым числом{bound}")
    return value


def write_atomic(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = tempfile.NamedTemporaryFile(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp",
        mode="w", encoding="utf-8", newline="\n", delete=False,
    )
    temp_path = Path(temporary.name)
    try:
        with temporary:
            temporary.write(content)
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def write_report(path, report):
    write_atomic(path, json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n")


def read_config(path):
    with path.open("rb") as config_file:
        return tomllib.load(config_file)


def report_path(config, config_path, mode):
    key = "path" if mode == "api" else "file_path"
    path = config_path.parent / config["output"][key]
    if path.resolve() == config_path.resolve():
        raise ReportError("выходной отчёт и конфигурация должны быть разными файлами")
    return path


def cache_key(vacancy, model):
    data = json.dumps([ANALYSIS_VERSION, model, source_fields(vacancy)], ensure_ascii=True, sort_keys=True)
    return sha256(data.encode("utf-8")).hexdigest()


def content_hash(vacancy):
    data = json.dumps(source_fields(vacancy), ensure_ascii=True, sort_keys=True)
    return sha256(data.encode("utf-8")).hexdigest()


def portal_company_code(row):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", row["id"]):
        return None
    code = row.get("company_code")
    if isinstance(code, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", code):
        return code
    url = vacancy_result({"id": row["id"], "vac_url": row.get("url")}, []).get("url")
    if url:
        match = re.fullmatch(r"/vacancy/card/([A-Za-z0-9_-]{1,128})/([A-Za-z0-9_-]{1,256})/?", urlsplit(url).path)
        if match and match[2] == row["id"]:
            return match[1]
    return None


def load_portal_vacancy(settings, company_code, vacancy_id):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", company_code) or not re.fullmatch(
        r"[A-Za-z0-9_-]{1,256}", vacancy_id
    ):
        raise ReportError("Некорректный идентификатор вакансии или работодателя.")
    timeout = positive_int(settings, "timeout_seconds")
    url = f"https://opendata.trudvsem.ru/api/v1/vacancies/vacancy/{company_code}/{vacancy_id}"
    data = request_json(url, "API «Работа России»", timeout, missing_ok=True)
    if data is NOT_FOUND:
        return None
    rows = read_vacancies(data, 1)
    if rows and rows[0]["id"] != vacancy_id:
        raise ReportError("Портал вернул другую вакансию.")
    if not rows and (available_count(data) or 0) > 0:
        raise ReportError("Портал вернул пустой список с ненулевым количеством вакансий.")
    return rows[0] if rows else None


def report_row(vacancy, row, mode):
    title = vacancy.get("job-name")
    if isinstance(title, str) and title.strip():
        row["title"] = title.strip()
    if mode == "api":
        company = vacancy.get("company")
        if isinstance(company, dict):
            row["company_code"] = company.get("companycode")
        code = portal_company_code(row)
        if code:
            row["company_code"] = code
        else:
            row.pop("company_code", None)
        row["content_hash"] = content_hash(vacancy)
    return row


def cached_findings(path, vacancy_id, fingerprint):
    try:
        with path.open(encoding="utf-8") as cache_file:
            entry = json.load(cache_file)
        if not isinstance(entry, dict) or not isinstance(entry.get("findings"), list):
            raise ValueError("неверный формат записи")
        if entry.get("id") == vacancy_id and entry.get("fingerprint") == fingerprint:
            return entry["findings"]
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as error:
        logging.warning("Не удалось прочитать кеш вакансии %s: %s", vacancy_id, error)
    return None


def ollama_chat(config, *, archive=False):
    ollama = config["ollama"]
    archive_model = ollama.get("archive_model", "") if archive else ""
    if not isinstance(archive_model, str):
        raise ReportError("ollama.archive_model должен быть строкой")
    model = archive_model or ollama["model"]
    if not isinstance(model, str) or not model.strip() or model.endswith((":cloud", "-cloud")):
        key = "archive_model" if archive_model else "model"
        raise ReportError(f"для ollama.{key} нужно имя локальной модели")
    timeout = positive_int(ollama, "timeout_seconds")

    def chat(payload):
        return request_json("http://127.0.0.1:11434/api/chat", f"Ollama ({model})", timeout, payload)

    return model, chat


def refresh_saved_findings(report):
    changed = False
    for row in report["vacancies"]:
        findings = row["findings"]
        kept = [finding for finding in findings if not compatible_citations(finding)]
        if len(kept) == len(findings):
            continue
        for finding in findings:
            if not compatible_citations(finding):
                continue
            qualification = next((quote["value"] for quote in finding["evidence"]
                                  if quote["field"] == "requirement.qualification"), "")
            if (placement := schedule_placement_finding(qualification)) and placement not in kept:
                kept.append(placement)
        row["findings"] = kept
        changed = True
    return changed


def generate_report(config, mode, output, local_vacancies=None, progress=None, before_work=None,
                    *, needs_refill=None, archived_ids=()):
    model, chat = ollama_chat(config)
    if progress:
        progress("loading", 0, None)
    rows = []
    skip_ids = set(archived_ids)
    saved_revision = None
    if needs_refill is not None:
        try:
            with output.open(encoding="utf-8") as previous:
                saved_revision = os.fstat(previous.fileno()).st_mtime_ns
                saved = json.load(previous)
                refresh_saved_findings(saved)
                saved_rows = saved["vacancies"]
        except FileNotFoundError:
            saved_rows = []
        rows = [row for row in saved_rows if row["id"] not in skip_ids]
        skip_ids.update(row["id"] for row in saved_rows)
        if not needs_refill(rows):
            return False
    initial_count = len(rows)
    if mode == "api" and before_work:
        vacancies = []

        def incoming():
            for batch in remote_batches(config["api"], before_work, refill=needs_refill is not None):
                if needs_refill is not None and not needs_refill(rows):
                    break
                batch = [vacancy for vacancy in batch if vacancy["id"] not in skip_ids]
                if not batch:
                    continue
                vacancies.extend(batch)
                progress("checking", checked_count, None, vacancies)
                yield from batch
                if needs_refill is not None and not needs_refill(rows):
                    break

        entries = incoming()
    else:
        vacancies = load_remote(config["api"]) if mode == "api" else local_vacancies
        entries = vacancies
        if progress:
            progress("checking", 0, len(vacancies), vacancies)

    cache_dir = output.with_name(f".{output.name}.cache") if mode == "api" else None
    checked_count = 0
    findings_count = 0
    reused_count = 0
    for index, vacancy in enumerate(entries, start=1):
        if before_work:
            before_work()
        if needs_refill is not None and not needs_refill(rows):
            break
        total = None if mode == "api" and before_work else len(vacancies)
        if progress:
            progress("checking", index - 1, total, processing=vacancy["id"])
        vacancy_id = vacancy["id"]
        if cache_dir is not None:
            filename = sha256(vacancy_id.encode("utf-8")).hexdigest() + ".json"
            entry_path = cache_dir / filename
            fingerprint = cache_key(vacancy, model)
            cached = cached_findings(entry_path, vacancy_id, fingerprint)
        else:
            cached = None
        if cached is not None:
            row = vacancy_result(vacancy, cached)
            reused_count += 1
        else:
            logging.info("Ollama (%s) — вакансия %d из %d", model, index, len(vacancies))
            row = analyze(vacancy, model, chat)
            if cache_dir is not None:
                try:
                    write_report(entry_path, {
                        "id": vacancy_id, "fingerprint": fingerprint, "findings": row["findings"],
                    })
                except OSError as error:
                    logging.warning("Не удалось сохранить кеш вакансии %s: %s", vacancy_id, error)
        report_row(vacancy, row, mode)
        if needs_refill is None or row["findings"]:
            rows.append(row)
        checked_count = index
        findings_count += len(row["findings"])
        if progress:
            progress("checking", index, total, result=row, cache_hit=cached is not None)
    if needs_refill is not None:
        if len(rows) == initial_count and (saved_revision is not None or not checked_count):
            return needs_refill(rows)
        try:
            current_revision = output.stat().st_mtime_ns
        except FileNotFoundError:
            current_revision = None
        if current_revision != saved_revision:
            raise ReportError("Рабочий отчёт обновился во время поиска. Запустите пополнение ещё раз.")
    if progress:
        progress("saving", checked_count, checked_count)
    write_report(output, {
        "model": model if rows else None,
        "source": mode,
        "completed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "vacancies": rows,
    })
    logging.info("Сохранён отчёт %s — вакансий %d, замечаний %d", output, len(rows),
                 findings_count if needs_refill is None else sum(len(row["findings"]) for row in rows))
    if cache_dir is not None:
        logging.info("Взято из кеша — %d из %d вакансий", reused_count, checked_count)
    if needs_refill is not None:
        return needs_refill(rows)


def main():
    args = arguments()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    output = None
    try:
        config = read_config(args.config)
        output = args.output or report_path(config, args.config, args.mode)
        output_path = output.resolve()
        if output_path == args.config.resolve():
            raise ReportError("выходной отчёт и конфигурация должны быть разными файлами")
        if args.mode == "file" and output_path == args.input.resolve():
            raise ReportError("выходной отчёт и входной JSON должны быть разными файлами")

        if args.log_file:
            log_path = args.log_file.resolve()
            protected = {output_path, args.config.resolve()}
            if args.mode == "file":
                protected.add(args.input.resolve())
            if log_path in protected:
                raise ReportError("журнал должен быть отдельным файлом")
            args.log_file.parent.mkdir(parents=True, exist_ok=True)
            handler = logging.FileHandler(args.log_file, encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            logging.getLogger().addHandler(handler)

        vacancies = load_local(args.input) if args.mode == "file" else None
        generate_report(config, args.mode, output, vacancies)
        return 0
    except (ReportError, OSError, ValueError, KeyError, TypeError) as error:
        logging.error("%s", error)
        if output is not None and output.is_file():
            logging.info("Файл отчёта остался на месте: %s", output)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
