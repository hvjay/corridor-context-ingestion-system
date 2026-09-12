import json
import re
import sqlite3
import tempfile
import threading
import unittest
from functools import partial
from pathlib import Path
from http.server import ThreadingHTTPServer
from urllib.request import urlopen, Request
from urllib.error import HTTPError

from test_attribution import meeting
from corridor_context.attribution import assess_segments, resolve_target
from corridor_context.db import connect, init_db, transaction, record_meeting, start_ingestion_run, get_active_fact_definitions
from corridor_context.reviews import record_reviews, customer_reviews, customers, backfill_reviews
from corridor_context.review_server import ReviewHandler


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'reviews.sqlite3'
        self.conn = connect(self.path)
        init_db(self.conn)
        self.definitions = get_active_fact_definitions(self.conn)
        self.run = start_ingestion_run(self.conn, 'test', None, 'deterministic', None)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def add(self, value):
        with transaction(self.conn):
            record_meeting(self.conn, value, 'test', 'processed')
            record_reviews(self.conn, self.run, value, resolve_target(value), assess_segments(value), self.definitions)

    def test_candidates_are_separate_multiple_and_idempotent(self):
        value = meeting('Acme has 25 benefits-eligible employees.',
                        'At my last company, we have 900 benefits-eligible employees. Our employer budget is $850 per employee per month.')
        self.add(value)
        self.add(value)
        result = customer_reviews(self.conn, 'acme-benefits')
        self.assertEqual(len(result['reviews']), 1)
        candidates = result['reviews'][0]['candidates']
        self.assertEqual({c['fact_type']: c['value'] for c in candidates}, {'employee_count': 900, 'employer_budget_pepm': 850})
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM fact_versions').fetchone()[0], 0)
        self.assertEqual(customer_reviews(self.conn, 'apex-manufacturing')['reviews'], [])
        self.assertEqual(next(c for c in customers(self.conn) if c['key'] == 'acme-benefits')['review_count'], 1)

    def test_ordinary_headcount_wording_is_retained_for_review(self):
        self.add(meeting('At my previous employer, we had 200 employees.'))
        self.assertEqual(customer_reviews(self.conn, 'acme-benefits')['reviews'][0]['candidates'][0]['value'], 200)

    def test_multiple_values_in_same_passage(self):
        self.add(meeting('Acme has 25 benefits-eligible employees and Apex has 900 benefits-eligible employees.'))
        self.assertEqual([c['value'] for c in customer_reviews(self.conn, 'acme-benefits')['reviews'][0]['candidates']], [25, 900])

    def test_snapshot_survives_meeting_change_and_unknown_fact_is_visible(self):
        value = meeting('Acme has 25 benefits-eligible employees.', 'They mentioned an unusual arrangement.')
        self.add(value)
        with transaction(self.conn):
            changed = meeting('A different transcript')
            record_meeting(self.conn, changed, 'new', 'processed')
        row = customer_reviews(self.conn, 'acme-benefits')['reviews'][0]
        self.assertEqual(row['candidates'], [])
        self.assertEqual(row['snapshot']['context'][0]['text'], value['transcript'][0]['text'])

    def test_legacy_passage_backfills_once(self):
        value = meeting('Acme has 25 benefits-eligible employees.', 'They prefer a PPO.')
        with transaction(self.conn):
            record_meeting(self.conn, value, 'test', 'processed')
            self.conn.execute('''INSERT INTO attribution_reviews
              (ingestion_run_id, meeting_id, target_client_key, segment_index, source_excerpt, reason, policy_version)
              VALUES (?, ?, ?, ?, ?, ?, ?)''', (self.run, value['id'], 'acme-benefits', 1, value['transcript'][1]['text'], 'ambiguous', 'v1'))
            backfill_reviews(self.conn, self.definitions)
            backfill_reviews(self.conn, self.definitions)
        self.add(value)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM attribution_reviews').fetchone()[0], 1)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM review_fact_candidates').fetchone()[0], 1)
        self.assertEqual(customer_reviews(self.conn, 'acme-benefits')['reviews'][0]['candidates'][0]['value'], 'PPO')

    def test_entire_transcript_is_preserved_after_meeting_edits(self):
        value = meeting('Acme has 25 benefits-eligible employees.', 'They prefer a PPO.',
                        'A distant middle passage.', 'Another distant passage.', 'Original closing remarks.')
        self.add(value)
        with transaction(self.conn):
            changed = meeting('Acme now has 100 benefits-eligible employees.')
            record_meeting(self.conn, changed, 'new', 'processed')
        review = next(r for r in customer_reviews(self.conn, 'acme-benefits')['reviews'] if r['segment_index'] == 1)
        self.assertEqual(review['transcript']['origin'], 'captured')
        self.assertEqual(len(review['transcript']['segments']), 5)
        self.assertEqual(review['transcript']['segments'][-1]['text'], 'Original closing remarks.')
        self.assertEqual(review['transcript']['flagged_index'], 1)

    def test_legacy_transcript_is_labeled_latest_and_scoped(self):
        value = meeting('Acme has 25 benefits-eligible employees.', 'They prefer a PPO.', 'Closing remarks.')
        self.add(value)
        row = self.conn.execute('SELECT id, snapshot_json FROM attribution_reviews LIMIT 1').fetchone()
        snapshot = json.loads(row['snapshot_json'])
        snapshot.pop('transcript')
        with transaction(self.conn):
            self.conn.execute('UPDATE attribution_reviews SET snapshot_json = ? WHERE id = ?', (json.dumps(snapshot), row['id']))
        review = next(r for r in customer_reviews(self.conn, 'acme-benefits')['reviews'] if r['id'] == row['id'])
        self.assertEqual(review['transcript']['origin'], 'latest')
        self.assertEqual(len(review['transcript']['segments']), 3)
        self.assertEqual(review['transcript']['flagged_index'], 1)
        value['target_client_key'] = 'apex-manufacturing'
        with transaction(self.conn):
            record_meeting(self.conn, value, 'changed-target', 'processed')
        review = next(r for r in customer_reviews(self.conn, 'acme-benefits')['reviews'] if r['id'] == row['id'])
        self.assertEqual(review['transcript']['origin'], 'unavailable')
        self.assertEqual(review['transcript']['segments'], [])

    def test_http_filters_customers_and_does_not_allow_writes(self):
        self.add(meeting('Acme has 25 benefits-eligible employees.', 'They prefer a PPO.'))
        server = ThreadingHTTPServer(('127.0.0.1', 0), partial(ReviewHandler, db_path=self.path))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{server.server_port}'
        try:
            with urlopen(base + '/api/customers/acme-benefits') as response:
                self.assertEqual(len(json.load(response)['reviews']), 1)
            with urlopen(base + '/api/customers/apex-manufacturing') as response:
                self.assertEqual(json.load(response)['reviews'], [])
            with urlopen(base + '/') as response:
                body = response.read()
                self.assertIn(b'Context review', body)
                nonce = re.search(rb'name="csp-nonce" content="([^"]+)"', body).group(1).decode()
                self.assertNotEqual(nonce, '__CSP_NONCE__')
                self.assertIn("'nonce-" + nonce + "'", response.headers['Content-Security-Policy'])
                self.assertIn("script-src 'self'", response.headers['Content-Security-Policy'])
            for path, method, code in [('/api/customers/unknown', 'GET', 404), ('/api/customers/acme-benefits', 'POST', 403), ('/../db.py', 'GET', 404)]:
                with self.assertRaises(HTTPError) as result:
                    urlopen(Request(base + path, method=method))
                self.assertEqual(result.exception.code, code)
            with self.assertRaises(HTTPError) as result:
                urlopen(Request(base + '/api/customers', headers={'Host': 'untrusted.example'}))
            self.assertEqual(result.exception.code, 403)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
