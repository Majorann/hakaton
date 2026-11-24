import argparse
import json
import logging
import os
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

from vacancy_analysis import InvalidModelResponse, analyze
from vacancy_fields import ReportError, vacancy_result
from vacancy_sources import (
    MAX_RESPONSE, MAX_UPLOAD, MAX_VACANCIES, load_local, load_portal_vacancy, load_remote,
    load_upload, ollama_chat, portal_company_code, remote_batches, request_json,
)
from vacancy_storage import (
    DEFAULT_CONFIG, cache_key, cached_findings, content_hash, read_config,
    refresh_saved_findings, report_path, report_row, write_atomic, write_report,
)


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


def generate_report(config, mode, output, local_vacancies=None, progress=None, before_work=None,
                    *, needs_refill=None, archived_ids=()):
    model, chat = ollama_chat(config)
    if progress:
        progress("loading", 0, None)
    rows = []
    skip_ids = set(archived_ids)
    saved_revision = None
    editor_reviews = {}
    if needs_refill is None:
        try:
            previous = json.loads(output.read_text(encoding="utf-8"))
            editor_reviews = {row["id"]: row for row in previous["vacancies"] if row.get("editor_review")}
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            pass
    else:
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
    errors = []
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
            try:
                row = analyze(vacancy, model, chat)
            except InvalidModelResponse as problem:
                failure = {"id": vacancy_id, "title": vacancy.get("job-name"), "error": str(problem)}
                errors.append(failure)
                checked_count = index
                logging.error("Вакансия %s не проверена: %s", vacancy_id, problem)
                if progress:
                    progress("checking", index, total, failure=failure)
                continue
            if cache_dir is not None:
                try:
                    write_report(entry_path, {
                        "id": vacancy_id, "fingerprint": fingerprint, "findings": row["findings"],
                    })
                except OSError as error:
                    logging.warning("Не удалось сохранить кеш вакансии %s: %s", vacancy_id, error)
        report_row(vacancy, row, mode)
        previous_row = editor_reviews.get(vacancy_id)
        if previous_row and previous_row.get("source_fields") == row["source_fields"]:
            review = previous_row["editor_review"]
            if review["status"] == "problem":
                row["editor_review"] = review
                if not row["findings"]:
                    row["findings"] = previous_row["findings"]
            elif not row["findings"]:
                row["editor_review"] = review
        rows.append(row)
        checked_count = index
        findings_count += len(row["findings"])
        if progress:
            progress("checking", index, total, result=row, cache_hit=cached is not None)
    if needs_refill is not None:
        if not errors and len(rows) == initial_count and (saved_revision is not None or not checked_count):
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
        **({"errors": errors} if errors else {}),
    })
    logging.info("Сохранён отчёт %s — вакансий %d, замечаний %d, ошибок проверки %d", output, len(rows),
                 findings_count if needs_refill is None else sum(len(row["findings"]) for row in rows), len(errors))
    if cache_dir is not None:
        logging.info("Взято из кеша — %d из %d вакансий", reused_count, checked_count)
    if needs_refill is not None:
        return needs_refill(rows)
    return len(errors)


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
        errors = generate_report(config, args.mode, output, vacancies)
        return 2 if errors else 0
    except (ReportError, OSError, ValueError, KeyError, TypeError) as error:
        logging.error("%s", error)
        if output is not None and output.is_file():
            logging.info("Файл отчёта остался на месте: %s", output)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
