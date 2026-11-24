import json
import logging
import os
import tempfile
import tomllib
from hashlib import sha256
from pathlib import Path

from vacancy_analysis import ANALYSIS_VERSION
from vacancy_fields import ReportError, source_fields
from vacancy_rules import compatible_citations, experience_finding
from vacancy_sources import portal_company_code


DEFAULT_CONFIG = Path(__file__).with_name("config.toml")


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


def has_review_issues(row):
    return bool(row["findings"]) or (row.get("editor_review") or {}).get("status") == "problem"


def report_revision(info):
    return f"{info.st_mtime_ns}:{info.st_size}"


def report_row(vacancy, row, mode):
    row["source_fields"] = source_fields(vacancy)
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


def refresh_saved_findings(report):
    changed = False
    for row in report["vacancies"]:
        findings = row["findings"]
        fields = row.get("source_fields")
        kept = []
        for finding in findings:
            if compatible_citations(finding):
                continue
            if (fields and finding.get("recommendation_source") == "rule" and finding["topic"] == "requirements"
                  and any(quote["field"] == "requirement.experience" for quote in finding["evidence"])):
                if revised := experience_finding(fields):
                    kept.append({**revised, "recommendation_source": "rule"})
            else:
                updated = {
                    key: finding[key].replace("Требуемая квалификация", "Дополнительные сведения по вакансии")
                    for key in ("problem", "problem_highlight", "recommendation") if key in finding
                }
                kept.append({**finding, **updated})
        if kept != findings:
            row["findings"] = kept
            changed = True
    return changed
