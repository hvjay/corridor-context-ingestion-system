from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from corridor_context.context import get_client_context
from corridor_context.db import connect, get_active_fact_definitions, init_db, record_meeting, transaction, apply_fact
from corridor_context.facts import extract_facts
from corridor_context import ingest as ingest_module
from corridor_context.ingest import ingest, stable_hash

FIXTURE = json.loads((ROOT / "fixtures" / "dummy_data.txt").read_text())


class IngestionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = ROOT / "data" / "test_client_context.sqlite3"
        if self.db.exists():
            self.db.unlink()
        wal = Path(str(self.db) + "-wal")
        shm = Path(str(self.db) + "-shm")
        if wal.exists():
            wal.unlink()
        if shm.exists():
            shm.unlink()
        self.conn = connect(self.db)
        init_db(self.conn)

    def tearDown(self) -> None:
        self.conn.close()

    def ingest_fixture(self) -> None:
        definitions = get_active_fact_definitions(self.conn)
        meetings = sorted(FIXTURE["meetings"], key=lambda item: (item["updated_at"], item["created_at"], item["id"]))
        for meeting in meetings:
            with transaction(self.conn):
                record_meeting(self.conn, meeting, stable_hash(meeting), "processed")
                for fact in extract_facts(meeting, definitions):
                    apply_fact(self.conn, meeting["id"], fact)

    def test_final_current_values_and_history(self) -> None:
        self.ingest_fixture()
        acme = get_client_context(self.db, "Acme Benefits", include_history=True)
        self.assertEqual(acme["facts"]["employee_count"]["value"], 26)
        self.assertEqual(acme["facts"]["employer_budget_pepm"]["value"], 725)
        self.assertEqual(acme["facts"]["preferred_plan_type"]["value"], "PPO")
        self.assertGreaterEqual(len(acme["history"]["employee_count"]), 3)

        apex = get_client_context(self.db, "Apex Manufacturing")
        self.assertEqual(apex["facts"]["employee_count"]["value"], 31)
        self.assertEqual(apex["facts"]["preferred_plan_type"]["value"], "PPO")
        self.assertEqual(apex["facts"]["employer_budget_pepm"]["value"], 650)

        north = get_client_context(self.db, "Northstar Logistics")
        self.assertEqual(north["facts"]["employee_count"]["value"], 24)
        self.assertEqual(north["facts"]["preferred_plan_type"]["value"], "PPO")
        self.assertEqual(north["facts"]["employer_budget_pepm"]["value"], 640)

    def test_one_current_fact_per_client_type(self) -> None:
        self.ingest_fixture()
        rows = self.conn.execute(
            """
            SELECT client_id, fact_type, count(*) as count
            FROM fact_versions
            WHERE is_current = 1
            GROUP BY client_id, fact_type
            HAVING count(*) > 1
            """
        ).fetchall()
        self.assertEqual(rows, [])

    def test_fact_config_can_disable_fact_type(self) -> None:
        config = json.loads((ROOT / "config" / "facts.json").read_text())
        for item in config:
            if item["fact_type"] == "incumbent_plan_pricing":
                item["active"] = False

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "facts_without_pricing.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")

            self.conn.close()
            self.db.unlink()
            self.conn = connect(self.db)
            init_db(self.conn, config_path)
            self.ingest_fixture()

        acme = get_client_context(self.db, "Acme Benefits")
        self.assertNotIn("incumbent_plan_pricing", acme["facts"])
        self.assertIn("employee_count", acme["facts"])

    def test_fact_definition_history_tracks_active_flag_changes(self) -> None:
        config = json.loads((ROOT / "config" / "facts.json").read_text())
        for item in config:
            if item["fact_type"] == "incumbent_plan_pricing":
                item["active"] = False

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "facts_without_pricing.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            init_db(self.conn, config_path)

        rows = self.conn.execute(
            """
            SELECT id, active, supersedes_version_id
            FROM fact_definition_versions
            WHERE fact_type = 'incumbent_plan_pricing'
            ORDER BY id
            """
        ).fetchall()
        self.assertEqual([row["active"] for row in rows], [1, 0])
        self.assertIsNotNone(rows[-1]["supersedes_version_id"])

        current = self.conn.execute(
            "SELECT active, current_version_id FROM fact_definitions WHERE fact_type = 'incumbent_plan_pricing'"
        ).fetchone()
        self.assertEqual(current["active"], 0)
        self.assertEqual(current["current_version_id"], rows[-1]["id"])

    def test_fact_versions_link_to_definition_version(self) -> None:
        self.ingest_fixture()
        row = self.conn.execute(
            """
            SELECT fv.fact_definition_version_id, fdv.config_hash
            FROM fact_versions fv
            JOIN fact_definition_versions fdv ON fdv.id = fv.fact_definition_version_id
            WHERE fv.fact_type = 'employee_count'
            LIMIT 1
            """
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertIsNotNone(row["fact_definition_version_id"])
        self.assertIsNotNone(row["config_hash"])

        acme = get_client_context(self.db, "Acme Benefits")
        definition = acme["facts"]["employee_count"]["provenance"]["factDefinition"]
        self.assertIsNotNone(definition["versionId"])
        self.assertIsNotNone(definition["configHash"])

    def test_ingest_records_run_and_attempts(self) -> None:
        meeting = FIXTURE["meetings"][0]
        original_list = ingest_module.transcript_api.list_meetings
        original_get = ingest_module.transcript_api.get_meeting
        try:
            ingest_module.transcript_api.list_meetings = lambda base_url, updated_after=None: [
                {
                    "id": meeting["id"],
                    "created_at": meeting["created_at"],
                    "updated_at": meeting["updated_at"],
                    "owner": meeting["owner"],
                }
            ]
            ingest_module.transcript_api.get_meeting = lambda base_url, meeting_id: meeting

            first = ingest("http://stub.test", self.db, extractor="deterministic")
            second = ingest("http://stub.test", self.db, extractor="deterministic")
        finally:
            ingest_module.transcript_api.list_meetings = original_list
            ingest_module.transcript_api.get_meeting = original_get

        self.assertEqual(first["processed"], 1)
        self.assertEqual(second["skipped"], 1)

        runs = self.conn.execute("SELECT status, meetings_seen, meetings_processed, meetings_skipped FROM ingestion_runs ORDER BY id").fetchall()
        self.assertEqual([row["status"] for row in runs], ["succeeded", "succeeded"])
        self.assertEqual(runs[0]["meetings_processed"], 1)
        self.assertEqual(runs[1]["meetings_skipped"], 1)

        attempts = self.conn.execute("SELECT status FROM meeting_processing_attempts ORDER BY id").fetchall()
        self.assertEqual([row["status"] for row in attempts], ["processed", "skipped"])

    def test_verbose_ingest_prints_per_meeting_progress(self) -> None:
        meeting = FIXTURE["meetings"][0]
        original_list = ingest_module.transcript_api.list_meetings
        original_get = ingest_module.transcript_api.get_meeting
        try:
            ingest_module.transcript_api.list_meetings = lambda base_url, updated_after=None: [
                {
                    "id": meeting["id"],
                    "created_at": meeting["created_at"],
                    "updated_at": meeting["updated_at"],
                    "owner": meeting["owner"],
                }
            ]
            ingest_module.transcript_api.get_meeting = lambda base_url, meeting_id: meeting

            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                result = ingest("http://stub.test", self.db, extractor="deterministic", verbose=True)
        finally:
            ingest_module.transcript_api.list_meetings = original_list
            ingest_module.transcript_api.get_meeting = original_get

        self.assertEqual(result["processed"], 1)
        output = stderr.getvalue()
        self.assertIn("started ingestion run", output)
        self.assertIn(f"processing {meeting['id']}...", output)
        self.assertIn(f"processed {meeting['id']}:", output)
        self.assertIn("finished ingestion run", output)

    def test_ingest_records_failed_attempt_without_context_update(self) -> None:
        good_meeting = FIXTURE["meetings"][0]
        bad_meeting = dict(good_meeting)
        bad_meeting["id"] = "mtg_bad_failure_case"
        original_list = ingest_module.transcript_api.list_meetings
        original_get = ingest_module.transcript_api.get_meeting
        original_extract = ingest_module.extract_meeting_facts
        try:
            ingest_module.transcript_api.list_meetings = lambda base_url, updated_after=None: [
                {
                    "id": bad_meeting["id"],
                    "created_at": bad_meeting["created_at"],
                    "updated_at": bad_meeting["updated_at"],
                    "owner": bad_meeting["owner"],
                }
            ]
            ingest_module.transcript_api.get_meeting = lambda base_url, meeting_id: bad_meeting
            ingest_module.extract_meeting_facts = lambda meeting, definitions, extractor: (_ for _ in ()).throw(RuntimeError("boom"))

            result = ingest("http://stub.test", self.db, extractor="deterministic")
        finally:
            ingest_module.transcript_api.list_meetings = original_list
            ingest_module.transcript_api.get_meeting = original_get
            ingest_module.extract_meeting_facts = original_extract

        self.assertEqual(result["failed"], 1)
        run = self.conn.execute("SELECT status, meetings_failed FROM ingestion_runs ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(run["status"], "partial")
        self.assertEqual(run["meetings_failed"], 1)
        attempt = self.conn.execute("SELECT status, error FROM meeting_processing_attempts ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(attempt["status"], "failed")
        self.assertIn("boom", attempt["error"])
        current_facts = self.conn.execute("SELECT count(*) AS count FROM fact_versions WHERE is_current = 1").fetchone()
        self.assertEqual(current_facts["count"], 0)

    def test_unknown_client_error(self) -> None:
        self.ingest_fixture()
        with self.assertRaises(KeyError):
            get_client_context(self.db, "Missing Client")


if __name__ == "__main__":
    unittest.main()
