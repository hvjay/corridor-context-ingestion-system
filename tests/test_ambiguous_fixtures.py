import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_attribution import ROOT
from corridor_context.attribution import assess_segments
from corridor_context.db import connect
from corridor_context.ingest import ingest
from corridor_context.context import get_client_context
from corridor_context.reviews import customer_reviews

MEETINGS = json.loads((ROOT / 'fixtures/ambiguous_transcripts.json').read_text())['meetings']
EXPECTED = json.loads((ROOT / 'fixtures/ambiguous_expectations.json').read_text())


class AmbiguousFixtureTests(unittest.TestCase):
    def test_expected_passages_are_held(self):
        for meeting in MEETINGS:
            with self.subTest(meeting=meeting['id']):
                self.assertEqual([d['segment_index'] for d in assess_segments(meeting) if d['status'] == 'review'],
                                 EXPECTED[meeting['id']]['review_segments'])

    def test_full_ingestion_is_safe_and_replay_is_idempotent(self):
        by_id = {m['id']: m for m in MEETINGS}
        with tempfile.TemporaryDirectory() as tmp, patch('corridor_context.ingest.transcript_api.list_meetings', return_value=MEETINGS), patch('corridor_context.ingest.transcript_api.get_meeting', side_effect=lambda _, key: by_id[key]):
            path = Path(tmp) / 'scenario.sqlite3'
            result = ingest('http://fixture', path, extractor='deterministic', attribution='rules')
            self.assertEqual(result['failed'], 0)
            self.assertEqual(result['processed'], 9)
            self.assertEqual(result['review_segments'], 17)
            self.assertEqual(ingest('http://fixture', path, extractor='deterministic', attribution='rules')['skipped'], 9)
            acme = get_client_context(path, 'Acme')
            self.assertEqual(acme['facts']['employee_count']['value'], 25)
            self.assertEqual(acme['facts']['preferred_plan_type']['value'], 'HMO')
            self.assertNotIn('employer_budget_pepm', acme['facts'])
            apex = get_client_context(path, 'Apex')
            self.assertEqual(apex['facts']['employee_count']['value'], 31)
            self.assertEqual(apex['facts']['benefit_cycle_start_date']['value'], '2027-01-01')
            north = get_client_context(path, 'Northstar')
            self.assertEqual(north['facts']['employer_budget_pepm']['value'], 640)
            self.assertEqual(north['facts']['preferred_plan_type']['value'], 'PPO')
            conn = connect(path)
            try:
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM attribution_reviews').fetchone()[0], 17)
                kinds = {r[0] for r in conn.execute('SELECT DISTINCT fact_type FROM review_fact_candidates')}
                self.assertEqual(kinds, {'employee_count', 'preferred_plan_type', 'employer_budget_pepm', 'benefit_cycle_start_date', 'incumbent_plan_pricing'})
                reviews = customer_reviews(conn, 'apex-manufacturing')['reviews']
                self.assertTrue(any(not r['candidates'] for r in reviews))
                self.assertTrue(all('acme' not in r['meeting_id'] for r in reviews))
            finally:
                conn.close()
