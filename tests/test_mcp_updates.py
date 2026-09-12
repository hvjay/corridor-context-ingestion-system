"""A running MCP process sees committed human corrections without restarting."""
import json
import subprocess
import sys
import unittest
from pathlib import Path

import test_corrections
from corridor_context.corrections import apply_proposal


class MCPUpdateTests(unittest.TestCase):
    def test_existing_mcp_process_reads_newly_applied_correction(self):
        fixture = test_corrections.CorrectionTests()
        fixture.setUp()
        process = subprocess.Popen([sys.executable, '-m', 'corridor_context.mcp_server', '--db', str(fixture.path)],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        def call(request_id):
            request={'jsonrpc':'2.0','id':request_id,'method':'tools/call',
                     'params':{'name':'get_client_context','arguments':{'client':'Acme Benefits','includeHistory':True}}}
            process.stdin.write(json.dumps(request)+'\n');process.stdin.flush()
            response=json.loads(process.stdout.readline())
            return json.loads(response['result']['content'][0]['text'])
        try:
            before=call(1)
            self.assertEqual(before['facts']['employee_count']['value'],20)
            proposal=fixture.proposal()
            apply_proposal(fixture.conn,'acme-benefits',fixture.review_id,proposal['id'])
            after=call(2)
            self.assertEqual(after['facts']['employee_count']['value'],25)
            self.assertGreater(after['contextVersion'],before['contextVersion'])
            self.assertEqual(after['facts']['employee_count']['provenance']['sourceType'],'human_correction')
            self.assertEqual([item['value'] for item in after['history']['employee_count']],[20,25])
        finally:
            process.stdin.close()
            process.wait(timeout=5)
            process.stdout.close()
            fixture.tearDown()
