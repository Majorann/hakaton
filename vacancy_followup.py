import json
from time import time

from vacancy_analysis import analyze
from vacancy_report import (
    ReportError, content_hash, load_portal_vacancy, ollama_chat, portal_company_code,
    report_row, write_report,
)


FOLLOWUP_MINUTES = (1, 60, 1440, 4320)
PENDING_STATUSES = {"waiting", "partial"}


def next_check(entry, minutes):
    if entry["status"] not in PENDING_STATUSES:
        return None
    delay = minutes * 60
    if entry.get("error"):
        delay = min(delay, 900)
    return (entry.get("attempted_at") or entry["sent_at"]) + delay


def follow_up(entry, config):
    vacancy = load_portal_vacancy(config["api"], entry["company_code"], entry["original"]["id"])
    now = time()
    if vacancy is None:
        return {
            **entry, "status": "unavailable", "checked_at": now, "attempted_at": now,
            "error": None, "unchanged": False,
            "history": [*entry["history"], {"at": now, "status": "unavailable"}],
        }

    previous = entry["latest"] or entry["original"]
    if content_hash(vacancy) == previous.get("content_hash"):
        return {**entry, "checked_at": now, "attempted_at": now, "error": None, "unchanged": True}

    model, chat = ollama_chat(config, archive=True)
    result = report_row(vacancy, analyze(vacancy, model, chat, strict=True), "api")
    now = time()
    original_topics = {finding["topic"] for finding in entry["original"]["findings"]}
    current_topics = {finding["topic"] for finding in result["findings"]}
    resolved = sorted(original_topics - current_topics)
    status = "fixed" if len(resolved) == len(original_topics) else "partial" if resolved else "waiting"
    event = {"at": now, "status": status, "findings": result["findings"], "resolved": resolved, "model": model}
    return {
        **entry, "status": status, "latest": result, "resolved": resolved,
        "checked_at": now, "attempted_at": now, "error": None, "unchanged": False,
        "history": [*entry["history"], event],
    }


class VacancyArchive:
    """The dashboard holds its lock across archive reads and updates."""

    def __init__(self, path):
        self.path = path
        try:
            self.entries = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            self.entries = []
        if not isinstance(self.entries, list):
            raise ReportError("Некорректный формат архива вакансий.")
        self.revision = 0

    def write(self, entries):
        write_report(self.path, entries)
        self.entries = entries
        self.revision += 1

    def mark_sent(self, row, track):
        code = portal_company_code(row)
        if track and not code:
            raise ReportError("В отчёте нет кода работодателя для повторной проверки этой вакансии.")
        now = time()
        entry = {
            "key": len(self.entries) + 1,
            "status": "waiting" if track else "archived",
            "sent_at": now, "company_code": code, "original": row, "latest": None,
            "resolved": [], "history": [],
        }
        self.write([*self.entries, entry])
        return entry

    def update(self, entry):
        entries = list(self.entries)
        entries[entry["key"] - 1] = entry
        self.write(entries)
