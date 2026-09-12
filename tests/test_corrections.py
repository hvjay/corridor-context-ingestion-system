import json
import tempfile
import threading
import unittest
from pathlib import Path
from functools import partial
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from unittest.mock import patch

from test_attribution import meeting
from corridor_context.attribution import assess_segments, resolve_target
from corridor_context.db import connect, init_db, transaction, record_meeting, start_ingestion_run, get_active_fact_definitions, apply_fact
from corridor_context.facts import extract_facts
from corridor_context.context import get_client_context
from corridor_context.reviews import record_reviews, customer_reviews, customers
from corridor_context.corrections import create_proposal, apply_proposal, ignore_review, ConflictError
from corridor_context.review_server import ReviewHandler


def plan(instruction='Set employee count to 25.', operation='set', value='25'):
    return {'action':'update','summary':'Correct employee count.','question':'', 'changes':[
        {'fact_type':'employee_count','operation':operation,'value_json':value,'display_value':'25 employees','instruction_excerpt':instruction}]}


class CorrectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)/'context.sqlite3'
        self.conn=connect(self.path);init_db(self.conn)
        self.definitions=get_active_fact_definitions(self.conn)
        value=meeting('Acme has 20 benefits-eligible employees.', 'They have 200 benefits-eligible employees.')
        run=start_ingestion_run(self.conn,'test',None,'deterministic',None)
        with transaction(self.conn):
            record_meeting(self.conn,value,'first','processed')
            for fact in extract_facts(value,self.definitions):apply_fact(self.conn,value['id'],fact)
            record_reviews(self.conn,run,value,resolve_target(value),assess_segments(value),self.definitions)
        self.review_id=self.conn.execute('SELECT id FROM attribution_reviews LIMIT 1').fetchone()[0]

    def tearDown(self):
        self.conn.close();self.tmp.cleanup()

    def proposal(self,response=None,instruction='Set employee count to 25.',request='request-1'):
        with patch('corridor_context.corrections._request_plan',return_value=response or plan(instruction)):
            return create_proposal(self.conn,'acme-benefits',self.review_id,instruction,'Sam',request)

    def test_preview_then_apply_preserves_history_and_provenance(self):
        proposal=self.proposal()
        self.assertEqual(get_client_context(self.path,'Acme')['facts']['employee_count']['value'],20)
        result=apply_proposal(self.conn,'acme-benefits',self.review_id,proposal['id'])
        self.assertEqual(result['action'],'updated')
        context=get_client_context(self.path,'Acme',True)
        self.assertEqual(context['facts']['employee_count']['value'],25)
        self.assertEqual(context['facts']['employee_count']['provenance']['sourceType'],'human_correction')
        self.assertEqual(context['facts']['employee_count']['provenance']['reviewResolutionId'],result['id'])
        self.assertEqual([r['value'] for r in context['history']['employee_count']],[20,25])
        self.assertEqual(apply_proposal(self.conn,'acme-benefits',self.review_id,proposal['id'])['id'],result['id'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM fact_versions').fetchone()[0],2)
        self.assertEqual(next(c for c in customers(self.conn) if c['key']=='acme-benefits')['review_count'],0)
        self.assertEqual(customer_reviews(self.conn,'acme-benefits')['reviews'][0]['resolution']['reviewer'],'Sam')

    def test_ignore_never_changes_facts_or_calls_agent(self):
        before=get_client_context(self.path,'Acme')
        with patch('corridor_context.corrections._request_plan',side_effect=AssertionError('No model needed')):
            result=ignore_review(self.conn,'acme-benefits',self.review_id,'Wrong employer','Sam','ignore-1')
            self.assertEqual(ignore_review(self.conn,'acme-benefits',self.review_id,'Wrong employer','Sam','ignore-1')['id'],result['id'])
        self.assertEqual(get_client_context(self.path,'Acme'),before)
        self.assertEqual(customer_reviews(self.conn,'acme-benefits')['reviews'][0]['status'],'ignored')

    def test_removal_keeps_history_and_advances_context_version(self):
        before=get_client_context(self.path,'Acme')['contextVersion']
        proposal=self.proposal(plan('Remove employee count.','remove','null'),'Remove employee count.')
        apply_proposal(self.conn,'acme-benefits',self.review_id,proposal['id'])
        after=get_client_context(self.path,'Acme',True)
        self.assertNotIn('employee_count',after['facts'])
        self.assertGreater(after['contextVersion'],before)
        self.assertEqual(after['history']['employee_count'][0]['value'],20)

    def test_stale_preview_cannot_overwrite_newer_fact(self):
        proposal=self.proposal()
        value=meeting('Acme has 30 benefits-eligible employees.')
        value['id']='new-meeting'
        with transaction(self.conn):
            record_meeting(self.conn,value,'new','processed')
            apply_fact(self.conn,value['id'],extract_facts(value,self.definitions)[0])
        with self.assertRaises(ConflictError):apply_proposal(self.conn,'acme-benefits',self.review_id,proposal['id'])
        self.assertEqual(get_client_context(self.path,'Acme')['facts']['employee_count']['value'],30)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM review_resolutions').fetchone()[0],0)

    def test_cross_customer_and_reused_requests_are_rejected(self):
        proposal=self.proposal()
        for action in [lambda:apply_proposal(self.conn,'apex-manufacturing',self.review_id,proposal['id']),
                       lambda:ignore_review(self.conn,'apex-manufacturing',self.review_id,'Ignore','Sam','ignore')]:
            with self.assertRaises(KeyError):action()
        with self.assertRaises(ConflictError):self.proposal(instruction='Set employee count to 50.')
        self.assertEqual(self.proposal()['id'],proposal['id'])

    def test_invalid_agent_values_and_quotes_fail_without_writing(self):
        for bad in [plan(value='-5'), plan(value='"twenty five"'), plan('Invented instruction')]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):self.proposal(bad)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM correction_proposals').fetchone()[0],0)
        self.assertEqual(get_client_context(self.path,'Acme')['facts']['employee_count']['value'],20)

    def test_clarify_and_ignore_agent_proposals(self):
        proposal=self.proposal({'action':'clarify','summary':'A value is missing.','question':'What is the employee count?','changes':[]})
        with self.assertRaises(ValueError):apply_proposal(self.conn,'acme-benefits',self.review_id,proposal['id'])
        proposal=self.proposal({'action':'ignore','summary':'Ignore this item.','question':'','changes':[]},request='ignore-plan')
        apply_proposal(self.conn,'acme-benefits',self.review_id,proposal['id'])
        self.assertEqual(get_client_context(self.path,'Acme')['facts']['employee_count']['value'],20)

    def test_failed_apply_rolls_back_resolution_and_source(self):
        proposal=self.proposal()
        with patch('corridor_context.corrections._write_validated_fact',side_effect=RuntimeError('write failed')):
            with self.assertRaises(RuntimeError):apply_proposal(self.conn,'acme-benefits',self.review_id,proposal['id'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM review_resolutions').fetchone()[0],0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM meetings WHERE id LIKE 'human-correction-%'").fetchone()[0],0)
        self.assertEqual(get_client_context(self.path,'Acme')['facts']['employee_count']['value'],20)

    def test_api_requires_session_token_and_same_origin(self):
        server=ThreadingHTTPServer(('127.0.0.1',0),partial(ReviewHandler,db_path=self.path))
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        base=f'http://127.0.0.1:{server.server_port}'
        route=f'/api/customers/acme-benefits/reviews/{self.review_id}/ignore'
        try:
            with urlopen(base+'/api/customers') as response:token=json.load(response)['csrfToken']
            payload=json.dumps({'instruction':'Wrong employer','reviewer':'Sam','requestId':'api-ignore'}).encode()
            for headers in [{'Content-Type':'application/json'}, {'Content-Type':'application/json','X-CSRF-Token':token,'Origin':'http://evil.example'}]:
                with self.assertRaises(HTTPError) as error:urlopen(Request(base+route,data=payload,headers=headers))
                self.assertEqual(error.exception.code,403)
            with urlopen(Request(base+route,data=payload,headers={'Content-Type':'application/json','X-CSRF-Token':token,'Origin':base})) as response:
                self.assertEqual(json.load(response)['action'],'ignored')
            self.assertEqual(get_client_context(self.path,'Acme')['facts']['employee_count']['value'],20)
        finally:server.shutdown();server.server_close();thread.join()
