import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("dns_sync", Path(__file__).resolve().parents[1] / "dns.py")
dns = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dns)


def record(identifier, kind, content, name="dealone.work"):
    return {"id": str(identifier), "type": kind, "content": content, "name": name}


class DNSTests(unittest.TestCase):
    def test_convert_parking_alias_preserves_mail_and_subdomains(self):
        records = [record(1, "ALIAS", "parked.porkbun.com"), record(2, "MX", "mail.example.com"),
                   record(3, "TXT", "verification-value"), record(4, "A", "8.8.8.8", "www.dealone.work"),
                   record(5, "CAA", '0 issue "letsencrypt.org"')]
        changes = dns.plan(records, "1.1.1.1")
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0][0], "/dns/edit/dealone.work/1")
        self.assertEqual(changes[0][1]["type"], "A")
        self.assertEqual(changes[0][1]["name"], "")

    def test_update_before_removing_duplicate_a_and_stale_ipv6(self):
        changes = dns.plan([record(1,"A","8.8.8.8"), record(2,"A","8.8.4.4"),
                            record(3,"AAAA","2606:4700:4700::1111")], "1.1.1.1")
        self.assertEqual([op for op, _ in changes], ["/dns/edit/dealone.work/1",
                         "/dns/delete/dealone.work/2", "/dns/delete/dealone.work/3"])

    def test_idempotent_when_already_correct(self):
        self.assertEqual(dns.plan([record(1,"A","1.1.1.1")], "1.1.1.1"), [])

    def test_creates_root_record_when_missing(self):
        self.assertEqual(dns.plan([record(1,"TXT","mail-policy")], "1.1.1.1")[0][0], "/dns/create/dealone.work")

    def test_refuses_private_and_ipv6_targets(self):
        for address in ("127.0.0.1", "10.0.0.1", "192.168.1.1", "::1", "2606:4700:4700::1111"):
            with self.assertRaises(ValueError):
                dns.plan([], address)

    def test_no_credentials_keeps_manual_mode(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(dns, "request") as api, patch("builtins.print"):
            dns.synchronize("1.1.1.1", apply=True)
            api.assert_not_called()

    def test_missing_one_credential_fails(self):
        with patch.dict(os.environ, {"PORKBUN_API_KEY":"test"}, clear=True), self.assertRaises(RuntimeError):
            dns.synchronize("1.1.1.1", apply=True)

    def test_dry_run_does_not_write(self):
        env={"PORKBUN_API_KEY":"test", "PORKBUN_SECRET_API_KEY":"test"}
        with patch.dict(os.environ, env, clear=True), patch.object(dns, "check_nameservers"), \
             patch.object(dns, "request", return_value={"records":[]}) as api, patch("builtins.print"):
            dns.synchronize("1.1.1.1")
            api.assert_called_once_with("/dns/retrieve/dealone.work")

    def test_applies_and_reads_back_authoritative_record(self):
        env={"PORKBUN_API_KEY":"test", "PORKBUN_SECRET_API_KEY":"test"}
        with patch.dict(os.environ, env, clear=True), patch.object(dns, "check_nameservers"), \
             patch.object(dns, "request", side_effect=[{"records":[]}, {"status":"SUCCESS"},
                          {"records":[record(1,"A","1.1.1.1")]}]) as api, patch("builtins.print"):
            dns.synchronize("1.1.1.1", apply=True)
            self.assertEqual(api.call_args_list[1].args[0], "/dns/create/dealone.work")
            self.assertEqual(api.call_count, 3)

    def test_non_porkbun_nameservers_stop_before_api_mutation(self):
        with patch.object(dns.subprocess, "run", return_value=SimpleNamespace(stdout="ns.example.net.\n")), \
             self.assertRaises(RuntimeError):
            dns.check_nameservers()

    def test_waits_for_load_balancer_public_ipv4(self):
        pending=SimpleNamespace(stdout=json.dumps({"status":{}}))
        ready=SimpleNamespace(stdout=json.dumps({"status":{"loadBalancer":{"ingress":[{"ip":"1.1.1.1"}]}}}))
        with patch.object(dns.subprocess, "run", side_effect=[pending,ready]), \
             patch.object(dns.time, "sleep"), patch("builtins.print"):
            self.assertEqual(dns.load_balancer_ip(), "1.1.1.1")


if __name__ == "__main__":
    unittest.main()
