#!/usr/bin/env python3
"""Configuration dependency waits may grant bounded grace only to proven transient comparisons."""
import json
import os
import shutil
import stat
import subprocess
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SETUP = os.path.join(ROOT, "scripts", "local-setup.nu")

FAKE = r'''#!/usr/bin/env python3
import json, os, pathlib, sys
counter = pathlib.Path(os.environ["FAKE_COUNTER"])
n = int(counter.read_text() or "0") + 1 if counter.exists() else 1
counter.write_text(str(n))
scenario = os.environ["FAKE_SCENARIO"]
name = sys.argv[sys.argv.index("application") + 1]
transient = {"type":"ComparisonError","message":"Failed to load target state: rpc error: code = Unavailable desc = dns: A record lookup error: lookup argocd-repo-server on 10.96.0.10:53: dial udp 10.96.0.10:53: i/o timeout"}
deterministic = {"type":"ComparisonError","message":"helm template failed: YAML parse error: unexpected EOF"}
status = {"health":{"status":"Healthy"},"sync":{"status":"Unknown"},"conditions":[]}
if scenario == "transient_then_ready":
    status["conditions"] = [transient]
    if n >= 62:
        status["sync"]["status"] = "Synced"
elif scenario == "transient_then_ready_immediately":
    status["conditions"] = [transient]
    if n >= 2:
        status["sync"]["status"] = "Synced"
elif scenario == "deterministic":
    status["conditions"] = [deterministic]
elif scenario == "empty":
    pass
elif scenario == "mixed":
    status["conditions"] = [transient if name == "good" else deterministic]
elif scenario == "mixed_conditions":
    status["conditions"] = [transient, deterministic]
elif scenario == "empty_message":
    status["conditions"] = [{"type":"ComparisonError","message":""}]
else:
    raise SystemExit(9)
print(json.dumps({"status":status}))
'''

class DependencyComparisonGraceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if shutil.which("nu") is None:
            raise unittest.SkipTest("nu is required")

    def run_case(self, scenario, apps, expect_success, expected_calls):
        with tempfile.TemporaryDirectory() as td:
            fake = os.path.join(td, "kubectl")
            counter = os.path.join(td, "counter")
            with open(fake, "w", encoding="utf-8") as fh:
                fh.write(FAKE)
            os.chmod(fake, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
            app_list = "[" + " ".join(json.dumps(x) for x in apps) + "]"
            expr = (
                f'source {json.dumps(SETUP)}; '
                f'wait_for_configuration_dependencies "test" {app_list} [] '
                '--poll-delay 0sec --transient-grace-attempts 3'
            )
            env = os.environ.copy()
            env.update({"PATH": td + os.pathsep + env.get("PATH", ""),
                        "FAKE_COUNTER": counter, "FAKE_SCENARIO": scenario})
            result = subprocess.run(["nu", "-c", expr], env=env,
                                    capture_output=True, text=True, timeout=30)
            if os.path.exists(counter):
                with open(counter, encoding="utf-8") as fh:
                    calls = int(fh.read())
            else:
                calls = 0
            self.assertEqual(result.returncode == 0, expect_success,
                             f"stdout={result.stdout!r} stderr={result.stderr!r}")
            self.assertEqual(calls, expected_calls)

    def test_transient_comparison_gets_bounded_grace_and_recovers(self):
        self.run_case("transient_then_ready", ["app"], True, 62)

    def test_deterministic_comparison_gets_no_grace(self):
        self.run_case("deterministic", ["app"], False, 60)

    def test_unknown_without_conditions_gets_no_grace(self):
        self.run_case("empty", ["app"], False, 60)

    def test_mixed_transient_and_deterministic_apps_get_no_grace(self):
        self.run_case("mixed", ["good", "bad"], False, 120)

    def test_mixed_conditions_on_one_app_get_no_grace(self):
        self.run_case("mixed_conditions", ["app"], False, 60)

    def test_empty_condition_message_gets_no_grace(self):
        self.run_case("empty_message", ["app"], False, 60)

    def test_production_call_sites_keep_safe_defaults(self):
        with open(SETUP, encoding="utf-8") as fh:
            source = fh.read()
        calls = [line for line in source.splitlines()
                 if "wait_for_configuration_dependencies " in line
                 and not line.lstrip().startswith(("#", "def "))]
        self.assertGreaterEqual(len(calls), 4)
        for line in calls:
            self.assertNotIn("--poll-delay", line)
            self.assertNotIn("--transient-grace-attempts", line)

    def test_production_default_really_waits_five_seconds(self):
        with tempfile.TemporaryDirectory() as td:
            fake = os.path.join(td, "kubectl")
            counter = os.path.join(td, "counter")
            with open(fake, "w", encoding="utf-8") as fh:
                fh.write(FAKE)
            os.chmod(fake, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
            env = os.environ.copy()
            env.update({"PATH": td + os.pathsep + env.get("PATH", ""),
                        "FAKE_COUNTER": counter,
                        "FAKE_SCENARIO": "transient_then_ready_immediately"})
            expr = (f'source {json.dumps(SETUP)}; '
                    'wait_for_configuration_dependencies "test" ["app"] []')
            import time
            started = time.monotonic()
            result = subprocess.run(["nu", "-c", expr], env=env,
                                    capture_output=True, text=True, timeout=15)
            elapsed = time.monotonic() - started
            with open(counter, encoding="utf-8") as fh:
                calls = int(fh.read())
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(calls, 2)
            self.assertGreaterEqual(elapsed, 4.5)
            self.assertLess(elapsed, 12)


if __name__ == "__main__":
    unittest.main(verbosity=2)
