import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

spec=importlib.util.spec_from_file_location('nessie_setup',Path(__file__).resolve().parents[1]/'nessie_setup.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


class NessieSetupTests(unittest.TestCase):
    def test_existing_sandbox_resources_are_used_without_creation_or_secret_output(self):
        responses=[{'items':[{'id':'customer-id','first_name':'DealOne'}]},
                   {'items':[{'id':'merchant-id','name':'DealOne Sandbox Checkout'}]}]
        def request(req,timeout):
            self.assertEqual(req.headers['Authorization'],'Bearer private-test-key')
            self.assertNotIn('private-test-key',req.full_url)
            self.assertIsNone(req.data)
            return io.BytesIO(json.dumps(responses.pop(0)).encode())
        with tempfile.NamedTemporaryFile() as env, patch.dict(os.environ,{'NESSIE_API_KEY':'private-test-key','GITHUB_ENV':env.name},clear=True), patch.object(module.urllib.request,'urlopen',side_effect=request), patch('sys.stdout',new_callable=io.StringIO) as output:
            module.setup()
            self.assertNotIn('private-test-key',output.getvalue())
            content=Path(env.name).read_text()
            self.assertIn('NESSIE_CUSTOMER_ID=customer-id',content)
            self.assertIn('NESSIE_MERCHANT_ID=merchant-id',content)

    def test_invalid_key_stops_setup_without_exposing_it(self):
        with patch.dict(os.environ,{'NESSIE_API_KEY':'private-test-key'},clear=True), patch.object(module.urllib.request,'urlopen',side_effect=HTTPError('https://example.test',401,'Unauthorized',None,None)):
            with self.assertRaisesRegex(RuntimeError,'HTTP 401') as raised:
                module.setup()
            self.assertNotIn('private-test-key',str(raised.exception))
