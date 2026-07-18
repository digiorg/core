#!/usr/bin/env python3
"""harbor-proxy-cache-config must fail loud, never silent (Issue #279).

Confirmed runtime evidence: harbor-oidc-config always succeeds, but
harbor-proxy-cache-config reaches ``BackoffLimitExceeded``. The Job's read-back
branches (taken once a registry/project already exists from a prior sync,
because ``BeforeHookCreation`` recreates this hook on every Harbor sync) do:

    existing=$(${CURL} -sf -u "${AUTH}" "${HARBOR_API}/...")

Under ``set -eu`` a *bare assignment* whose right-hand side is a failing
command substitution kills the script immediately, with **no** echoed message
-- unlike every other failure path in this script, which prints an explicit
"ERROR: ..." line first. That silent-death shape is exactly what "harden with
retained/safe diagnostics ... inspect its API payload/readback rather than
guessing" calls for: today, if that one curl call fails, the pod dies with an
empty tail of logs and nobody can tell whether Harbor was unreachable, the
credential was wrong, or the API shape changed.

The fix keeps every HTTP call's status code and body available via
``-o file -w '%{http_code}'`` (the pattern the file already uses for its POSTs)
instead of ``-f`` + bare assignment, and prints a clear diagnostic including the
HTTP status before exiting non-zero. Credentials must never be printed (no
``-v``/``--verbose``/``-i``/``--include`` curl flags, no echoing ``$AUTH`` or
``$HARBOR_ADMIN_PASSWORD``).

Pure python3 + PyYAML::

    python3 platform/tests/test_harbor_proxy_cache_diagnostics.py
"""
import os
import re
import stat
import subprocess
import tempfile
import textwrap
import unittest

import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
JOB = os.path.join(REPO_ROOT, "platform", "base", "harbor", "harbor-proxy-cache-job.yaml")


def _script():
    with open(JOB, encoding="utf-8") as fh:
        docs = [d for d in yaml.safe_load_all(fh) if d]
    job = next(d for d in docs if d.get("kind") == "Job")
    return job["spec"]["template"]["spec"]["containers"][0]["command"][-1]


def _run_with_fake_curl(fake_curl):
    with tempfile.TemporaryDirectory() as tmp:
        curl_path = os.path.join(tmp, "curl")
        with open(curl_path, "w", encoding="utf-8") as fh:
            fh.write("#!/bin/sh\n" + textwrap.dedent(fake_curl))
        os.chmod(curl_path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
        env = os.environ.copy()
        env["PATH"] = tmp + os.pathsep + env["PATH"]
        env["HARBOR_ADMIN_PASSWORD"] = "fixture-password-never-print"
        return subprocess.run(
            ["/bin/sh", "-c", _script()],
            capture_output=True,
            text=True,
            env=env,
            timeout=20,
        )


class NoSilentCurlFailureTest(unittest.TestCase):
    def test_no_bare_assignment_from_a_dash_f_curl_call(self):
        script = _script()
        # This is the exact silent-death shape: `var=$(... -sf ...)` outside an
        # `if`/`while` test, which set -e kills with zero output.
        self.assertNotRegex(
            script, r"=\$\(\$\{CURL\}[^)]*-sf",
            "curl -f response captured via bare assignment dies silently under "
            "set -eu; capture the HTTP status explicitly instead",
        )

    def test_readback_calls_capture_explicit_http_status(self):
        script = _script()
        # Every readback GET must go through the same -o file -w "%{http_code}"
        # shape already used for the POST calls, so a failure is diagnosable.
        self.assertGreaterEqual(
            len(re.findall(r'-w\s+"%\{http_code\}"', script)), 3,
            "registry/project readback must capture an explicit HTTP status code",
        )

    def test_readback_failure_prints_actionable_error(self):
        script = _script()
        self.assertIn("could not read back registry", script)
        self.assertIn("could not read back project", script)

    def test_registry_id_lookup_filters_the_requested_name(self):
        script = _script()
        self.assertIn('grep -F "\\\"name\\\":\\\"${registry_name}\\\""', script)

    def test_create_transport_failure_is_actionable_and_secret_safe(self):
        result = _run_with_fake_curl(r'''
            case "$*" in
              */ping*) exit 0 ;;
              *) exit 7 ;;
            esac
        ''')
        output = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("create transport failure (curl exit 7)", output)
        self.assertNotIn("fixture-password-never-print", output)

    def test_readback_transport_failure_is_actionable_and_secret_safe(self):
        result = _run_with_fake_curl(r'''
            case "$*" in
              */ping*) exit 0 ;;
              *"-X POST"*) printf 409; exit 0 ;;
              *"/registries?q="*) exit 7 ;;
              *) exit 9 ;;
            esac
        ''')
        output = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("readback transport failure (curl exit 7)", output)
        self.assertNotIn("fixture-password-never-print", output)

    def test_never_prints_credentials(self):
        script = _script()
        for flag in ("--verbose", " -v ", "--include", " -i "):
            self.assertNotIn(flag, script, f"curl flag {flag!r} could leak the Basic-auth header")
        self.assertNotRegex(script, r"echo.*\$\{?AUTH\}?\b")
        self.assertNotRegex(script, r"echo.*HARBOR_ADMIN_PASSWORD")


if __name__ == "__main__":
    unittest.main(verbosity=2)
