import copy
import json
import unittest
from structured_config import StructuredSession, RequestSchemaError
from test_structured_config import Session, Guard
from guard_mcp import error_result


class RequestSchemaTests(unittest.IsolatedAsyncioTestCase):
    async def test_exact_duplicate_ledger_produces_usable_nonmutating_correction(self):
        s=Session(Guard(),None,'session','/scratch/proj/.vivado-ai/log')
        await s.request(dict(op='begin',ip='generic',cell='u0',part='part',requirements=['requested feature']))
        req={'op':'describe','queries':['r1'],'requirements':['requested feature']};original=copy.deepcopy(req)
        with self.assertRaises(RequestSchemaError) as caught:await s.request(req)
        details=caught.exception.details
        self.assertEqual(details['unexpected_fields'],['requirements'])
        self.assertEqual(req,original)
        reply=await s.request(details['corrected_request'])
        self.assertIn('descriptions',reply)
        self.assertEqual(s.requirements,{'r1':'requested feature'})
        self.assertEqual(s.apply_attempts,0)

    async def test_changed_or_reordered_ledger_is_never_discarded(self):
        s=StructuredSession(None,None,'session','unused');s.requirements={'r1':'width 8','r2':'output enabled'}
        for values in [['width 16','output enabled'],['output enabled','width 8']]:
            with self.assertRaises(RequestSchemaError) as caught:await s.request({'op':'select','selections':[],'requirements':values})
            self.assertFalse(caught.exception.details['requirements_match_stored_ledger'])
            self.assertNotIn('corrected_request',caught.exception.details)
            self.assertEqual(s.requirements,{'r1':'width 8','r2':'output enabled'})

    async def test_other_unknown_fields_are_not_silently_dropped(self):
        s=StructuredSession(None,None,'session','unused')
        with self.assertRaises(RequestSchemaError) as caught:await s.request({'op':'select','selections':[],'invented_setting':1})
        self.assertEqual(caught.exception.details['unexpected_fields'],['invented_setting'])
        self.assertNotIn('corrected_request',caught.exception.details)

    async def test_missing_operation_is_explicit(self):
        s=StructuredSession(None,None,'session','unused')
        with self.assertRaises(RequestSchemaError) as caught:await s.request({'queries':['r1']})
        self.assertIn('describe',caught.exception.details['allowed_operations'])
        self.assertEqual(s.apply_attempts,0)

    def test_error_payload_exposes_repair_details(self):
        details={'operation':'describe','unexpected_fields':['requirements'],'allowed_fields':['op','queries']}
        result=error_result('STRUCTURED_ERROR: test','evidence.json',{'stage':'discovering'},details)
        self.assertTrue(result.isError)
        self.assertEqual(result.structuredContent['request_schema'],details)
        self.assertEqual(json.loads(result.content[0].text)['request_schema'],details)

if __name__=='__main__':unittest.main()
