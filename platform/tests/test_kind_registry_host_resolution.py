#!/usr/bin/env python3
"""KinD node registry host-resolution contract for Issue #301."""

from pathlib import Path
import re
import os
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SETUP = ROOT / "scripts" / "local-setup.nu"
TEXT = SETUP.read_text()


def function_body(name: str) -> str:
    match = re.search(rf"(?m)^def (?:\"main )?{re.escape(name)}(?:\"|\b)", TEXT)
    if not match:
        raise AssertionError(f"missing function {name}")
    nxt = re.search(r"(?m)^def ", TEXT[match.end():])
    end = match.end() + nxt.start() if nxt else len(TEXT)
    return TEXT[match.start():end]


class KindRegistryHostResolutionContractTest(unittest.TestCase):
    def node_script_and_body(self):
        body = function_body("ensure_kind_node_digiorg_local_resolution")
        match = re.search(r"let node_script = r#'(.*?)'#", body, re.S)
        if match is None:
            self.fail("node mutation must be one inspectable literal script")
        return match.group(1), body

    def run_script(self, script, hosts, *, env=None):
        executable = script.replace("/etc/hosts", str(hosts))
        return subprocess.run(
            ["sh", "-c", executable], capture_output=True, text=True, env=env
        )

    def test_bootstrap_configures_node_resolution_before_ingress(self):
        body = function_body("bootstrap")
        call = "ensure_kind_node_digiorg_local_resolution"
        self.assertIn(call, body)
        self.assertLess(body.index(call), body.index("install_ingress"))

    def test_resume_configures_node_resolution_before_ingress_and_apps(self):
        body = function_body("sync_gated_apps_for_local_dev")
        call = "ensure_kind_node_digiorg_local_resolution"
        self.assertIn(call, body)
        self.assertLess(body.index(call), body.index("apply_bootstrap_managed_ingress_for_local_dev"))
        self.assertLess(body.index(call), body.index("for app in $gated_apps"))

    def test_node_script_is_idempotent_exact_and_fail_closed(self):
        script, _ = self.node_script_and_body()
        self.assertIn("127.0.0.1 digiorg.local", script)
        self.assertNotIn("--insecure", script)
        self.assertNotIn("insecure_skip_verify", script)
        self.assertIn("conflicting digiorg.local", script)
        self.assertIn("exactly one", script)

        with tempfile.TemporaryDirectory() as tmp:
            hosts = Path(tmp) / "hosts"
            hosts.write_text("127.0.0.1 localhost\n")
            first = self.run_script(script, hosts)
            self.assertEqual(first.returncode, 0, first.stderr)
            second = self.run_script(script, hosts)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(hosts.read_text().splitlines().count("127.0.0.1 digiorg.local"), 1)

            hosts.write_text("127.0.0.1 localhost\n10.0.0.7 digiorg.local\n")
            conflict = self.run_script(script, hosts)
            self.assertNotEqual(conflict.returncode, 0)
            self.assertIn("conflicting digiorg.local", conflict.stderr)
            self.assertNotIn("127.0.0.1 digiorg.local", hosts.read_text().splitlines())

    def test_ambiguous_single_line_and_duplicate_forms_fail_closed(self):
        script, _ = self.node_script_and_body()
        ambiguous = (
            "127.0.0.1 digiorg.local extra.local\n",
            "127.0.0.1 digiorg.local digiorg.local\n",
            "127.0.0.1 digiorg.local # digiorg.local\n",
            "127.0.0.1 digiorg.local\n127.0.0.1 digiorg.local\n",
        )
        with tempfile.TemporaryDirectory() as tmp:
            hosts = Path(tmp) / "hosts"
            for content in ambiguous:
                with self.subTest(content=content):
                    hosts.write_text(content)
                    result = self.run_script(script, hosts)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("conflicting digiorg.local", result.stderr)
                    self.assertEqual(hosts.read_text(), content)

    def test_semantically_exact_singleton_with_alternate_whitespace_is_accepted(self):
        script, _ = self.node_script_and_body()
        with tempfile.TemporaryDirectory() as tmp:
            hosts = Path(tmp) / "hosts"
            content = "\t127.0.0.1\t digiorg.local   \n"
            hosts.write_text(content)
            result = self.run_script(script, hosts)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(hosts.read_text(), content)

    def test_inspection_failure_does_not_mutate_hosts(self):
        script, _ = self.node_script_and_body()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            hosts = root / "hosts"
            original = "127.0.0.1 localhost\n"
            hosts.write_text(original)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            fake_awk = fake_bin / "awk"
            fake_awk.write_text("#!/bin/sh\nexit 2\n")
            fake_awk.chmod(0o755)
            env = dict(os.environ, PATH=f"{fake_bin}:{os.environ['PATH']}")
            result = self.run_script(script, hosts, env=env)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("failed to inspect", result.stderr)
            self.assertEqual(hosts.read_text(), original)

    def test_crlf_and_lone_cr_are_normalized_at_command_boundary(self):
        script, body = self.node_script_and_body()
        self.assertIn('str replace --all "\\r\\n" "\\n"', body)
        self.assertIn('str replace --all "\\r" "\\n"', body)
        self.assertIn("docker exec $kind_node sh -c $normalized_node_script", body)
        with tempfile.TemporaryDirectory() as tmp:
            for newline in ("\r\n", "\r"):
                with self.subTest(newline=repr(newline)):
                    hosts = Path(tmp) / ("hosts-" + str(len(newline)))
                    hosts.write_text("127.0.0.1 localhost\n")
                    transcoded = script.replace("\n", newline)
                    normalized = transcoded.replace("\r\n", "\n").replace("\r", "\n")
                    result = self.run_script(normalized, hosts)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(
                        hosts.read_text().splitlines().count("127.0.0.1 digiorg.local"), 1
                    )

    def test_docker_exec_is_fail_closed(self):
        _, body = self.node_script_and_body()
        self.assertIn('docker exec $kind_node sh -c $normalized_node_script', body)
        self.assertIn("| complete", body)
        self.assertIn("if $result.exit_code != 0", body)
        self.assertIn("error make", body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
