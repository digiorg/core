#!/usr/bin/env python3
"""KinD containerd trust contract for the local Harbor ingress."""

from pathlib import Path
import os
import re
import subprocess
import tempfile
import time
import tomllib
import unittest

ROOT = Path(__file__).resolve().parents[2]
SETUP = ROOT / "scripts" / "local-setup.nu"
TEXT = SETUP.read_text()


def function_body(name: str) -> str:
    match = re.search(rf"(?m)^def {re.escape(name)}\b", TEXT)
    if not match:
        raise AssertionError(f"missing function {name}")
    nxt = re.search(r"(?m)^def ", TEXT[match.end():])
    end = match.end() + nxt.start() if nxt else len(TEXT)
    return TEXT[match.start():end]


class KindContainerdCaTrustContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.body = function_body("ensure_kind_node_digiorg_local_ca_trust")
        match = re.search(r"let node_script = r#'(.*?)'#", cls.body, re.S)
        if match is None:
            raise AssertionError("node trust mutation must be one inspectable literal script")
        cls.script = match.group(1)

    def make_ca(self, directory: Path) -> bytes:
        key = directory / "ca.key"
        cert = directory / "ca.crt"
        result = subprocess.run(
            [
                "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                "-keyout", str(key), "-out", str(cert), "-days", "1",
                "-subj", "/CN=Issue301 Test CA",
            ],
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        return cert.read_bytes()

    def run_node_script(self, root: Path, ca: bytes, script=None, env=None):
        script = self.script if script is None else script
        registry = root / "digiorg.local"
        executable = script.replace(
            'registry_dir="/etc/containerd/certs.d/digiorg.local"',
            f'registry_dir="{registry}"',
        )
        return subprocess.run(
            ["sh", "-c", executable], input=ca, capture_output=True, env=env
        )

    def test_installs_exact_tls_verified_pull_resolve_configuration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ca = self.make_ca(root)
            result = self.run_node_script(root, ca)
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
            registry = root / "digiorg.local"
            self.assertEqual((registry / "ca.crt").read_bytes(), ca)
            hosts = (registry / "hosts.toml").read_text()
            self.assertEqual(
                hosts,
                'server = "https://digiorg.local"\n\n'
                'capabilities = ["pull", "resolve"]\n'
                'ca = "/etc/containerd/certs.d/digiorg.local/ca.crt"\n',
            )
            parsed = tomllib.loads(hosts)
            self.assertEqual(parsed["server"], "https://digiorg.local")
            self.assertEqual(parsed["capabilities"], ["pull", "resolve"])
            self.assertEqual(
                parsed["ca"], "/etc/containerd/certs.d/digiorg.local/ca.crt"
            )
            self.assertNotIn("host", parsed)
            self.assertNotIn("skip_verify", hosts)
            self.assertNotIn("push", hosts)

    def test_missing_containerd_trust_root_is_created_before_registry_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Path(tmp) / "fixture"
            fixture.mkdir()
            ca = self.make_ca(fixture)
            missing_trust_root = Path(tmp) / "certs.d"
            self.assertFalse(missing_trust_root.exists())
            result = self.run_node_script(missing_trust_root, ca)
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
            registry = missing_trust_root / "digiorg.local"
            self.assertTrue(registry.is_dir())
            self.assertEqual((registry / "ca.crt").read_bytes(), ca)
            self.assertTrue((registry / "hosts.toml").is_file())

    def faulting_mv_env(self, root: Path, mode: str):
        fake_bin = root / ("bin-" + mode)
        fake_bin.mkdir()
        fake_mv = fake_bin / "mv"
        fake_mv.write_text(
            "#!/bin/sh\n"
            "set -eu\n"
            f"mode={mode!r}\n"
            "src=\"$2\"\n"
            "dst=\"$3\"\n"
            "case \"$mode:$src\" in\n"
            "  fail-hosts:*.hosts.toml.*) exit 2 ;;\n"
            "  fail-ca:*.ca.crt.*) exit 2 ;;\n"
            "  concurrent:*.hosts.toml.*) printf '%s\\n' 'server = \"https://other.invalid\"' >\"$dst\"; exit 0 ;;\n"
            "esac\n"
            "exec /bin/mv \"$@\"\n"
        )
        fake_mv.chmod(0o755)
        return dict(os.environ, PATH=f"{fake_bin}:{os.environ['PATH']}")

    def test_initial_hosts_publish_failure_installs_no_ca(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ca = self.make_ca(root)
            result = self.run_node_script(
                root, ca, env=self.faulting_mv_env(root, "fail-hosts")
            )
            self.assertNotEqual(result.returncode, 0)
            registry = root / "digiorg.local"
            self.assertFalse((registry / "ca.crt").exists())
            self.assertFalse((registry / "hosts.toml").exists())

    def test_interruption_after_policy_publish_leaves_restrictive_missing_ca_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ca = self.make_ca(root)
            result = self.run_node_script(
                root, ca, env=self.faulting_mv_env(root, "fail-ca")
            )
            self.assertNotEqual(result.returncode, 0)
            registry = root / "digiorg.local"
            self.assertFalse((registry / "ca.crt").exists())
            policy = tomllib.loads((registry / "hosts.toml").read_text())
            self.assertEqual(policy["capabilities"], ["pull", "resolve"])
            self.assertNotIn("host", policy)

    def test_concurrent_conflicting_hosts_creator_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ca = self.make_ca(root)
            result = self.run_node_script(
                root, ca, env=self.faulting_mv_env(root, "concurrent")
            )
            self.assertNotEqual(result.returncode, 0)
            registry = root / "digiorg.local"
            self.assertFalse((registry / "ca.crt").exists())
            self.assertEqual(
                (registry / "hosts.toml").read_text(),
                'server = "https://other.invalid"\n',
            )

    def test_identical_rerun_does_not_replace_installed_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ca = self.make_ca(root)
            first = self.run_node_script(root, ca)
            self.assertEqual(first.returncode, 0, first.stderr.decode(errors="replace"))
            registry = root / "digiorg.local"
            before = tuple((registry / name).stat().st_ino for name in ("ca.crt", "hosts.toml"))
            time.sleep(0.01)
            second = self.run_node_script(root, ca)
            self.assertEqual(second.returncode, 0, second.stderr.decode(errors="replace"))
            after = tuple((registry / name).stat().st_ino for name in ("ca.crt", "hosts.toml"))
            self.assertEqual(after, before)

    def test_malformed_ca_fails_without_installing_trust(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = self.run_node_script(root, b"not a certificate\n")
            self.assertNotEqual(result.returncode, 0)
            registry = root / "digiorg.local"
            self.assertFalse((registry / "ca.crt").exists())
            self.assertFalse((registry / "hosts.toml").exists())

    def test_conflicting_hosts_configuration_fails_without_rotating_ca(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry = root / "digiorg.local"
            registry.mkdir()
            old_ca = b"existing-ca-bytes\n"
            (registry / "ca.crt").write_bytes(old_ca)
            conflicting = 'server = "https://other.invalid"\n'
            (registry / "hosts.toml").write_text(conflicting)
            new_ca = self.make_ca(root)
            result = self.run_node_script(root, new_ca)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(b"conflicting containerd hosts configuration", result.stderr)
            self.assertEqual((registry / "ca.crt").read_bytes(), old_ca)
            self.assertEqual((registry / "hosts.toml").read_text(), conflicting)

    def test_windows_line_endings_are_normalized_at_command_boundary(self):
        self.assertIn('str replace --all "\\r\\n" "\\n"', self.body)
        self.assertIn('str replace --all "\\r" "\\n"', self.body)
        self.assertIn("docker exec -i $kind_node sh -c $normalized_node_script", self.body)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ca = self.make_ca(root)
            for newline in ("\r\n", "\r"):
                with self.subTest(newline=repr(newline)):
                    transcoded = self.script.replace("\n", newline)
                    normalized = transcoded.replace("\r\n", "\n").replace("\r", "\n")
                    result = self.run_node_script(root, ca, normalized)
                    self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))

    def test_public_ca_flows_over_stdin_and_errors_fail_closed(self):
        self.assertIn("digiorg-local-ca-secret", self.body)
        self.assertIn("jsonpath='{.data.ca\\.crt}'", self.body)
        self.assertIn("decode base64", self.body)
        self.assertIn("docker exec -i $kind_node", self.body)
        self.assertIn("| complete", self.body)
        self.assertIn("if $result.exit_code != 0", self.body)
        self.assertIn("error make", self.body)
        self.assertNotIn("--insecure", self.body)
        self.assertNotIn("skip_verify", self.body)

    def test_resume_installs_node_trust_before_apps_and_keeps_post_loop_convergence(self):
        sync = function_body("sync_gated_apps_for_local_dev")
        dependency = sync.index('wait_for_configuration_dependencies "Digiorg local CA"')
        trust = sync.index("ensure_kind_node_digiorg_local_ca_trust")
        loop = sync.index("for app in $gated_apps")
        wait = sync.index("wait_for_ingress_local_ca_convergence")
        self.assertLess(dependency, trust)
        self.assertLess(trust, loop)
        self.assertLess(loop, wait)
        self.assertEqual(sync.count("wait_for_ingress_local_ca_convergence"), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
