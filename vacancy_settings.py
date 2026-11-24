import json
import re

from vacancy_fields import ReportError
from vacancy_followup import FOLLOWUP_MINUTES
from vacancy_sources import request_json


REMINDER_HOURS = (0, 12, 24, 72)


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
