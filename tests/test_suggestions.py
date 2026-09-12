import json
import unittest
from unittest.mock import patch

import test_corrections
from corridor_context.suggestions import suggest_resolution, saved_suggestion
from corridor_context.corrections import ConflictError, ignore_review
from corridor_context.context import get_client_context
from corridor_context.reviews import customer_reviews


class SuggestionTests(unittest.TestCase):
    setUp = test_corrections.CorrectionTests.setUp
    tearDown = test_corrections.CorrectionTests.tearDown
    def response(self, action='clarify'):
        return {'action':action, 'rationale':'The pronoun does not establish the employer.',
                'question':'Which employer has 200 employees?' if action == 'clarify' else '',
                'suggested_instruction':'' if action == 'clarify' else 'Ignore this other-employer statement.',
                'evidence_ids':['source-0']}

    def test_suggestion_persists_without_mutations_and_reuses_cache(self):
        before=get_client_context(self.path,'Acme')
        with patch('corridor_context.suggestions._request_plan',return_value=self.response()) as agent:
            first=suggest_resolution(self.conn,'acme-benefits',self.review_id)
            self.assertEqual(suggest_resolution(self.conn,'acme-benefits',self.review_id),first)
            agent.assert_called_once()
            self.assertTrue(agent.call_args.args[0]['review']['transcript']['segments'])
        self.assertEqual(get_client_context(self.path,'Acme'),before)
        self.assertEqual(customer_reviews(self.conn,'acme-benefits')['reviews'][0]['suggestion'],first)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM review_resolutions').fetchone()[0],0)
        self.assertEqual(first['suggested_instruction'],'')

    def test_other_customer_and_resolved_records_rejected(self):
        with self.assertRaises(KeyError):suggest_resolution(self.conn,'apex-manufacturing',self.review_id)
        ignore_review(self.conn,'acme-benefits',self.review_id,'Ignore','Sam','ignore')
        with self.assertRaises(ConflictError):suggest_resolution(self.conn,'acme-benefits',self.review_id)

    def test_invalid_evidence_and_assumed_clarification_fail(self):
        for result in [{**self.response(),'evidence_ids':['missing-source']},
                       {**self.response(),'suggested_instruction':'Set budget to $550'}]:
            with patch('corridor_context.suggestions._request_plan',return_value=result),self.assertRaises(ValueError):
                suggest_resolution(self.conn,'acme-benefits',self.review_id)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM review_suggestions').fetchone()[0],0)

    def test_stale_recommendation_refreshes_after_fact_change(self):
        with patch('corridor_context.suggestions._request_plan',return_value=self.response('ignore')) as agent:
            suggest_resolution(self.conn,'acme-benefits',self.review_id)
            self.conn.execute("UPDATE fact_versions SET normalized_value_json = '30' WHERE is_current=1")
            self.conn.commit()
            self.assertTrue(saved_suggestion(self.conn,'acme-benefits',self.review_id)['stale'])
            self.assertFalse(suggest_resolution(self.conn,'acme-benefits',self.review_id)['stale'])
            self.assertEqual(agent.call_count,2)

    def test_change_during_generation_rejects_proposal(self):
        def changing(*args,**kwargs):
            self.conn.execute("UPDATE fact_versions SET normalized_value_json = '30' WHERE is_current=1")
            self.conn.commit()
            return self.response()
        with patch('corridor_context.suggestions._request_plan',side_effect=changing),self.assertRaises(ConflictError):
            suggest_resolution(self.conn,'acme-benefits',self.review_id)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM review_suggestions').fetchone()[0],0)

    def test_accept_ignore_is_idempotent_and_keeps_facts(self):
        from corridor_context.suggestions import accept_suggestion
        before=get_client_context(self.path,'Acme')
        with patch('corridor_context.suggestions._request_plan',return_value=self.response('ignore')):
            suggestion=suggest_resolution(self.conn,'acme-benefits',self.review_id)
        with patch('corridor_context.corrections._request_plan',return_value={'action':'ignore','summary':'Other employer.','question':'','changes':[]}):
            result=accept_suggestion(self.conn,'acme-benefits',self.review_id,suggestion['id'],'Sam','accept-1')
        self.assertEqual(result['resolution']['action'],'ignored')
        self.assertEqual(accept_suggestion(self.conn,'acme-benefits',self.review_id,suggestion['id'],'Sam','accept-1'),result)
        self.assertEqual(get_client_context(self.path,'Acme'),before)

    def test_accept_update_preserves_history(self):
        from corridor_context.suggestions import accept_suggestion
        from test_corrections import plan
        instruction='Set employee count to 25.'
        advice={**self.response('update'),'suggested_instruction':instruction}
        with patch('corridor_context.suggestions._request_plan',return_value=advice):
            suggestion=suggest_resolution(self.conn,'acme-benefits',self.review_id)
        with patch('corridor_context.corrections._request_plan',return_value=plan(instruction)):
            result=accept_suggestion(self.conn,'acme-benefits',self.review_id,suggestion['id'],'Sam','accept-update')
        self.assertEqual(result['resolution']['action'],'updated')
        self.assertEqual(get_client_context(self.path,'Acme')['facts']['employee_count']['value'],25)

    def test_accept_rejects_wrong_customer_stale_and_clarification(self):
        from corridor_context.suggestions import accept_suggestion
        with patch('corridor_context.suggestions._request_plan',return_value=self.response()):
            suggestion=suggest_resolution(self.conn,'acme-benefits',self.review_id)
        with self.assertRaises(ValueError):accept_suggestion(self.conn,'acme-benefits',self.review_id,suggestion['id'],'Sam','x')
        with self.assertRaises(KeyError):accept_suggestion(self.conn,'apex-manufacturing',self.review_id,suggestion['id'],'Sam','x')
        self.conn.execute('DELETE FROM review_suggestions');self.conn.commit()
        with patch('corridor_context.suggestions._request_plan',return_value=self.response('ignore')):
            suggestion=suggest_resolution(self.conn,'acme-benefits',self.review_id)
        self.conn.execute("UPDATE fact_versions SET normalized_value_json = '30' WHERE is_current=1");self.conn.commit()
        with self.assertRaises(ConflictError):accept_suggestion(self.conn,'acme-benefits',self.review_id,suggestion['id'],'Sam','x')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM review_resolutions').fetchone()[0],0)

    def test_accept_changed_action_requires_review(self):
        from corridor_context.suggestions import accept_suggestion
        with patch('corridor_context.suggestions._request_plan',return_value=self.response('ignore')):
            suggestion=suggest_resolution(self.conn,'acme-benefits',self.review_id)
        with patch('corridor_context.corrections._request_plan',return_value={'action':'clarify','summary':'Need confirmation.','question':'Which employer?','changes':[]}):
            result=accept_suggestion(self.conn,'acme-benefits',self.review_id,suggestion['id'],'Sam','accept-clarify')
        self.assertEqual(result['proposal']['action'],'clarify')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM review_resolutions').fetchone()[0],0)

    def test_citations_resolve_to_original_text_without_model_copying(self):
        original = 'Their employer budget is $550 per employee per month.  We prefer that too — “perhaps”.'
        self.conn.execute('UPDATE attribution_reviews SET source_excerpt = ? WHERE id = ?', (original, self.review_id))
        self.conn.commit()
        with patch('corridor_context.suggestions._request_plan',return_value=self.response()) as agent:
            result=suggest_resolution(self.conn,'acme-benefits',self.review_id)
        self.assertEqual(result['evidence'],[original])
        supplied=agent.call_args.args[0]['evidence_passages']
        self.assertEqual(supplied[0],{'id':'source-0','text':original})
        allowed=agent.call_args.kwargs['schema']['properties']['evidence_ids']['items']['enum']
        self.assertEqual(allowed,[item['id'] for item in supplied])

    def test_empty_or_invalid_source_ids_do_not_save_suggestions(self):
        for ids in [[],['missing'],[0],[None]]:
            with patch('corridor_context.suggestions._request_plan',return_value={**self.response(),'evidence_ids':ids}),self.assertRaises(ValueError):
                suggest_resolution(self.conn,'acme-benefits',self.review_id)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM review_suggestions').fetchone()[0],0)
