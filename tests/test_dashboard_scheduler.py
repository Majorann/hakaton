import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread
from time import time
from unittest.mock import patch

from vacancy_dashboard import Dashboard
from vacancy_settings import update_toml
from vacancy_storage import DEFAULT_CONFIG


TIMEOUT = 5
MAIN_MODEL = "main:test"
ARCHIVE_MODEL = "archive:test"
VACANCIES = {
    lane: {"id": lane, "job-name": lane, "duty": "Проверять документы"}
    for lane in ("main", "archive")
}


def portal_response(lane):
    return {"results": {"vacancies": [{"vacancy": VACANCIES[lane]}]}}


class DashboardSchedulerTest(unittest.TestCase):
    def setUp(self):
        folder = self.enterContext(TemporaryDirectory())
        config_path = Path(folder) / "config.toml"
        config = DEFAULT_CONFIG.read_text(encoding="utf-8")
        config = update_toml(config, "ollama", "model", MAIN_MODEL)
        config = update_toml(config, "ollama", "archive_model", ARCHIVE_MODEL)
        config = update_toml(config, "web", "followup_enabled", True)
        config_path.write_text(config, encoding="utf-8")
        self.dashboard = Dashboard(config_path)
        entry = self.dashboard.archive.mark_sent({
            "id": "archive", "company_code": "company", "findings": [{"topic": "duties"}],
        }, track=True)
        entry["sent_at"] = time() - 2 * 86400
        self.started = {lane: Event() for lane in VACANCIES}
        self.release = {lane: Event() for lane in VACANCIES}
        self.models = {}
        self.enterContext(patch("vacancy_sources.request_json", side_effect=self.request_json))
        self.enterContext(patch("vacancy_settings.request_json", side_effect=self.request_json))
        self.addCleanup(self.stop_jobs)
        self.scheduler = Thread(target=self.dashboard.run_scheduler, daemon=True)

    def request_json(self, url, label, timeout, payload=None, *, missing_ok=False):
        if url.endswith("/api/tags"):
            return {"models": [{"name": model} for model in (MAIN_MODEL, ARCHIVE_MODEL)]}
        if not url.endswith("/api/chat"):
            return portal_response("archive" if "/vacancy/" in url else "main")
        lane = json.loads(payload["messages"][1]["content"])["vacancy"]["job-name"]
        self.models[lane] = payload["model"]
        self.started[lane].set()
        if not self.release[lane].wait(TIMEOUT):
            raise TimeoutError(f"Test did not release {lane}")
        return {"message": {"content": json.dumps({"missing": [], "ambiguous": [], "contradiction": []})}}

    def stop_jobs(self):
        self.dashboard.stop()
        for event in self.release.values():
            event.set()
        if self.scheduler.ident is not None:
            self.scheduler.join(TIMEOUT)
            self.assertFalse(self.scheduler.is_alive())
        self.wait_for_idle("active")
        self.wait_for_idle("followup_running")

    def wait_for_idle(self, field):
        with self.dashboard.lock:
            self.assertTrue(self.dashboard.lock.wait_for(lambda: not self.dashboard.state()[field], TIMEOUT), field)

    def assert_chat_started(self, lane, model):
        self.assertTrue(self.started[lane].wait(TIMEOUT), lane)
        self.assertEqual(self.models[lane], model)

    def start_auto_refill(self):
        self.dashboard.auto_refill = True
        self.dashboard.refill_at = time()
        self.scheduler.start()

    def select_archive_model(self, model):
        self.dashboard.save_settings({**self.dashboard.preferences, "archive_model": model})

    def test_due_refill_and_archive_run_together(self):
        self.start_auto_refill()
        self.assert_chat_started("archive", ARCHIVE_MODEL)
        self.assert_chat_started("main", MAIN_MODEL)
        self.assertEqual(self.dashboard.state()["active"], "api")
        self.assertFalse(self.dashboard.state()["followup_blocks_main"])

        self.release["main"].set()
        self.wait_for_idle("active")
        self.assertEqual(self.dashboard.state()["jobs"]["api"]["status"], "success")
        self.assertTrue(self.dashboard.state()["followup_running"])

        self.release["archive"].set()
        self.wait_for_idle("followup_running")
        archive = json.loads(self.dashboard.archive.path.read_text(encoding="utf-8"))
        self.assertEqual(archive[0]["history"][-1]["model"], ARCHIVE_MODEL)

    def test_archive_without_second_agent_uses_main_model_in_turn(self):
        self.select_archive_model("")
        self.start_auto_refill()
        self.assert_chat_started("archive", MAIN_MODEL)
        self.assertTrue(self.dashboard.state()["followup_blocks_main"])
        self.assertIsNone(self.dashboard.state()["active"])
        self.assertFalse(self.dashboard.start("api", None))

        self.release["archive"].set()
        self.assert_chat_started("main", MAIN_MODEL)
        self.assertFalse(self.dashboard.state()["followup_running"])

    def test_archive_can_join_file_check_with_same_model(self):
        self.select_archive_model(MAIN_MODEL)
        upload = json.dumps(portal_response("main")).encode()
        self.assertTrue(self.dashboard.start("file", upload))
        self.assert_chat_started("main", MAIN_MODEL)
        self.scheduler.start()
        self.assert_chat_started("archive", MAIN_MODEL)

        self.select_archive_model("")
        self.release["main"].set()
        self.wait_for_idle("active")
        self.assertTrue(self.dashboard.state()["followup_blocks_main"])
        self.assertFalse(self.dashboard.start("file", upload))
