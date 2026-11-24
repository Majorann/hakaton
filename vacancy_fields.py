import re
from urllib.parse import urlsplit


MAX_FIELD_CHARS = 8000
MAX_VACANCY_CHARS = 16000
MAX_FIELD_ITEMS = 128
PORTAL_CARD_HOSTS = {"trudvsem.ru", "www.trudvsem.ru"}

TOPICS = {
    "title": "job-name",
    "duties": "duty",
    "requirements": "requirements",
    "pay": "salary",
    "schedule": "schedule",
    "location": "addresses.address",
    "employment": "employment",
}

ANALYSIS_FIELDS = (
    "job-name", "duty", "description", "job-description", "requirements",
    "qualification", "skills", "salary", "salary_min", "salary_max", "currency",
    "schedule", "scheduleTypeComment", "shift", "employment",
)
NESTED_FIELDS = {
    "requirement": ("education", "qualification", "experience"),
    "term": ("text",),
}
FIELD_LABELS = {
    "job-name": "Должность", "duty": "Обязанности", "description": "Описание вакансии",
    "job-description": "Описание работы", "requirements": "Требования", "qualification": "Квалификация",
    "skills": "Навыки", "salary": "Зарплата", "salary_min": "Минимальная зарплата",
    "salary_max": "Максимальная зарплата", "currency": "Валюта", "schedule": "График работы",
    "scheduleTypeComment": "Описание графика", "shift": "Смена", "employment": "Тип занятости",
    "requirement.education": "Образование", "requirement.qualification": "Дополнительные сведения по вакансии",
    "requirement.experience": "Опыт работы", "term.text": "Условия работы",
    "addresses.address": "Адрес работы", "addresses.address.location": "Адрес работы",
}
FIELD_INDEX = re.compile(r"\[\d+\]")
FIELD_REFERENCE = re.compile(
    r"(?<![\w.-])`?([A-Za-z][A-Za-z0-9_-]*(?:\[\d+\])?"
    r"(?:\.[A-Za-z][A-Za-z0-9_-]*(?:\[\d+\])?)*)`?(?![\w-])"
)


class ReportError(Exception):
    """Ошибка, которую можно показать при запуске программы."""


def source_fields(vacancy):
    fields = {}
    total_chars = 0

    def add(path, value):
        nonlocal total_chars
        if isinstance(value, list):
            if len(value) > MAX_FIELD_ITEMS:
                raise ReportError(f"В вакансии слишком много значений в поле {path}.")
            for index, item in enumerate(value):
                if isinstance(item, (str, int, float)):
                    add(f"{path}[{index}]", item)
            return
        if type(value) in (int, float):
            value = str(value)
        if isinstance(value, str):
            total_chars += len(value)
            if len(value) > MAX_FIELD_CHARS or total_chars > MAX_VACANCY_CHARS:
                raise ReportError(f"Текст вакансии превышает допустимый объём (поле {path}).")
            if value.strip():
                value.encode("utf-8")
                fields[path] = value

    for field in ANALYSIS_FIELDS:
        add(field, vacancy.get(field))
    for parent, children in NESTED_FIELDS.items():
        obj = vacancy.get(parent)
        if isinstance(obj, dict):
            for child in children:
                add(f"{parent}.{child}", obj.get(child))
    addresses = vacancy.get("addresses")
    if isinstance(addresses, dict) and isinstance(addresses.get("address"), list):
        if len(addresses["address"]) > MAX_FIELD_ITEMS:
            raise ReportError("В вакансии слишком много адресов.")
        for index, address in enumerate(addresses["address"]):
            if isinstance(address, dict):
                add(f"addresses.address[{index}].location", address.get("location"))
    return fields


def vacancy_result(vacancy, findings):
    row = {"id": vacancy["id"], "findings": findings}
    url = vacancy.get("vac_url")
    if isinstance(url, str) and len(url) <= 2048:
        # Некорректный URL не должен мешать анализу вакансии.
        try:
            parts = urlsplit(url)
            if (parts.scheme == "https" and parts.hostname in PORTAL_CARD_HOSTS
                    and parts.username is None and parts.port in (None, 443)):
                row["url"] = url
        except ValueError:
            pass
    return row
