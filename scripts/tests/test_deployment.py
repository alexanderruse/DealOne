import importlib.util
from pathlib import Path
import os
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DeploymentTests(unittest.TestCase):
    def test_only_available_optimized_cloud_can_be_selected(self):
        vultr = load("vultr")
        plans = [{"id":"vc2-2c-4gb","type":"vc2","vcpu_count":2,"ram":4096,"monthly_cost":10},
                 {"id":"voc-c-2c-4gb","type":"voc","vcpu_count":2,"ram":4096,"monthly_cost":30},
                 {"id":"voc-g-2c-8gb","type":"voc","vcpu_count":2,"ram":8192,"monthly_cost":50}]
        self.assertEqual(vultr.select_plan(plans, {p['id'] for p in plans})['id'], 'voc-c-2c-4gb')
        with self.assertRaises(RuntimeError):
            vultr.select_plan(plans, {'vc2-2c-4gb'})

    def test_deploy_requires_secrets_tls_and_explicit_model(self):
        deploy = load("deploy")
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(RuntimeError):
            deploy.runtime_config()
        env = {"DATABASE_URL":"postgresql+psycopg://user:password@host/db?sslmode=require",
               "SESSION_SECRET":"x"*32,"DEMO_ADMIN_TOKEN":"y"*24,"XAI_API_KEY":"test-key","XAI_MODEL":"sponsor-model"}
        with patch.dict(os.environ, env, clear=True):
            secret, config = deploy.runtime_config()
            self.assertNotIn('VULTR_API_KEY',secret)
            self.assertEqual(config['PAYMENTS_MODE'],'simulation')
            self.assertEqual(config['APP_DOMAIN'],'dealone.work')
        env['DATABASE_URL']='postgresql+psycopg://user:password@host/db?sslmode=disable'
        with patch.dict(os.environ, env, clear=True), self.assertRaises(RuntimeError):
            deploy.runtime_config()

    def test_quote_never_creates_cluster(self):
        vultr = load('vultr')
        with patch.object(vultr, 'all_pages', side_effect=[[], [{"id":"voc-c-2c-4gb","type":"voc","vcpu_count":2,"ram":4096,"monthly_cost":30}]]), \
             patch.object(vultr, 'request', side_effect=[{"available_plans":["voc-c-2c-4gb"]},{"versions":["v1.33.1+1"]}]) as api:
            vultr.provision()
            self.assertTrue(all(len(call.args) == 1 for call in api.call_args_list))


if __name__ == '__main__':
    unittest.main()
