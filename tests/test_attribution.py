from __future__ import annotations

import copy
import json
import os
from dataclasses import replace
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from corridor_context.attribution import assess_segments, resolve_target
from corridor_context.facts import extract_facts, load_fact_definitions
from corridor_context.db import connect, init_db, transaction, record_meeting, apply_fact
from corridor_context.ingest import ingest
from corridor_context.context import get_client_context
from corridor_context.llm_extractor import extract_facts_with_llm


def meeting(*texts):
    return {
        "id": "mixed", "created_at": "2026-09-12T10:00:00Z", "updated_at": "2026-09-12T10:00:00Z",
        "target_client_key": "acme-benefits",
        "owner": {"email": "broker@corridor.example"},
        "attendees": [{"email": "person@acme.example"}],
        "transcript": [{"text": text, "speaker": {"email": "person@acme.example"},
                        "start_time": f"2026-09-12T10:0{i}:00Z", "end_time": f"2026-09-12T10:0{i}:30Z"}
                       for i, text in enumerate(texts)],
    }


class AttributionTests(unittest.TestCase):
    def facts(self, value):
        return extract_facts(value, load_fact_definitions())

    def test_mixed_employer_does_not_overwrite_target(self):
        value = meeting("Acme Benefits has 25 benefits-eligible employees.",
                        "Apex Manufacturing has 900 benefits-eligible employees.")
        self.assertEqual([f.normalized_value for f in self.facts(value)], [25])
        self.assertEqual(resolve_target(value)["key"], "acme-benefits")

    def test_former_employer_and_following_pronouns_are_held(self):
        value = meeting("Acme Benefits has 25 benefits-eligible employees.",
                        "At my last company, Beta, we have 900 benefits-eligible employees.",
                        "We prefer a PPO.", "For Acme, we prefer an HMO.")
        self.assertEqual([f.normalized_value for f in self.facts(value)], [25, "HMO"])
        self.assertEqual([d["status"] for d in assess_segments(value)], ["accepted", "review", "review", "accepted"])

    def test_same_segment_comparison_is_held_in_full(self):
        value = meeting("Acme has 25 benefits-eligible employees and Apex has 900 benefits-eligible employees.")
        self.assertEqual(self.facts(value), [])

    def test_unknown_employer_subject_is_held(self):
        value = meeting("Acme has 25 benefits-eligible employees.", "Beta has 900 benefits-eligible employees.")
        self.assertEqual([f.normalized_value for f in self.facts(value)], [25])

    def test_ambiguous_and_subsidiary_references_are_held(self):
        for text in ["They have 900 benefits-eligible employees.",
                     "Our subsidiary has 900 benefits-eligible employees.",
                     "At Beta, we have 900 benefits-eligible employees.",
                     "Beta's employer budget is $900 per employee per month."]:
            with self.subTest(text=text):
                value = meeting("Acme has 25 benefits-eligible employees.", text)
                self.assertEqual([f.normalized_value for f in self.facts(value)], [25])

    def test_alias_and_target_speaker_continuation(self):
        value = meeting("For Acme, we have 25 benefits-eligible employees.", "We prefer a PPO.")
        self.assertEqual([f.normalized_value for f in self.facts(value)], [25, "PPO"])
        self.assertTrue(all(f.client_name == "Acme Benefits" for f in self.facts(value)))

    def test_target_resolution_is_not_first_transcript_mention(self):
        value = meeting("Apex has 900 benefits-eligible employees.", "Acme has 25 benefits-eligible employees.")
        del value["target_client_key"]
        self.assertEqual([f.normalized_value for f in self.facts(value)], [25])
        value["attendees"].append({"email": "person@apex.example"})
        with self.assertRaises(ValueError):
            self.facts(value)
        value["target_client_key"] = "unknown"
        with self.assertRaises(ValueError):
            self.facts(value)

    def test_unknown_speaker_cannot_inherit_topic(self):
        value = meeting("Acme has 25 benefits-eligible employees.", "We prefer a PPO.")
        value["transcript"][1]["speaker"]["email"] = "person@unknown.example"
        self.assertEqual([f.normalized_value for f in self.facts(value)], [25])

    def test_llm_extractor_does_not_fall_back_when_analysis_fails(self):
        value = meeting("Acme has 25 benefits-eligible employees.")
        with patch("corridor_context.llm_extractor.analyze_attribution", side_effect=ValueError("Invalid attribution")):
            with self.assertRaisesRegex(ValueError, "Invalid attribution"):
                extract_facts_with_llm(value, load_fact_definitions())

    def test_persistence_rejects_cross_client_write_before_mutation(self):
        value = meeting("Acme has 25 benefits-eligible employees.", "Apex has 900 benefits-eligible employees.")
        fact = self.facts(value)[0]
        with tempfile.TemporaryDirectory() as tmp:
            conn = connect(Path(tmp) / "context.db")
            init_db(conn)
            with transaction(conn):
                record_meeting(conn, value, "hash", "processed")
            with self.assertRaises(ValueError), transaction(conn):
                apply_fact(conn, value["id"], fact)
                apply_fact(conn, value["id"], replace(fact, client_name="Apex Manufacturing"))
            self.assertEqual(conn.execute("SELECT count(*) FROM fact_versions").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT count(*) FROM clients").fetchone()[0], 0)
            foreign = value["transcript"][1]
            with self.assertRaises(ValueError), transaction(conn):
                apply_fact(conn, value["id"], replace(fact, source=replace(fact.source, excerpt=foreign["text"], start_time=foreign["start_time"], end_time=foreign["end_time"])))
            conn.close()

    def test_reviews_persist_and_other_employer_is_not_created(self):
        value = meeting("Acme has 25 benefits-eligible employees.", "Apex has 900 benefits-eligible employees.")
        with tempfile.TemporaryDirectory() as tmp, patch("corridor_context.ingest.transcript_api.list_meetings", return_value=[value]), patch("corridor_context.ingest.transcript_api.get_meeting", return_value=value):
            db = Path(tmp) / "context.db"
            result = ingest("http://test", db, extractor="deterministic", attribution="rules")
            self.assertEqual(result["facts"], 1)
            self.assertEqual(result["review_segments"], 1)
            self.assertEqual(ingest("http://test", db, extractor="deterministic", attribution="rules")["skipped"], 1)
            conn = connect(db)
            self.assertEqual(conn.execute("SELECT count(*) FROM attribution_reviews").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT count(*) FROM clients").fetchone()[0], 1)
            conn.close()
            self.assertEqual(get_client_context(db, "Acme")["facts"]["employee_count"]["value"], 25)
            with self.assertRaises(KeyError):
                get_client_context(db, "Apex")


if __name__ == "__main__":
    unittest.main()
