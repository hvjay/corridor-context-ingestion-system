import copy
import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from test_attribution import meeting
from corridor_context.llm_attribution import validate_analysis, analyze_attribution, facts_from_analysis
from corridor_context.facts import load_fact_definitions
from corridor_context.db import connect, apply_fact, transaction
from corridor_context.ingest import ingest
from corridor_context.context import get_client_context
from corridor_context.reviews import customer_reviews


def response_for(value):
    return {'target_client_key':'acme-benefits','segments':[
        {'segment_index':0,'status':'accepted','subject_client_key':'acme-benefits','reason':'Speaker explicitly identifies Acme.', 'evidence_segment_indexes':[0], 'candidates':[]},
        {'segment_index':1,'status':'accepted','subject_client_key':'acme-benefits','reason':'They refers to Acme in the immediately preceding statement.', 'evidence_segment_indexes':[0,1], 'candidates':[
            {'fact_type':'employee_count','value_json':'25','display_value':'25 employees','source_excerpt':'They have 25 benefits-eligible employees.','subject_client_key':'acme-benefits'}]},
        {'segment_index':2,'status':'review','subject_client_key':None,'reason':'Previous employer is not identified as Acme.', 'evidence_segment_indexes':[2], 'candidates':[
            {'fact_type':'employer_budget_pepm','value_json':'850','display_value':'$850 PEPM','source_excerpt':value['transcript'][2]['text'],'subject_client_key':None}]}
    ]}


class LLMAttributionTests(unittest.TestCase):
    def setUp(self):
        self.meeting=meeting('The client is Acme Benefits.', 'They have 25 benefits-eligible employees.',
                             'At my previous employer, our budget was $850 per employee per month.')
        self.result=response_for(self.meeting)
        self.definitions=load_fact_definitions()

    def test_clear_pronoun_can_be_accepted_without_regex_veto(self):
        decisions=validate_analysis(self.meeting,self.definitions,self.result)
        self.assertEqual(facts_from_analysis(self.meeting,decisions)[0].normalized_value,25)
        self.assertEqual(decisions[2]['candidates'][0]['value'],850)

    def test_invalid_responses_fail_closed(self):
        mutations=[
            lambda r:r.update(target_client_key='apex-manufacturing'),
            lambda r:r['segments'].pop(),
            lambda r:r['segments'][1].update(segment_index=0),
            lambda r:r['segments'][1].update(subject_client_key='apex-manufacturing'),
            lambda r:r['segments'][1].update(evidence_segment_indexes=[100]),
            lambda r:r['segments'][1].update(reason=''),
            lambda r:r['segments'][1]['candidates'][0].update(source_excerpt='Invented quotation'),
            lambda r:r['segments'][1]['candidates'][0].update(subject_client_key='apex-manufacturing'),
            lambda r:r['segments'][1]['candidates'][0].update(value_json='"twenty-five"'),
            lambda r:r['segments'][1]['candidates'][0].update(value_json='-1'),
            lambda r:r['segments'][1]['candidates'][0].update(fact_type='not-configured'),
        ]
        for index,mutate in enumerate(mutations):
            with self.subTest(case=index):
                response=copy.deepcopy(self.result);mutate(response)
                with self.assertRaises((ValueError,TypeError)):
                    validate_analysis(self.meeting,self.definitions,response)

    def test_no_api_key_requires_explicit_offline_mode(self):
        with patch.dict(os.environ,{},clear=True), patch('corridor_context.llm_attribution.load_local_env'):
            with self.assertRaisesRegex(RuntimeError,'OPENAI_API_KEY'):
                analyze_attribution(self.meeting,self.definitions)

    def test_ingestion_calls_llm_once_and_persists_evidence_and_candidates(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ,{'OPENAI_API_KEY':'test'}), patch('corridor_context.llm_attribution._request_analysis',return_value=self.result) as model, patch('corridor_context.ingest.transcript_api.list_meetings',return_value=[self.meeting]), patch('corridor_context.ingest.transcript_api.get_meeting',return_value=self.meeting):
            path=Path(tmp)/'context.sqlite3'
            first=ingest('http://test',path)
            self.assertEqual(first['failed'],0)
            self.assertEqual(first['facts'],1)
            self.assertEqual(ingest('http://test',path)['skipped'],1)
            self.assertEqual(model.call_count,1)
            self.assertEqual(get_client_context(path,'Acme')['facts']['employee_count']['value'],25)
            conn=connect(path)
            try:
                decisions=json.loads(conn.execute('SELECT attribution_json FROM meetings').fetchone()[0])
                self.assertEqual(decisions[1]['evidence_segment_indexes'],[0,1])
                reviews=customer_reviews(conn,'acme-benefits')['reviews']
                self.assertEqual(reviews[0]['snapshot']['attribution']['engine'],'llm')
                self.assertEqual(reviews[0]['candidates'][0]['value'],850)
                self.assertNotIn('employer_budget_pepm',get_client_context(path,'Acme')['facts'])
                fact=facts_from_analysis(self.meeting,decisions)[0]
                with self.assertRaises(ValueError), transaction(conn):
                    apply_fact(conn,self.meeting['id'],replace(fact,normalized_value=999))
                self.assertEqual(model.call_count,1)
            finally:conn.close()
            with patch('corridor_context.llm_attribution._request_analysis',side_effect=RuntimeError('API unavailable')):
                self.meeting['updated_at']='2026-09-13T10:00:00Z'
                self.assertEqual(ingest('http://test',path)['failed'],1)
            self.assertEqual(get_client_context(path,'Acme')['facts']['employee_count']['value'],25)
