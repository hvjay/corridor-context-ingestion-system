import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from test_attribution import meeting
from corridor_context.attribution import resolve_target
from corridor_context.context import get_client_context
from corridor_context.db import connect, init_db, transaction, record_meeting, _write_validated_fact
from corridor_context.types import ExtractedFact, SourceSpan
from corridor_context.timeline import reconcile
from corridor_context.ingest import ingest
from corridor_context.llm_attribution import validate_analysis, facts_from_analysis
from corridor_context.facts import load_fact_definitions
from corridor_context.mcp_server import handle


class TimelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)/'timeline.sqlite3'
        self.conn=connect(self.path);init_db(self.conn)

    def tearDown(self):
        self.conn.close();self.tmp.cleanup()

    def add(self, identity, count, stated, start=None, end=None, updated='2026-09-12T10:00:00Z', revision=None, created=None):
        text=f'Acme has {count} benefits-eligible employees.'
        if start:text+=f' Effective from {start}'+(f' until {end}' if end else '')+'.'
        value=meeting(text);value.update(id=identity,updated_at=updated)
        if created:
            value['created_at'] = created
        value['transcript'][0]['start_time']=stated
        fact=ExtractedFact('Acme Benefits','employee_count',count,f'{count} employees',SourceSpan(text,stated,stated),.9,'Test evidence','update',stated,'test','1',start,end,text if start else None)
        with transaction(self.conn):
            record_meeting(self.conn,value,revision or identity,'processed')
            return _write_validated_fact(self.conn,identity,fact,resolve_target(value))

    def context(self, at='2026-09-12T20:00:00Z'):
        return get_client_context(self.path,'Acme',True,at)

    def test_old_upload_preserves_newer_knowledge_and_separate_dates(self):
        self.add('june',30,'2026-06-01T10:00:00Z')
        self.add('may',25,'2026-05-01T10:00:00Z',updated='2026-09-12T12:00:00Z')
        context=self.context()
        self.assertEqual(context['facts']['employee_count']['value'],30)
        old=context['history']['employee_count'][0]
        self.assertFalse(old['isCurrent'])
        self.assertEqual(old['statementAt'],'2026-05-01T10:00:00Z')
        self.assertEqual(old['sourceUpdatedAt'],'2026-09-12T12:00:00Z')
        self.assertNotEqual(old['learnedAt'],old['statementAt'])
        self.assertEqual(self.context('2026-05-15')['facts']['employee_count']['value'],25)

    def test_reversed_upload_order_has_same_values(self):
        self.add('may',25,'2026-05-01T10:00:00Z')
        self.add('june',30,'2026-06-01T10:00:00Z')
        self.assertEqual(self.context()['facts']['employee_count']['value'],30)

    def test_future_period_activates_and_expires_without_reingestion(self):
        self.add('baseline',25,'2026-05-01T10:00:00Z')
        self.add('future',40,'2026-06-01T10:00:00Z','2027-01-01','2027-02-01')
        before=self.context('2026-12-31')
        during=self.context('2027-01-01')
        after=self.context('2027-02-01')
        self.assertEqual(before['facts']['employee_count']['value'],25)
        self.assertEqual(during['facts']['employee_count']['value'],40)
        self.assertEqual(after['facts']['employee_count']['value'],25)
        self.assertNotEqual(before['selectionVersion'],during['selectionVersion'])
        self.assertEqual(before['contextVersion'],during['contextVersion'])

    def test_retroactive_correction_uses_effective_time_then_statement_time(self):
        self.add('may',25,'2026-05-01T10:00:00Z','2026-05-01')
        self.add('june',30,'2026-06-01T10:00:00Z','2026-06-01')
        self.add('correction',26,'2026-07-01T10:00:00Z','2026-05-01','2026-06-01')
        self.assertEqual(self.context('2026-05-20')['facts']['employee_count']['value'],26)
        self.assertEqual(self.context()['facts']['employee_count']['value'],30)

    def test_equal_time_disagreement_is_a_conflict_not_last_upload_wins(self):
        self.add('a',25,'2026-05-01T10:00:00Z')
        self.add('b',30,'2026-05-01T10:00:00Z')
        result=self.context()
        self.assertNotIn('employee_count',result['facts'])
        self.assertEqual({r['value'] for r in result['conflicts']['employee_count']},{25,30})

    def test_source_created_at_breaks_temporal_ties_in_both_upload_orders(self):
        for newest_first in (False, True):
            with self.subTest(newest_first=newest_first):
                self.conn.execute('DELETE FROM fact_versions')
                self.conn.commit()
                rows = [('earlier', 25, '2026-05-01T11:00:00Z'),
                        ('later', 30, '2026-05-01T13:00:00+01:00')]
                if newest_first:
                    rows.reverse()
                for identity, count, created in rows:
                    self.add(identity, count, '2026-05-01T10:00:00Z', created=created)
                result = self.context()
                self.assertEqual(result['facts']['employee_count']['value'], 30)
                self.assertNotIn('employee_count', result['conflicts'])

    def test_source_created_at_does_not_override_newer_statement(self):
        self.add('june', 30, '2026-06-01T10:00:00Z', created='2026-06-01T11:00:00Z')
        self.add('may', 25, '2026-05-01T10:00:00Z', created='2026-09-01T11:00:00Z')
        self.assertEqual(self.context()['facts']['employee_count']['value'], 30)

    def test_timezone_offsets_compare_as_instants(self):
        self.add('later',30,'2026-05-01T09:00:00-07:00')
        self.add('earlier',25,'2026-05-01T15:00:00Z')
        self.assertEqual(self.context()['facts']['employee_count']['value'],30)

    def test_revised_old_source_changes_history_not_newer_current(self):
        self.add('may',25,'2026-05-01T10:00:00Z',revision='v1')
        self.add('june',30,'2026-06-01T10:00:00Z')
        self.add('may',26,'2026-05-01T10:00:00Z',revision='v2')
        self.assertEqual(self.context()['facts']['employee_count']['value'],30)
        self.assertEqual(self.context('2026-05-15')['facts']['employee_count']['value'],26)
        self.assertEqual(len(self.context()['history']['employee_count']),3)

    def test_retraction_blocks_late_old_evidence_but_not_a_later_statement(self):
        self.add('may',25,'2026-05-01T10:00:00Z')
        client=self.conn.execute('SELECT id FROM clients').fetchone()[0]
        with transaction(self.conn):
            self.conn.execute('INSERT INTO fact_retractions(client_id,fact_type,retracted_at) VALUES (?,?,?)',(client,'employee_count','2026-06-01T00:00:00Z'))
            reconcile(self.conn,client)
        self.add('old',26,'2026-05-15T10:00:00Z')
        self.assertNotIn('employee_count',self.context()['facts'])
        self.add('new',30,'2026-07-01T10:00:00Z')
        self.assertEqual(self.context()['facts']['employee_count']['value'],30)

    def test_missing_statement_time_rejected_instead_of_using_upload(self):
        with self.assertRaises(ValueError):self.add('unknown',25,None)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM fact_versions').fetchone()[0],0)

    def test_invalid_period_rejected_atomically(self):
        with self.assertRaises(ValueError):self.add('bad',25,'2026-05-01','2026-06-01','2026-05-01')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM fact_versions').fetchone()[0],0)

    def test_mcp_supports_asof_and_validates_input(self):
        self.add('may',25,'2026-05-01T10:00:00Z')
        self.add('june',30,'2026-06-01T10:00:00Z')
        request={'id':1,'method':'tools/call','params':{'name':'get_client_context','arguments':{'client':'Acme','asOf':'2026-05-20'}}}
        result=handle(request,self.path)
        self.assertEqual(json.loads(result['result']['content'][0]['text'])['facts']['employee_count']['value'],25)
        request['params']['arguments']['asOf']='not-a-date'
        self.assertEqual(handle(request,self.path)['error']['code'],-32602)

    def test_llm_effective_period_roundtrip_and_evidence_validation(self):
        value=meeting('Acme will have 40 benefits-eligible employees starting January 1, 2027.')
        item={'fact_type':'employee_count','value_json':'40','display_value':'40 employees','source_excerpt':value['transcript'][0]['text'],'subject_client_key':'acme-benefits','effective_from':'2027-01-01','effective_to':None,'temporal_evidence':'starting January 1, 2027'}
        response={'target_client_key':'acme-benefits','segments':[{'segment_index':0,'status':'accepted','subject_client_key':'acme-benefits','reason':'Explicit employer and date.','evidence_segment_indexes':[0],'candidates':[item]}]}
        facts=facts_from_analysis(value,validate_analysis(value,load_fact_definitions(),response))
        self.assertEqual(facts[0].effective_from,'2027-01-01')
        item['temporal_evidence']='Invented date evidence'
        with self.assertRaises(ValueError):validate_analysis(value,load_fact_definitions(),response)

    def ingest_meeting(self, identity, text, stated, updated):
        value=meeting(text);value.update(id=identity,updated_at=updated)
        value['transcript'][0]['start_time']=stated
        with patch('corridor_context.ingest.transcript_api.list_meetings',return_value=[value]),patch('corridor_context.ingest.transcript_api.get_meeting',return_value=value):
            return ingest('http://synthetic-api',self.path,extractor='deterministic',attribution='rules')

    def test_ingestion_old_modified_meeting_does_not_win_by_upload_order(self):
        self.assertEqual(self.ingest_meeting('june','Acme has 30 benefits-eligible employees.','2026-06-01T10:00:00Z','2026-06-02')['failed'],0)
        self.assertEqual(self.ingest_meeting('may','Acme has 25 benefits-eligible employees.','2026-05-01T10:00:00Z','2026-09-01')['failed'],0)
        self.assertEqual(self.context()['facts']['employee_count']['value'],30)
        self.assertEqual(self.ingest_meeting('may','Acme has 25 benefits-eligible employees.','2026-05-01T10:00:00Z','2026-09-01')['skipped'],1)

    def test_revision_removal_retires_old_source_evidence(self):
        self.ingest_meeting('source','Acme has 30 benefits-eligible employees.','2026-06-01T10:00:00Z','2026-06-02')
        result=self.ingest_meeting('source','For Acme Benefits, thank you for meeting.','2026-06-01T10:00:00Z','2026-09-01')
        self.assertEqual(result['failed'],0)
        self.assertNotIn('employee_count',self.context()['facts'])
        self.assertEqual(len(self.context()['history']['employee_count']),1)

    def test_failed_revision_keeps_last_successful_facts(self):
        self.ingest_meeting('source','Acme has 30 benefits-eligible employees.','2026-06-01T10:00:00Z','2026-06-02')
        with patch('corridor_context.ingest.extract_meeting_facts',side_effect=ValueError('Temporary extraction failure')):
            result=self.ingest_meeting('source','Acme has 31 benefits-eligible employees.','2026-06-01T10:00:00Z','2026-09-01')
        self.assertEqual(result['failed'],1)
        self.assertEqual(self.context()['facts']['employee_count']['value'],30)

    def test_dated_human_correction_updates_historical_period(self):
        import test_corrections
        from corridor_context.corrections import create_proposal, apply_proposal
        fixture=test_corrections.CorrectionTests();fixture.setUp()
        try:
            instruction='Set employee count to 25 effective January 1, 2027.'
            response=test_corrections.plan(instruction)
            response['changes'][0].update(effective_from='2027-01-01',effective_to=None,temporal_evidence='effective January 1, 2027')
            with patch('corridor_context.corrections._request_plan',return_value=response):
                proposal=create_proposal(fixture.conn,'acme-benefits',fixture.review_id,instruction,'Sam','future')
            apply_proposal(fixture.conn,'acme-benefits',fixture.review_id,proposal['id'])
            self.assertEqual(get_client_context(fixture.path,'Acme',as_of='2026-12-01')['facts']['employee_count']['value'],20)
            self.assertEqual(get_client_context(fixture.path,'Acme',as_of='2027-01-01')['facts']['employee_count']['value'],25)
        finally:fixture.tearDown()

    def test_older_revision_of_same_meeting_cannot_replace_newer_revision(self):
        self.ingest_meeting('source','Acme has 30 benefits-eligible employees.','2026-06-01T10:00:00Z','2026-09-02')
        result=self.ingest_meeting('source','Acme has 25 benefits-eligible employees.','2026-06-01T10:00:00Z','2026-09-01')
        self.assertEqual(result['skipped'],1)
        self.assertEqual(self.context()['facts']['employee_count']['value'],30)
