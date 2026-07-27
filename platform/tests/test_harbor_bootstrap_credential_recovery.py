#!/usr/bin/env python3
"""Issue #285 stdout12: the Harbor bootstrap credential contract.

Live evidence from two independent, genuinely fresh KinD runs (immutable
runtime tag ``issue285-runtime-v10-20260725T162655Z``):

* ``Request/harbor-crossplane-system-robot`` reported ``Ready=True
  Available`` and ``Synced=True ReconcileSuccess``; last observed method
  ``GET`` with status ``200``. The Harbor robot genuinely exists with exactly
  the declared least-privilege permission set.
* ``crossplane-system/crossplane-harbor-credentials`` nonetheless existed with
  **zero data keys**, and its ``.metadata.managedFields`` proved
  ``crossplane-http-provider`` had only ever owned ``type`` -- no manager ever
  owned a ``data`` path. No other Core/Catalog code creates or clears it.

Root cause (verified against the pinned upstream sources, not inferred):

1. ``internal/data-patcher/patch.go`` ``ApplyResponseDataToSecrets`` is
   best-effort: every per-Secret failure is ``logger.Info``'d and discarded,
   so a failed injection never fails the reconcile and is never retried.
2. ``internal/kube-handler/client.go`` ``GetOrCreateSecret`` creates the
   Secret *shell* before any key is written, and
   ``internal/service/request/deployaction.go`` applies injection to the
   CREATE response **whatever its status code** -- so any non-2xx or
   unexpected CREATE response deterministically leaves exactly the observed
   artifact: a Secret owning ``type`` and nothing else.
3. goharbor/harbor v2.15.1 only ever populates ``secret`` on ``CreateRobot``
   (``src/server/v2.0/handler/robot.go``); ``ListRobot``/``GetRobotByID``
   return the ``Robot`` model, which has no secret. With
   ``missingFieldStrategy: preserve`` (required, see
   test_harbor_bootstrap_crash_safety.py) no later OBSERVE can ever
   repopulate the keys.
4. Once the robot exists, ``expectedResponseCheck`` reports up-to-date
   forever, so provider-http never re-issues CREATE -- and re-issuing would
   collide with Harbor's own ``unique_robot UNIQUE(name, project_id)``
   constraint anyway.

=> The credential is **structurally unrecoverable** through the declarative
Request alone, and the bootstrap had no gate that could even notice: it
checked Argo sync/health and the Request's conditions, both of which were
green. The failure only surfaced ~15 minutes later as an unrelated gated-sync
timeout on the *next* Application.

The contracts below lock the correction: a fail-closed, in-cluster credential
gate that inspects key presence/non-emptiness without ever reading a value,
and a crash/resume-safe recovery that refreshes the existing robot's secret
(Harbor ``PATCH /robots/{robot_id}`` -> ``RefreshSec``) instead of minting a
second robot -- and that never runs at all when the credential is healthy.

Run:
    python3 -m unittest discover -s platform/tests \\
        -p 'test_harbor_bootstrap_credential_recovery.py'
"""
from pathlib import Path
import json
import re
import shutil
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[2]
SETUP_PATH = ROOT / "scripts/local-setup.nu"
SETUP = SETUP_PATH.read_text(encoding="utf-8")
NU = shutil.which("nu")


def run_nu(snippet: str, timeout: int = 60) -> subprocess.CompletedProcess:
    """Execute a snippet against the real production Nushell source.

    Source-level assertions cannot catch runtime faults such as
    `column_already_exists`; these helpers run the shipped functions.
    """
    return subprocess.run(
        [NU, "--no-config-file", "-c", f"source {SETUP_PATH.as_posix()}\n{snippet}"],
        cwd=ROOT, text=True, capture_output=True, timeout=timeout, check=False,
    )
REQUEST = yaml.safe_load(
    (ROOT / "crossplane/bootstrap/harbor-robot-request.yaml").read_text(encoding="utf-8")
)

REQUIRED_KEYS = ("name", "secret", "basicAuth")
CREDENTIAL_SECRET = "crossplane-harbor-credentials"

# Every Nushell function that makes up the new bootstrap boundary.
BOUNDARY_FUNCS = (
    "harbor_credential_required_keys",
    "normalize_posix_container_script",
    "harbor_credential_probe_script",
    "harbor_credential_repair_script",
    "harbor_robot_selector_jq",
    "harbor_robot_expected_permissions",
    "harbor_credential_probe_job",
    "harbor_credential_repair_job",
    "harbor_credential_recovery_rbac",
    "probe_harbor_credential_keys",
    "parse_harbor_credential_probe_output",
    "run_bootstrap_job",
    "cleanup_bootstrap_job_verified",
    "get_crossplane_system_pod_list",
    "fail_bootstrap_job_after_cleanup",
    "select_owned_pod",
    "harbor_recovery_privilege_leftovers",
    "harbor_recovery_cleanup_verdict",
    "harbor_recovery_resume_preflight",
    "harbor_recovery_delete_leftover_pods",
    "harbor_recovery_final_teardown",
    "harbor_credential_missing_keys",
    "ensure_harbor_credential_secret_shell",
    "repair_harbor_credential_secret",
    "ensure_crossplane_harbor_credentials",
)

POD_LIST_RAW_PATH = "/api/v1/namespaces/crossplane-system/pods"


def func_body(name: str) -> str:
    """Return the source of a single Nushell function definition.

    Trailing blank/comment lines belong to the *next* definition's doc block,
    so they are dropped -- assertions must judge the function itself.
    """
    start = SETUP.index(f"def {name} [")
    nxt = SETUP.find("\ndef ", start + 5)
    body = SETUP[start:] if nxt < 0 else SETUP[start:nxt]
    lines = body.splitlines()
    while lines and (not lines[-1].strip() or lines[-1].lstrip().startswith("#")):
        lines.pop()
    return "\n".join(lines)


class BootstrapBoundaryExistsTest(unittest.TestCase):
    def test_every_boundary_function_is_defined_exactly_once(self):
        for name in BOUNDARY_FUNCS:
            self.assertEqual(
                SETUP.count(f"def {name} ["), 1, f"{name} must be defined exactly once"
            )

    def test_required_keys_are_exactly_the_bootstrap_contract(self):
        body = func_body("harbor_credential_required_keys")
        literal = re.search(r"\[([^\]]*)\]", body[body.index("{"):]).group(1)
        self.assertEqual(
            re.findall(r'"([A-Za-z]+)"', literal),
            list(REQUIRED_KEYS),
            "the contract is exactly name/secret/basicAuth, in that order",
        )


@unittest.skipIf(NU is None, "nushell (nu) is required for behavioural parser tests")
class ProbeOutputParserBehaviourTest(unittest.TestCase):
    """Issue #285 review finding 2: the probe parser is executed, not just read.

    The previous implementation seeded every required key with `insert` and
    then wrote the parsed value with `insert` again. Nushell 0.114.1 raises
    `nu::shell::column_already_exists` on the second write, so the gate
    crashed at runtime on *every* probe -- while every source-level assertion
    still passed. These tests run the shipped function.
    """

    def parse(self, output: str) -> dict:
        escaped = output.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        result = run_nu(f'parse_harbor_credential_probe_output "{escaped}" | to json')
        self.assertEqual(
            result.returncode, 0,
            f"parser crashed on {output!r}\nSTDERR:\n{result.stderr}",
        )
        return json.loads(result.stdout)

    def test_all_keys_present_parses_to_all_true(self):
        self.assertEqual(
            self.parse("name=true\nsecret=true\nbasicAuth=true"),
            {"name": True, "secret": True, "basicAuth": True},
        )

    def test_realistic_partial_report_parses_each_key_independently(self):
        self.assertEqual(
            self.parse("name=true\nsecret=true\nbasicAuth=false"),
            {"name": True, "secret": True, "basicAuth": False},
        )

    def test_empty_secret_shell_reports_every_key_false(self):
        self.assertEqual(
            self.parse("name=false\nsecret=false\nbasicAuth=false"),
            {"name": False, "secret": False, "basicAuth": False},
        )

    def test_missing_and_unknown_lines_fail_closed_to_false(self):
        # Truncated output, interleaved noise and unrelated keys must never be
        # read as "present" -- the gate has to stay fail-closed.
        self.assertEqual(
            self.parse("name=true\nsomethingElse=true\n\nnot-a-pair"),
            {"name": True, "secret": False, "basicAuth": False},
        )

    def test_missing_keys_helper_agrees_with_parsed_report(self):
        result = run_nu(
            'parse_harbor_credential_probe_output "name=true\\nsecret=false\\nbasicAuth=true"'
            " | harbor_credential_missing_keys $in | to json"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), ["secret"])


@unittest.skipIf(NU is None or shutil.which("jq") is None, "nu and jq are required")
class RobotSelectionBehaviourTest(unittest.TestCase):
    """Issue #285 review findings 6+7: the robot is selected by a real JSON
    parser, structurally, with an exact identity and an exact permission set.

    The previous implementation matched with `grep`/`tr "}"` on the first
    default page (Harbor `page_size` defaults to 10) using
    `endswith("crossplane-system")` and never compared permissions -- so a
    similarly named or over-privileged robot could be silently rotated, and
    the intended robot could be missed entirely beyond page 1.
    """

    @classmethod
    def setUpClass(cls):
        filt = run_nu("harbor_robot_selector_jq")
        assert filt.returncode == 0, filt.stderr
        cls.filter_text = filt.stdout
        expected = run_nu("harbor_robot_expected_permissions")
        assert expected.returncode == 0, expected.stderr
        cls.expected = expected.stdout.strip()

    def select(self, robots) -> dict:
        proc = subprocess.run(
            ["jq", "-c", "--arg", "name", "crossplane-system",
             "--argjson", "expected", self.expected, self.filter_text],
            input=json.dumps(robots), text=True, capture_output=True, timeout=30,
        )
        self.assertEqual(proc.returncode, 0, f"selector failed: {proc.stderr}")
        return json.loads(proc.stdout)

    @staticmethod
    def canonical_permissions():
        return [
            {"kind": "system", "namespace": "/",
             "access": [{"resource": "project", "action": "create"}]},
            {"kind": "project", "namespace": "*",
             "access": [{"resource": "robot", "action": "create"},
                        {"resource": "robot", "action": "read"},
                        {"resource": "artifact", "action": "read"}]},
        ]

    def robot(self, **over):
        base = {"id": 7, "name": "robot$crossplane-system", "level": "system",
                "permissions": self.canonical_permissions()}
        base.update(over)
        return base

    def test_expected_permissions_match_the_declarative_manifest_exactly(self):
        """The recovery's expectation and the Request's own least-privilege
        payload must never drift apart."""
        payload = json.loads(REQUEST["spec"]["forProvider"]["payload"]["body"])
        def norm(perms):
            return sorted(
                (p["kind"], p["namespace"],
                 tuple(sorted((a["resource"], a["action"]) for a in p["access"])))
                for p in perms
            )
        self.assertEqual(norm(json.loads(self.expected)), norm(payload["permissions"]))

    def test_exact_canonical_robot_is_selected(self):
        result = self.select([self.robot()])
        self.assertEqual(result["ok"], True)
        self.assertEqual(result["id"], 7)

    def test_reordered_json_and_permission_order_still_matches(self):
        reordered = {
            "level": "system",
            "permissions": [
                {"namespace": "*", "kind": "project",
                 "access": [{"action": "read", "resource": "artifact", "effect": "allow"},
                            {"action": "create", "resource": "robot"},
                            {"action": "read", "resource": "robot"}]},
                {"kind": "system", "namespace": "/",
                 "access": [{"resource": "project", "action": "create"}]},
            ],
            "name": "robot$crossplane-system", "id": 42,
        }
        self.assertEqual(self.select([reordered]), {"ok": True, "id": 42,
                                                    "name": "robot$crossplane-system",
                                                    "matches": 1})

    def test_similarly_named_decoy_is_never_selected(self):
        decoy = self.robot(id=9, name="robot$other-crossplane-system", permissions=[])
        result = self.select([decoy, self.robot()])
        self.assertEqual(result["ok"], True)
        self.assertEqual(result["id"], 7, "must pick the exact canonical robot, not the decoy")

    def test_only_a_decoy_present_is_no_match_not_a_rotation(self):
        decoy = self.robot(id=9, name="robot$other-crossplane-system", permissions=[])
        self.assertEqual(self.select([decoy]), {"ok": False, "reason": "no-match", "matches": 0})

    def test_robot_without_harbor_prefix_fails_closed(self):
        self.assertEqual(self.select([self.robot(name="crossplane-system")])["ok"], False)

    def test_recovery_uses_exact_server_side_name_query(self):
        script = func_body("harbor_credential_repair_script")
        self.assertIn("robots?q=Name=$ROBOT_NAME&page=", script)
        self.assertNotIn("robots?q=Name=~$ROBOT_NAME", script)

    def test_project_level_robot_is_not_a_system_match(self):
        self.assertEqual(self.select([self.robot(level="project")])["reason"], "no-match")

    def test_ambiguous_duplicate_canonical_matches_fail_closed(self):
        result = self.select([self.robot(id=7), self.robot(id=8)])
        self.assertEqual(result["ok"], False)
        self.assertEqual(result["reason"], "ambiguous")

    def test_extra_action_is_permission_drift(self):
        perms = self.canonical_permissions()
        perms[1]["access"].append({"resource": "robot", "action": "delete"})
        result = self.select([self.robot(permissions=perms)])
        self.assertEqual(result, {"ok": False, "reason": "permission-drift", "matches": 1})

    def test_missing_action_is_permission_drift(self):
        perms = self.canonical_permissions()
        perms[1]["access"].pop()
        self.assertEqual(self.select([self.robot(permissions=perms)])["reason"],
                         "permission-drift")

    def test_wrong_namespace_is_permission_drift(self):
        perms = self.canonical_permissions()
        perms[1]["namespace"] = "library"
        self.assertEqual(self.select([self.robot(permissions=perms)])["reason"],
                         "permission-drift")

    def test_missing_permissions_field_is_drift_not_success(self):
        robot = self.robot()
        del robot["permissions"]
        self.assertEqual(self.select([robot])["reason"], "permission-drift")

    def test_object_valued_permissions_containing_canonical_entries_is_rejected(self):
        # jq's `.[]?` on an OBJECT iterates its VALUES, not an error -- so a
        # `permissions` field that is a JSON object (not an array) whose
        # values happen to be the two canonical permission entries must
        # never normalize the same as the real canonical array. PR#287
        # finding: this must fail closed (never `ok: true`).
        perms = self.canonical_permissions()
        malformed_permissions = {"first": perms[0], "second": perms[1]}
        result = self.select([self.robot(permissions=malformed_permissions)])
        self.assertEqual(result["ok"], False)

    def test_object_valued_access_containing_canonical_entries_is_rejected(self):
        # Same shape confusion one level deeper: a permission entry's
        # `access` must be an array of objects, never an object whose values
        # happen to be the canonical access entries.
        perms = self.canonical_permissions()
        access = perms[1]["access"]
        perms[1]["access"] = {str(i): a for i, a in enumerate(access)}
        result = self.select([self.robot(permissions=perms)])
        self.assertEqual(result["ok"], False)

    def test_non_numeric_id_fails_closed(self):
        self.assertEqual(self.select([self.robot(id="7")])["reason"], "invalid-id")

    def test_empty_result_set_is_no_match(self):
        self.assertEqual(self.select([]), {"ok": False, "reason": "no-match", "matches": 0})

    def test_selector_never_emits_a_secret_field(self):
        result = self.select([self.robot(secret="should-never-be-echoed")])
        self.assertNotIn("secret", result)


@unittest.skipIf(NU is None or shutil.which("sh") is None, "nu and sh are required")
class RepairShellBehaviourTest(unittest.TestCase):
    """Execute the *composed* in-pod shell helpers against real fixtures.

    Issue #285 second review, finding 2: the shipped header parser was
    `tr -d '\\n' | awk 'tolower($1) == "x-total-count:"'`. Deleting the
    newlines collapses the whole header block onto ONE line, so `$1` is
    `HTTP/2` and the total is always empty -- and an empty total was then
    silently accepted, so a truncated robot list could be trusted. Source
    assertions could not see this; these tests run the real code.
    """

    @classmethod
    def setUpClass(cls):
        composed = run_nu("harbor_credential_repair_script")
        assert composed.returncode == 0, composed.stderr
        cls.script = Path("/tmp/pr287_repair_lib.sh")
        cls.script.write_text(composed.stdout, encoding="utf-8")

    def call(self, func: str, *args: str, stdin: str = "") -> subprocess.CompletedProcess:
        """Source the script in library-only mode and invoke one helper."""
        cmd = f'. "{self.script}"; {func} ' + " ".join(f'"{a}"' for a in args)
        return subprocess.run(
            ["sh", "-c", cmd], input=stdin, text=True, capture_output=True,
            timeout=30, env={"HARBOR_REPAIR_LIB_ONLY": "1", "PATH": "/usr/bin:/bin"},
        )

    def headers(self, body: str) -> str:
        path = Path("/tmp/pr287_headers.txt")
        path.write_text(body, encoding="utf-8")
        return str(path)

    def test_realistic_crlf_header_yields_the_total(self):
        h = self.headers("HTTP/2 200\r\nX-Total-Count: 42\r\n"
                         "content-type: application/json\r\n\r\n")
        result = self.call("parse_total_count", h)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "42")

    def test_lowercase_header_name_is_accepted(self):
        h = self.headers("HTTP/1.1 200 OK\r\nx-total-count: 7\r\n\r\n")
        result = self.call("parse_total_count", h)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "7")

    def test_zero_is_a_valid_total(self):
        h = self.headers("HTTP/1.1 200 OK\r\nX-Total-Count: 0\r\n\r\n")
        result = self.call("parse_total_count", h)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "0")

    def test_missing_total_fails_closed(self):
        h = self.headers("HTTP/2 200\r\ncontent-type: application/json\r\n\r\n")
        self.assertNotEqual(self.call("parse_total_count", h).returncode, 0,
                            "a missing X-Total-Count must never be silently accepted")

    def test_malformed_total_fails_closed(self):
        h = self.headers("HTTP/2 200\r\nX-Total-Count: not-a-number\r\n\r\n")
        self.assertNotEqual(self.call("parse_total_count", h).returncode, 0)

    def test_negative_total_fails_closed(self):
        h = self.headers("HTTP/2 200\r\nX-Total-Count: -1\r\n\r\n")
        self.assertNotEqual(self.call("parse_total_count", h).returncode, 0)

    def test_duplicate_totals_fail_closed(self):
        h = self.headers("HTTP/2 200\r\nX-Total-Count: 5\r\nX-Total-Count: 9\r\n\r\n")
        self.assertNotEqual(self.call("parse_total_count", h).returncode, 0,
                            "an ambiguous header set must never be trusted")

    def test_a_header_value_elsewhere_in_the_block_is_not_mistaken_for_the_total(self):
        h = self.headers("HTTP/2 200\r\nx-request-id: x-total-count-123\r\n"
                         "X-Total-Count: 3\r\n\r\n")
        result = self.call("parse_total_count", h)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "3")

    def test_no_optional_whitespace_form_is_accepted(self):
        """Issue #285 third review finding 4: HTTP allows zero OWS between the
        `:` and the field value (RFC 9110 5.5) -- `awk`'s default whitespace
        field-splitting rejected this syntactically valid form because
        `$2` is empty when there is no space after the colon."""
        h = self.headers("HTTP/2 200\r\nX-Total-Count:42\r\n\r\n")
        result = self.call("parse_total_count", h)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "42")

    def test_trailing_garbage_after_the_number_is_rejected(self):
        """`awk '{print $2}'` silently dropped everything after the first
        field, so `X-Total-Count: 42 garbage` was accepted as `42` -- the
        parser must validate and use the *complete* value after the colon."""
        h = self.headers("HTTP/2 200\r\nX-Total-Count: 42 garbage\r\n\r\n")
        self.assertNotEqual(self.call("parse_total_count", h).returncode, 0,
                            "trailing content after the number must fail closed")


FAKE_CURL = r"""#!/bin/sh
# Behavioural fixture for collect_robots(): serves paginated `GET /robots`
# responses purely from FAKE_TOTAL/FAKE_PAGE_SIZE, so the real pagination
# loop and jq calls in collect_robots() run against real (fake-served) HTTP
# semantics rather than a source-level assertion.
headers=""
body=""
url=""
prev=""
for arg in "$@"; do
  case "$prev" in
    -D) headers="$arg" ;;
    -o) body="$arg" ;;
  esac
  case "$arg" in
    *page=*) url="$arg" ;;
  esac
  prev="$arg"
done
page=$(printf '%s' "$url" | sed -n 's/.*page=\([0-9]*\).*/\1/p')
total="${FAKE_TOTAL:-0}"
page_size="${FAKE_PAGE_SIZE:-100}"
start=$(( (page - 1) * page_size + 1 ))
end=$(( page * page_size ))
if [ "$end" -gt "$total" ]; then end="$total"; fi
if [ "$start" -gt "$end" ]; then
  count=0
else
  count=$(( end - start + 1 ))
fi
{
  i=0
  printf '['
  while [ "$i" -lt "$count" ]; do
    [ "$i" -gt 0 ] && printf ','
    printf '{"id":%d}' "$((start + i))"
    i=$((i + 1))
  done
  printf ']'
} > "$body"
{
  printf 'HTTP/2 200\r\n'
  printf 'X-Total-Count: %s\r\n' "$total"
  printf '\r\n'
} > "$headers"
exit 0
"""


@unittest.skipIf(NU is None, "nushell (nu) is required")
class RecoveryPaginationBoundaryBehaviourTest(unittest.TestCase):
    """Issue #285 third review finding 3: `collect_robots()` (the in-pod
    recovery pagination loop) must accept a result set of exactly
    `MAX_PAGES*PAGE_SIZE = 2000` and fail only strictly above that bound.

    The previous loop treated "this page came back full" as the sole
    continue-signal and "the page counter walked past MAX_PAGES" as the sole
    overflow signal, so a fully-saturated 20th page advanced the counter to
    21 and was rejected as overflow even though all 2000 robots had already
    been collected and reconciled against the authoritative total.

    A fake `curl` on PATH serves real paginated JSON from `$FAKE_TOTAL`, so
    the real shell loop and real `jq` calls in `collect_robots()` are what is
    actually exercised here, not a model of them.
    """

    @classmethod
    def setUpClass(cls):
        composed = run_nu("harbor_credential_repair_script")
        assert composed.returncode == 0, composed.stderr
        cls.script = Path("/tmp/pr287_pagination_lib.sh")
        cls.script.write_text(composed.stdout, encoding="utf-8")
        cls.bindir = Path(tempfile.mkdtemp(prefix="pr287-fakecurl-"))
        curl_path = cls.bindir / "curl"
        curl_path.write_text(FAKE_CURL, encoding="utf-8")
        curl_path.chmod(0o755)

    def collect(self, total: int, page_size: int = 100):
        workdir = Path(tempfile.mkdtemp(prefix="pr287-collect-"))
        # The composed script carries `set -eu` (it is meant to run as a Job
        # entrypoint): calling collect_robots as a plain statement would abort
        # the whole wrapper on a non-zero return before EXIT= could be
        # printed. Using it as an `if` condition is exempt from `set -e`
        # (POSIX), so the real return code is still observable here.
        cmd = (
            f'. "{self.script}"; '
            f'WORKDIR="{workdir}"; ROBOT_NAME="crossplane-system"; '
            f'HARBOR_API="https://harbor.invalid/api/v2.0"; HARBOR_CACERT=/dev/null; '
            f'harbor_auth_cfg=/dev/null; PAGE_SIZE={page_size}; MAX_PAGES=20; '
            f'if collect_robots; then code=0; else code=$?; fi; echo "EXIT=$code"'
        )
        env = {
            "HARBOR_REPAIR_LIB_ONLY": "1",
            "PATH": f"{self.bindir}:/usr/bin:/bin",
            "FAKE_TOTAL": str(total),
            "FAKE_PAGE_SIZE": str(page_size),
        }
        result = subprocess.run(
            ["sh", "-c", cmd], text=True, capture_output=True, timeout=30, env=env,
        )
        exit_code = int(re.search(r"EXIT=(-?\d+)", result.stdout).group(1))
        robots_file = workdir / "robots.json"
        collected = json.loads(robots_file.read_text()) if robots_file.exists() else None
        return exit_code, collected, result.stderr

    def test_exactly_the_page_boundary_is_accepted(self):
        """total == MAX_PAGES*PAGE_SIZE == 2000: every one of the 20 allowed
        pages is full, and the result must still be accepted."""
        exit_code, collected, stderr = self.collect(total=2000)
        self.assertEqual(exit_code, 0, stderr)
        self.assertEqual(len(collected), 2000)

    def test_one_past_the_boundary_is_rejected(self):
        """total == 2001 cannot be collected within MAX_PAGES*PAGE_SIZE and
        must fail closed, strictly above the bound."""
        exit_code, _collected, stderr = self.collect(total=2001)
        self.assertNotEqual(exit_code, 0)
        self.assertIn("exceeded", stderr)

    def test_a_partial_last_page_within_bound_is_accepted(self):
        exit_code, collected, stderr = self.collect(total=1550)
        self.assertEqual(exit_code, 0, stderr)
        self.assertEqual(len(collected), 1550)

    def test_zero_robots_is_accepted(self):
        exit_code, collected, stderr = self.collect(total=0)
        self.assertEqual(exit_code, 0, stderr)
        self.assertEqual(collected, [])

    def test_well_under_one_page_is_accepted(self):
        exit_code, collected, stderr = self.collect(total=7)
        self.assertEqual(exit_code, 0, stderr)
        self.assertEqual(len(collected), 7)


FAKE_IDENTITY_CURL = r"""#!/bin/sh
# Like FAKE_CURL, but every synthesized item carries a real identity, so the
# REAL collect_robots() pagination loop feeds the REAL selector jq filter a
# result set whose canonical robot may sit on any page (CANONICAL_AT is its
# 1-based position in the full, unpaginated result set) or be genuinely
# absent (CANONICAL_AT unset/0) behind any number of decoys. FAKE_MODE
# reproduces the non-identity failure modes a single GET can also see:
# a malformed (non-array) 200 body, or an auth/server error curl -f would
# turn into a nonzero exit with no response body captured.
headers=""
body=""
url=""
prev=""
for arg in "$@"; do
  case "$prev" in
    -D) headers="$arg" ;;
    -o) body="$arg" ;;
  esac
  case "$arg" in
    *page=*) url="$arg" ;;
  esac
  prev="$arg"
done
mode="${FAKE_MODE:-identity}"
case "$mode" in
  malformed)
    printf '{"unexpected":"object"}' > "$body"
    { printf 'HTTP/2 200\r\n'; printf 'X-Total-Count: 5\r\n'; printf '\r\n'; } > "$headers"
    exit 0
    ;;
  http_error)
    : > "$body"
    exit 22
    ;;
esac
page=$(printf '%s' "$url" | sed -n 's/.*page=\([0-9]*\).*/\1/p')
total="${FAKE_TOTAL:-0}"
page_size="${FAKE_PAGE_SIZE:-100}"
canonical_at="${CANONICAL_AT:-0}"
start=$(( (page - 1) * page_size + 1 ))
end=$(( page * page_size ))
if [ "$end" -gt "$total" ]; then end="$total"; fi
if [ "$start" -gt "$end" ]; then
  count=0
else
  count=$(( end - start + 1 ))
fi
{
  i=0
  n=$start
  printf '['
  while [ "$i" -lt "$count" ]; do
    [ "$i" -gt 0 ] && printf ','
    if [ "$n" -eq "$canonical_at" ]; then
      printf '{"id":%d,"name":"robot$crossplane-system","level":"system","permissions":%s}' \
        "$n" "$CANONICAL_PERMISSIONS"
    else
      printf '{"id":%d,"name":"robot$decoy-%d","level":"system","permissions":[]}' "$n" "$n"
    fi
    i=$((i + 1))
    n=$((n + 1))
  done
  printf ']'
} > "$body"
{
  printf 'HTTP/2 200\r\n'
  printf 'X-Total-Count: %s\r\n' "$total"
  printf '\r\n'
} > "$headers"
exit 0
"""


@unittest.skipIf(NU is None or shutil.which("jq") is None, "nu and jq are required")
class RecoveryIdentityAcrossPagesBehaviourTest(unittest.TestCase):
    """Issue #285 third review finding 1: proves the actual convergence
    contract end to end through the REAL collect_robots() pagination loop
    and the REAL selector jq filter together (not a model of either) --
    the exact decision the guarded create-vs-rotate branch is built on.

    * canonical on page 1 -- the common case, must still resolve via rotate.
    * canonical on a later page -- must be found and resolved via rotate,
      never reported no-match (which would wrongly route to create).
    * genuinely absent behind exactly 100 decoys (a saturated single page,
      the declarative OBSERVE's exact blind spot) -- must resolve to
      no-match, so the create path is the one that converges it.
    * a malformed page or an auth/server failure must fail collect_robots
      closed before the selector -- and therefore before either create or
      rotate is ever attempted.
    """

    @classmethod
    def setUpClass(cls):
        composed = run_nu("harbor_credential_repair_script")
        assert composed.returncode == 0, composed.stderr
        cls.script = Path("/tmp/pr287_identity_lib.sh")
        cls.script.write_text(composed.stdout, encoding="utf-8")
        cls.bindir = Path(tempfile.mkdtemp(prefix="pr287-fakeidentitycurl-"))
        curl_path = cls.bindir / "curl"
        curl_path.write_text(FAKE_IDENTITY_CURL, encoding="utf-8")
        curl_path.chmod(0o755)
        cls.expected_permissions = run_nu("harbor_robot_expected_permissions").stdout.strip()

    def collect_and_select(self, total: int, canonical_at: int = 0, page_size: int = 100,
                           mode: str = "identity"):
        workdir = Path(tempfile.mkdtemp(prefix="pr287-identity-collect-"))
        cmd = (
            f'. "{self.script}"; '
            f'WORKDIR="{workdir}"; ROBOT_NAME="crossplane-system"; '
            f'HARBOR_API="https://harbor.invalid/api/v2.0"; HARBOR_CACERT=/dev/null; '
            f'harbor_auth_cfg=/dev/null; PAGE_SIZE={page_size}; MAX_PAGES=20; '
            f'if collect_robots; then code=0; else code=$?; fi; echo "EXIT=$code"'
        )
        env = {
            "HARBOR_REPAIR_LIB_ONLY": "1",
            "PATH": f"{self.bindir}:/usr/bin:/bin",
            "FAKE_TOTAL": str(total),
            "FAKE_PAGE_SIZE": str(page_size),
            "CANONICAL_AT": str(canonical_at),
            "CANONICAL_PERMISSIONS": self.expected_permissions,
            "FAKE_MODE": mode,
        }
        result = subprocess.run(
            ["sh", "-c", cmd], text=True, capture_output=True, timeout=30, env=env,
        )
        exit_code = int(re.search(r"EXIT=(-?\d+)", result.stdout).group(1))
        robots_file = workdir / "robots.json"
        if exit_code != 0 or not robots_file.exists():
            return exit_code, None, result.stderr

        filt = run_nu("harbor_robot_selector_jq")
        assert filt.returncode == 0, filt.stderr
        proc = subprocess.run(
            ["jq", "-c", "--arg", "name", "crossplane-system",
             "--argjson", "expected", self.expected_permissions, filt.stdout],
            input=robots_file.read_text(), text=True, capture_output=True, timeout=30,
        )
        assert proc.returncode == 0, proc.stderr
        return exit_code, json.loads(proc.stdout), result.stderr

    def test_canonical_on_page_one_resolves_to_rotate(self):
        _code, selection, stderr = self.collect_and_select(total=1, canonical_at=1)
        self.assertIsNotNone(selection, stderr)
        self.assertEqual(selection["ok"], True)

    def test_canonical_on_a_later_page_still_resolves_to_rotate(self):
        """The exact review scenario: page 1 (items 1..100) is entirely
        decoys, the canonical robot is item 150 on page 2. A single-GET
        declarative OBSERVE could never see this; the exhaustive scan must."""
        _code, selection, stderr = self.collect_and_select(total=150, canonical_at=150)
        self.assertIsNotNone(selection, stderr)
        self.assertEqual(selection["ok"], True,
                         "the canonical robot on page 2 must still be found, not treated as absent")

    def test_genuinely_absent_behind_exactly_100_decoys_is_no_match(self):
        """A saturated page 1 (100 decoys) with the canonical robot nowhere
        in the (reconciled, complete) result set -- the declarative
        OBSERVE's isRemovedCheck must refuse to conclude removed from this
        alone (test_harbor_bootstrap_crash_safety.py), but the exhaustive
        scan here has actually seen everything and can safely conclude it."""
        _code, selection, stderr = self.collect_and_select(total=100, canonical_at=0)
        self.assertIsNotNone(selection, stderr)
        self.assertEqual(selection["ok"], False)
        self.assertEqual(selection["reason"], "no-match")

    def test_a_malformed_page_fails_closed_before_any_selection(self):
        code, selection, _stderr = self.collect_and_select(total=5, mode="malformed")
        self.assertNotEqual(code, 0)
        self.assertIsNone(selection, "a malformed page must never reach the selector")

    def test_an_auth_or_server_failure_fails_closed_before_any_selection(self):
        code, selection, _stderr = self.collect_and_select(total=5, mode="http_error")
        self.assertNotEqual(code, 0)
        self.assertIsNone(selection, "an auth/server failure must never reach the selector")


class RepairTransactionOrderTest(unittest.TestCase):
    """Issue #285 review findings 1/4/7/8 on the in-pod repair script."""

    @classmethod
    def setUpClass(cls):
        cls.script = func_body("harbor_credential_repair_script")
        cls.job = func_body("harbor_credential_repair_job")

    def _pos(self, needle, msg=""):
        self.assertIn(needle, self.script, msg)
        return self.script.index(needle)

    def test_admin_basic_auth_is_decoded_exactly_once_at_the_api_boundary(self):
        """`.data.value` is Kubernetes' outer base64 of a value that is itself
        already base64(admin:password). Sending it verbatim produced
        `Basic base64(base64(admin:password))` and could only ever 401.

        Exactly one decode may *produce the Basic token that is used*; any
        further decode is validation-only and must be piped straight into a
        matcher, never stored or turned into a header."""
        producing = re.findall(
            r"base64 -d > \"\$harbor_basic_file\"|base64 --decode > \"\$harbor_basic_file\"",
            self.script,
        )
        self.assertEqual(
            len(producing), 1,
            "exactly one decode may produce the Basic token actually used",
        )
        # That decode consumes the Kubernetes-API `.data.value` of the admin Secret.
        self.assertIn('jq -r \'.data.value // empty\' "$admin_auth_response" | base64 -d',
                      self.script)
        # Every other decode is validation-only: piped into a matcher, never redirected.
        for match in re.finditer(r"base64 -d(?! > \"\$harbor_basic_file\")", self.script):
            tail = self.script[match.end():match.end() + 60]
            self.assertRegex(
                tail, r"^\s*(2>/dev/null\s*)?\|\s*grep",
                "a non-producing decode must feed a matcher directly, never a file",
            )

    def test_decoded_basic_token_shape_is_validated_before_use(self):
        # base64 shape, then that it really decodes to admin:<nonempty>.
        self.assertIn("'^[A-Za-z0-9+/]+={0,2}$'", self.script)
        self.assertIn("'^admin:.+$'", self.script)
        basic_validated = self.script.index("'^admin:.+$'")
        header_built = self.script.index('Authorization: Basic')
        self.assertLess(basic_validated, header_built,
                        "validate the decoded token before building the auth header")
        self.assertIn("exit 1", self.script)

    def test_target_secret_and_resource_version_are_read_before_harbor_rotation(self):
        """A rotation is irreversible: Harbor issues a new secret and forgets
        the old one. Reading/validating the write target only afterwards can
        leave Kubernetes holding a permanently invalid credential."""
        read_rv = self._pos("resource_version=")
        rotate = self._pos("-X PATCH")
        self.assertLess(read_rv, rotate,
                        "resourceVersion must be read and validated before RefreshSec")

    def test_resource_version_is_validated_nonempty_and_used_as_precondition(self):
        self.assertIn('"$resource_version"', self.script)
        self.assertIn("resourceVersion", self.script)

    def test_permissions_are_revalidated_immediately_before_rotation(self):
        select_at = self._pos('-f "$selector_file"')
        gate_at = self._pos("refusing to rotate")
        rotate = self._pos("-X PATCH")
        self.assertLess(select_at, rotate,
                        "identity+permission validation must gate the rotation")
        self.assertLess(gate_at, rotate,
                        "a failed selection must abort before Harbor is mutated")
        # The selection is re-done inside the retry loop, not hoisted above it.
        self.assertLess(self.script.index("while [ \"$attempt\""), select_at)

    def test_pagination_is_followed_not_just_the_first_page(self):
        self.assertIn("PAGE_SIZE=100", self.script)
        self.assertIn("page=$page&page_size=$PAGE_SIZE", self.script)
        self.assertIn("X-Total-Count", self.script)
        self.assertIn("MAX_PAGES", self.script)
        # Total from the authoritative header must be reconciled, fail-closed.
        self.assertIn('"$collected_final" -ne "$total"', self.script)

    def test_json_is_parsed_with_a_real_parser_not_grep_and_tr(self):
        self.assertIn("jq ", self.script)
        self.assertNotIn('tr "}"', self.script)
        self.assertNotIn("grep -o \"\\\"id\\\"", self.script)

    def test_repair_job_uses_a_digest_pinned_image_that_provides_jq(self):
        self.assertRegex(self.job, r"natsio/nats-box:[0-9.]+@sha256:[0-9a-f]{64}")

    def test_repair_pod_runs_unprivileged_with_writable_tmpfs_and_readable_mounts(self):
        self.assertIn("runAsNonRoot: true", self.job)
        self.assertIn("runAsUser: 65534", self.job)
        self.assertIn("fsGroup: 65534", self.job)
        self.assertIn("allowPrivilegeEscalation: false", self.job)
        self.assertIn("readOnlyRootFilesystem: true", self.job)
        self.assertIn('drop: ["ALL"]', self.job)
        self.assertIn('medium: "Memory"', self.job)
        self.assertIn("defaultMode:", self.job)

    def test_conflict_triggers_a_bounded_retry_of_the_whole_transaction(self):
        self.assertIn("409", self.script)
        self.assertRegex(self.script, r"attempt|retry")

    def test_readback_equality_is_preserved_on_encoded_values(self):
        for expected, persisted in (
            ("name_b64_file", "post_name_b64_file"),
            ("secret_b64_file", "post_secret_b64_file"),
            ("basic_auth_b64_file", "post_basic_auth_b64_file"),
        ):
            self.assertIn(f'cmp -s "${persisted}" "${expected}"', self.script)

class CredentialGateOrderingTest(unittest.TestCase):
    """provider ready -> bootstrap sync -> credentials ready -> downstream."""

    def test_gate_runs_inside_the_gated_loop_after_the_bootstrap_app_syncs(self):
        loop = func_body("sync_gated_apps_for_local_dev")
        self.assertIn("ensure_crossplane_harbor_credentials", loop)
        self.assertLess(
            loop.index("wait_for_provider_http_ready"),
            loop.index("kubectl patch application $app"),
            "provider-http must be proven ready before any gated sync starts",
        )
        self.assertLess(
            loop.index("kubectl patch application $app"),
            loop.index("ensure_crossplane_harbor_credentials"),
            "the credential gate must run after the bootstrap Application syncs",
        )

    def test_downstream_applications_stay_ordered_behind_the_gate(self):
        loop = func_body("sync_gated_apps_for_local_dev")
        gated = loop[loop.index("let gated_apps") : loop.index("for app in $gated_apps")]
        order = [gated.index(f'"{app}"') for app in
                 ("crossplane-provider-configs", "crossplane-harbor-bootstrap",
                  "crossplane-xrds", "core-catalog")]
        self.assertEqual(order, sorted(order))
        # The gate is bound to the bootstrap Application, so crossplane-xrds and
        # core-catalog (later in the same sequential loop) cannot start until
        # the credential is proven complete.
        self.assertIn(
            'if $app == "crossplane-harbor-bootstrap"',
            loop,
            "the credential gate must key off the bootstrap Application",
        )

    def test_gate_fails_closed_when_the_credential_is_still_incomplete(self):
        gate = func_body("ensure_crossplane_harbor_credentials")
        self.assertIn("error make", gate)
        # The failure must name the contract, and must be raised only after a
        # recovery attempt has been made and re-verified.
        self.assertLess(
            gate.index("repair_harbor_credential_secret"),
            gate.rindex("error make"),
            "recovery must be attempted before the run is failed",
        )
        self.assertLess(
            gate.rindex("probe_harbor_credential_keys"),
            gate.rindex("error make"),
            "the post-recovery state must be re-probed before declaring success",
        )


class CredentialProbeSecurityTest(unittest.TestCase):
    """The completeness check must reveal key names and booleans -- nothing else."""

    def test_probe_reports_presence_per_key_without_reading_values(self):
        script = func_body("harbor_credential_probe_script")
        self.assertIn("-s ", script, "non-emptiness is tested with test -s, not by reading")
        self.assertIn("=true", script)
        self.assertIn("=false", script)
        for leak in ("cat ", "head -c", "od ", "xxd", "base64 -d", "wc -c"):
            self.assertNotIn(
                leak, script, f"probe must never surface credential bytes via {leak!r}"
            )

    def test_probe_pod_projects_the_secret_optionally_and_has_no_api_access(self):
        body = func_body("harbor_credential_probe_job")
        self.assertIn("automountServiceAccountToken: false", body)
        self.assertIn("optional: true", body)
        self.assertIn(CREDENTIAL_SECRET, body)
        for key in REQUIRED_KEYS:
            self.assertIn(f'key: "{key}"', body)
        self.assertIn("readOnly: true", body)
        self.assertIn('restartPolicy: "Never"', body)
        self.assertIn("backoffLimit: 0", body)

    def test_probe_never_reads_secret_data_through_the_api(self):
        for name in ("probe_harbor_credential_keys", "ensure_crossplane_harbor_credentials",
                     "ensure_harbor_credential_secret_shell"):
            body = func_body(name)
            self.assertNotIn(
                "jsonpath='{.data", body, f"{name} must not read Secret data client-side"
            )
            self.assertNotIn(
                "decode base64", body, f"{name} must not decode credential material"
            )

    def test_probe_uses_the_digest_pinned_platform_curl_image(self):
        body = func_body("harbor_credential_probe_job")
        self.assertRegex(body, r"curlimages/curl:[0-9.]+@sha256:[0-9a-f]{64}")

    def test_probe_pod_is_explicitly_hardened_with_a_numeric_uid(self):
        """Issue #285 review finding 8: runtime user must not be inferred from
        the tag. `curlimages/curl` declares a *non-numeric* image user
        (`curl_user`, UID 101), so `runAsNonRoot: true` without an explicit
        numeric `runAsUser` makes the kubelet refuse to start the container
        ("cannot verify user is non-root") -- the gate would then fail on
        every run. The UID is therefore pinned numerically, and the projected
        credential is made group-readable for it via fsGroup."""
        body = func_body("harbor_credential_probe_job")
        self.assertIn("runAsNonRoot: true", body)
        self.assertRegex(body, r"runAsUser: \d+",
                         "a numeric UID is required alongside runAsNonRoot")
        self.assertNotIn("curl_user", body.split("image:")[1] if "image:" in body else "",
                         "never rely on the image's non-numeric user name")
        self.assertIn("fsGroup:", body)
        self.assertIn("allowPrivilegeEscalation: false", body)
        self.assertIn("readOnlyRootFilesystem: true", body)
        self.assertIn('drop: ["ALL"]', body)
        # The projected credential must stay readable under that fsGroup.
        self.assertIn("defaultMode:", body)

    def test_shell_creation_never_writes_or_clobbers_credential_data(self):
        body = func_body("ensure_harbor_credential_secret_shell")
        self.assertIn("--ignore-not-found", body)
        self.assertNotIn(
            "kubectl apply", body,
            "apply would prune provider-owned data keys on a resumed run; only "
            "create the shell when it is genuinely absent",
        )
        self.assertNotIn("--from-literal", body)


@unittest.skipIf(NU is None or shutil.which("sh") is None, "nu and sh are required")
class ContainerCommandLineEndingBehaviourTest(unittest.TestCase):
    """Exercise the generated in-container commands from real LF/CRLF sources.

    Windows Git checkouts preserve CRLF inside Nushell multiline literals.
    The resulting carriage returns must be removed at the production boundary,
    before either script becomes the third ``sh -c`` container argument.
    """

    def generated_commands(self, source_bytes: bytes) -> dict:
        with tempfile.TemporaryDirectory(prefix="pr287-line-endings-") as tmp:
            setup = Path(tmp) / "local-setup.nu"
            setup.write_bytes(source_bytes)
            snippet = (
                f"source {setup.as_posix()}\n"
                "let probe = (harbor_credential_probe_job)\n"
                "let repair = (harbor_credential_repair_job)\n"
                "{probe: $probe.spec.template.spec.containers.0.command.2, "
                "repair: $repair.spec.template.spec.containers.0.command.2} | to json"
            )
            result = subprocess.run(
                [NU, "--no-config-file", "-c", snippet],
                cwd=ROOT, text=True, capture_output=True, timeout=60, check=False,
            )
        self.assertEqual(
            result.returncode, 0,
            f"real Nushell could not source/generate jobs\nSTDERR:\n{result.stderr}",
        )
        return json.loads(result.stdout)

    def assert_command_contract(self, commands: dict) -> None:
        self.assertEqual(set(commands), {"probe", "repair"})
        commands_with_cr = [name for name, script in commands.items() if "\r" in script]
        self.assertEqual(
            commands_with_cr, [],
            f"generated commands retained carriage returns: {commands_with_cr}",
        )
        self.assertTrue(commands["probe"].startswith("#!/bin/sh\nset -e\n"))
        self.assertTrue(commands["repair"].startswith("set -eu\numask 077\n"))
        for name, script in commands.items():
            self.assertIn("\n", script, f"{name} command lost its LF separators")

        probe = subprocess.run(
            ["sh", "-c", commands["probe"]],
            cwd=ROOT, text=True, capture_output=True, timeout=30, check=False,
        )
        self.assertEqual(
            probe.returncode, 0,
            f"generated probe command failed\nSTDERR:\n{probe.stderr}",
        )
        self.assertNotIn("illegal option", probe.stderr.lower())
        self.assertEqual(
            probe.stdout,
            "name=false\nsecret=false\nbasicAuth=false\n",
            "probe stdout must contain only the three key-presence booleans",
        )

    def test_lf_checkout_preserves_generated_command_semantics(self):
        source = SETUP_PATH.read_bytes()
        self.assertNotIn(b"\r", source, "repository fixture is expected to be LF-only")
        self.assert_command_contract(self.generated_commands(source))

    def test_pure_normalizer_handles_crlf_lone_cr_and_lf(self):
        result = run_nu(
            r'normalize_posix_container_script "one\r\ntwo\rthree\n" | to json'
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), "one\ntwo\nthree\n")

    def test_crlf_checkout_normalizes_both_generated_container_commands(self):
        source = SETUP_PATH.read_bytes()
        crlf_source = source.replace(b"\n", b"\r\n")
        self.assertIn(b"\r\n", crlf_source)
        self.assert_command_contract(self.generated_commands(crlf_source))


class RobotSecretRecoveryTest(unittest.TestCase):
    """Recovery refreshes only an existing, uniquely selected robot whose
    identity and complete permission set match the locked contract. Absence is
    handled by the declarative Request's CREATE lifecycle; ambiguous, malformed
    or permission-mismatched selections remain fail-closed and are never used
    for credential rotation."""

    def test_recovery_uses_harbor_refresh_sec_for_an_existing_robot(self):
        script = func_body("harbor_credential_repair_script")
        self.assertIn("-X PATCH", script)
        self.assertIn("/robots/", script)
        self.assertNotIn("-X DELETE", script)
        self.assertNotIn("-X PUT", script)

    def test_recovery_preserves_least_privilege_by_sending_no_permissions_on_rotate(self):
        script = func_body("harbor_credential_repair_script")
        # goharbor v2.15.1 RefreshSec: an empty body makes Harbor generate the
        # secret itself (robot.CreateSec) and return it; the robot's stored
        # permission set is never rewritten. Permissions are *read* and
        # compared before rotating (review finding 6), but the refresh request
        # body itself must stay exactly `{}` so nothing can be granted here.
        refresh_body = re.search(r'printf "\{\}" > "\$refresh_body"', script)
        self.assertIsNotNone(refresh_body, "the RefreshSec body must be exactly {}")
        body_flag = re.search(r'--data-binary @"\$refresh_body"', script)
        self.assertIsNotNone(body_flag)

    def test_recovery_resolves_the_robot_by_identity_and_fails_closed_on_ambiguity(self):
        script = func_body("harbor_credential_repair_script")
        self.assertIn('ROBOT_NAME="crossplane-system"', script)
        self.assertIn("Name=$ROBOT_NAME", script)
        self.assertNotIn("Name=~$ROBOT_NAME", script)
        self.assertIn('--arg name "$ROBOT_NAME"', script)
        self.assertIn("refusing to rotate", script,
                      "an ambiguous or unexpected match must abort before rotating")
        self.assertIn("exit 1", script)

    def test_recovery_validates_the_refreshed_secret_before_persisting_it(self):
        script = func_body("harbor_credential_repair_script")
        # goharbor v2.15.1 CreateSec -> utils.GenerateRandomStringWithLen(32)
        # over [a-zA-Z0-9], retried until IsValidSec (8..128, upper+lower+digit).
        self.assertRegex(script, r"A-Za-z0-9\]\{8,128\}")

    def test_credential_material_never_reaches_argv_stdout_or_a_host_file(self):
        script = func_body("harbor_credential_repair_script")
        job = func_body("harbor_credential_repair_job")
        # Harbor and Kubernetes credentials are passed by curl config file /
        # request-body file, never as command-line arguments.
        self.assertIn("--config ", script)
        self.assertNotIn(" -u ", script)
        self.assertNotIn("Authorization: Basic $", script)
        self.assertNotIn("Authorization: Bearer $", script)
        self.assertIn("--data-binary @", script)
        self.assertNotIn("-d \"", script)
        # Encoded Secret fields are still credential material. They must never
        # be expanded through jq --arg (visible in /proc/<pid>/cmdline); jq may
        # receive only paths and read the values from tmpfs-backed files.
        for var in ("name_b64", "secret_b64", "basic_auth_b64"):
            self.assertNotRegex(
                script,
                rf'--arg\s+\w+\s+"\${var}"',
                f"{var} must not be passed through process argv",
            )
        for flag in (
            '--rawfile n "$name_b64_file"',
            '--rawfile s "$secret_b64_file"',
            '--rawfile b "$basic_auth_b64_file"',
        ):
            self.assertIn(flag, script)
        # Anything holding credential material lives in a memory-backed volume.
        self.assertIn('medium: "Memory"', job)
        self.assertIn("umask 077", script)
        # Nothing is echoed but status keys.
        self.assertNotIn("echo \"$", script)
        for name in ("repair_harbor_credential_secret", "harbor_credential_repair_job"):
            body = func_body(name)
            self.assertNotIn("mktemp", body)
            self.assertNotIn("save ", body)

    def test_recovery_does_not_bypass_tls(self):
        script = func_body("harbor_credential_repair_script")
        self.assertIn("--cacert", script)
        for bypass in (" -k ", "--insecure", "-sSk"):
            self.assertNotIn(bypass, script)
        self.assertIn("https://digiorg.local/api/v2.0", script)
        self.assertIn("https://kubernetes.default.svc", script)

    def test_no_boundary_function_uses_dev_stdin(self):
        for name in BOUNDARY_FUNCS:
            self.assertNotIn(
                "/dev/stdin", func_body(name), f"{name} must stay Windows/Nushell portable"
            )

    def test_recovery_rbac_is_least_privilege_and_removed_afterwards(self):
        rbac = func_body("harbor_credential_recovery_rbac")
        self.assertIn('resourceNames: ["crossplane-harbor-credentials"]', rbac)
        self.assertIn('resourceNames: ["harbor-admin-basic-auth"]', rbac)
        self.assertIn('verbs: ["patch"]', rbac)
        self.assertIn('verbs: ["get"]', rbac)
        for forbidden in ("ClusterRole", '"list"', '"watch"', '"delete"', '"*"'):
            self.assertNotIn(forbidden, rbac, f"recovery RBAC must not grant {forbidden}")
        repair = func_body("repair_harbor_credential_secret")
        teardown = func_body("harbor_recovery_final_teardown")
        self.assertIn("harbor_credential_recovery_rbac", repair)
        # The actual removal now lives in the delegated teardown, which
        # `repair` hands the manifest to -- not an inline delete of its own.
        self.assertIn("(harbor_recovery_final_teardown", repair)
        self.assertIn("kubectl delete", teardown)

    def test_recovery_verifies_the_persisted_secret_matches_byte_for_byte(self):
        """A 200 from the PATCH proves the API accepted the request, not that
        the stored bytes are what was sent. The script must read the Secret
        back a second time and compare its data.name/data.secret/data.basicAuth
        fields against the exact base64 values it generated -- as opaque
        base64 text, never decoded -- and fail closed on any mismatch."""
        script = func_body("harbor_credential_repair_script")
        self.assertIn(
            'TARGET_SECRET_URL="$K8S_API/api/v1/namespaces/crossplane-system'
            '/secrets/crossplane-harbor-credentials"', script,
        )
        self.assertGreaterEqual(
            script.count('"$TARGET_SECRET_URL"'), 3,
            "the target Secret must be read once for the pre-patch "
            "resourceVersion, written once via PATCH, and read again "
            "afterwards to verify what was actually persisted",
        )
        for expected, persisted in (
            ("name_b64_file", "post_name_b64_file"),
            ("secret_b64_file", "post_secret_b64_file"),
            ("basic_auth_b64_file", "post_basic_auth_b64_file"),
        ):
            self.assertIn(
                f'cmp -s "${persisted}" "${expected}"', script,
                f"the post-patch readback must compare {expected} byte-for-byte",
            )
        # The comparison must run on the still-encoded base64 text: no decode
        # may appear anywhere at or after the readback. (Exactly one decode is
        # allowed earlier, at the harbor-admin-basic-auth API boundary -- see
        # RepairTransactionOrderTest.)
        readback_at = script.index("post_patch_secret")
        self.assertNotIn("decode base64", script)
        for decode in ("base64 -d", "base64 --decode", "base64 -D"):
            self.assertNotIn(
                decode, script[readback_at:],
                "target Secret equality must compare opaque base64, never decode it",
            )

    def test_repair_removes_job_and_rbac_on_every_failure_path_not_just_success(self):
        """Issue #285 review finding: `error make` inside
        ensure_harbor_credential_secret_shell (or a job apply/wait failure)
        used to abort the function immediately, skipping the Job/RBAC
        teardown at the bottom entirely -- a failed recovery attempt left a
        ServiceAccount/Role/RoleBinding and a completed/failed Job on the
        cluster forever. Every risky step must be caught, and the Job + RBAC
        cleanup must run unconditionally afterwards, exactly once, regardless
        of which step failed (or whether none did)."""
        repair = func_body("repair_harbor_credential_secret")
        self.assertIn("try {", repair)
        self.assertIn("catch", repair)

        try_at = repair.index("try {")
        catch_at = repair.index("catch", try_at)

        # Every risky step lives inside the try block, so its failure is
        # caught rather than propagating past the cleanup below.
        for risky in ("kubectl apply -f -",
                      "ensure_harbor_credential_secret_shell",
                      "harbor_credential_repair_job",
                      "run_bootstrap_job"):
            risky_at = repair.index(risky)
            self.assertTrue(
                try_at < risky_at < catch_at,
                f"{risky} must run inside the try block so a failure there is caught",
            )

        # The cleanup itself lives strictly after the try/catch expression,
        # not inside either branch -- so it always runs exactly once. It is
        # now delegated to `harbor_recovery_final_teardown`, found by its
        # exact executable call site, not a comment mentioning its name.
        last_catch_brace = repair.index("}", repair.index("{|err|", catch_at))
        cleanup_region = repair[last_catch_brace:]
        self.assertIn("(harbor_recovery_final_teardown", cleanup_region,
                      "the outer recovery cleanup must be delegated to the shared teardown")
        self.assertEqual(
            cleanup_region.count("(harbor_recovery_final_teardown"), 1,
            "the delegated teardown must run exactly once, not be duplicated across branches",
        )
        self.assertNotIn("kubectl delete job harbor-credential-repair", cleanup_region)
        self.assertNotIn("kubectl delete -f -", cleanup_region,
                         "the RBAC/Job/Pod cleanup mechanics live in the teardown, not inlined here")

        teardown = func_body("harbor_recovery_final_teardown")
        self.assertIn('cleanup_bootstrap_job_verified "harbor-credential-repair"', teardown)
        self.assertIn("kubectl delete -f -", teardown)
        self.assertEqual(
            teardown.count('cleanup_bootstrap_job_verified "harbor-credential-repair"'), 1,
            "the checked Job+Pod cleanup must run exactly once inside the teardown",
        )


class BootstrapJobLifecycleTest(unittest.TestCase):
    """Issue #285 review finding 3: a fixed-name Job must never be trusted.

    The probe ignored the result of its pre-delete and then used `kubectl
    apply`. If the delete failed (or was still terminating), `apply` was a
    no-op against a *stale completed* Job -- `kubectl wait` was instantly
    satisfied by the old Complete condition and `kubectl logs` returned the
    previous run's report. A stale "all keys present" report would then let
    an incomplete credential through the gate.
    """

    @classmethod
    def setUpClass(cls):
        cls.probe = func_body("probe_harbor_credential_keys")
        cls.repair = func_body("repair_harbor_credential_secret")
        cls.runner = func_body("run_bootstrap_job")
        cls.cleanup = func_body("cleanup_bootstrap_job_verified")
        cls.fail_after_cleanup = func_body("fail_bootstrap_job_after_cleanup")

    def test_a_shared_runner_owns_job_lifecycle_for_probe_and_repair(self):
        self.assertIn("run_bootstrap_job", self.probe)
        self.assertIn("run_bootstrap_job", self.repair)
        self.assertIn(
            'run_bootstrap_job (harbor_credential_probe_job) '
            '"harbor-credential-probe" "60s"',
            self.probe,
        )
        self.assertIn(
            'run_bootstrap_job (harbor_credential_repair_job) '
            '"harbor-credential-repair" "180s"',
            self.repair,
        )

    def test_startup_has_a_distinct_300_second_budget_before_functional_wait(self):
        self.assertIn("startup_timeout?: duration", self.runner)
        self.assertIn("$startup_timeout | default 300sec", self.runner)
        startup_list_at = self.runner.index("let startup_pods_result")
        wait_at = self.runner.index("kubectl wait --for=condition=Complete")
        self.assertLess(startup_list_at, wait_at)
        self.assertIn('$"--timeout=($timeout)"', self.runner[wait_at:])

    def test_pre_delete_failure_is_fatal_not_ignored(self):
        self.assertIn("cleanup_bootstrap_job_verified", self.runner)
        pre_at = self.runner.index("let pre_cleanup")
        after = self.runner[pre_at:]
        self.assertIn("not $pre_cleanup.ok", after)
        self.assertIn("error make", after,
                      "a failed pre-delete must abort, never fall through to apply")
        # And the shared helper's own delete is itself checked, not discarded.
        self.assertIn("kubectl delete job", self.cleanup)
        self.assertIn("exit_code != 0", self.cleanup)

    def test_cleanup_verifies_absence_of_both_the_job_and_its_pods(self):
        """Issue #285 third review finding 2: a delete returning 0 was
        previously trusted outright. The probe Job mounts the credential
        Secret, so a Pod that survives past a "successful" delete (e.g.
        still Terminating) is the dangerous case -- both the Job and its
        Pods must be positively re-observed absent."""
        self.assertIn('kubectl get job', self.cleanup)
        self.assertIn('--ignore-not-found', self.cleanup)
        job_absence_at = self.cleanup.index("kubectl get job")
        self.assertIn("is-empty", self.cleanup[job_absence_at:])
        self.assertIn("get_crossplane_system_pod_list", self.cleanup)
        pods_absence_at = self.cleanup.index("get_crossplane_system_pod_list")
        self.assertIn("is-empty", self.cleanup[pods_absence_at:])
        self.assertLess(job_absence_at, pods_absence_at)

    def test_a_cleanup_failure_is_itself_fatal_not_swallowed(self):
        self.assertIn("return {ok: false", self.cleanup)
        self.assertIn("not $cleanup.ok", self.fail_after_cleanup)
        self.assertIn("error make", self.fail_after_cleanup)

    def test_absence_is_verified_before_creating_the_new_job(self):
        absence_at = self.cleanup.index("is-empty")
        pre_cleanup_call_at = self.runner.index("let pre_cleanup")
        create_at = self.runner.index("kubectl create")
        self.assertLess(pre_cleanup_call_at, create_at,
                        "the old Job's checked-and-verified removal must run before creating the new one")
        # The verification itself lives inside the shared helper, invoked above.
        self.assertLess(absence_at, len(self.cleanup))

    def test_create_is_used_so_a_surviving_job_cannot_be_silently_reused(self):
        self.assertIn("kubectl create -f -", self.runner)
        self.assertNotIn("kubectl apply -f -", self.runner,
                         "apply would silently accept a surviving stale Job")

    def test_every_failure_path_after_create_routes_through_checked_cleanup(self):
        """Issue #285 third review finding 2: the Job UID fetch/validation
        step in particular used to have NO cleanup call on failure at all --
        only wait/list/selection/logs did, and even those discarded the
        cleanup's own result. Every failure branch after `create` must now
        go through the single checked-and-verified helper."""
        self.assertNotIn(
            'kubectl delete job $job_name -n crossplane-system --ignore-not-found } | complete)\n    error make',
            self.runner,
            "no failure path may call kubectl delete directly and discard its result",
        )
        for needle in (
            "Failed to create the",
            "Failed to parse the identity",
            "returned an invalid name or UID",
            "did not complete successfully",
            "Failed to list the pods",
            "Refusing to trust the",
            "identity changed before reading logs",
            "before reading logs",
            "Failed to read the",
            "identity changed after reading logs",
            "after reading logs",
        ):
            self.assertIn(needle, self.runner, f"missing expected failure message: {needle!r}")
        self.assertEqual(
            self.runner.count("fail_bootstrap_job_after_cleanup"), 20,
            "all 20 create/startup/identity/wait/pod/job/log failure branches "
            "must route through checked cleanup",
        )

    def test_create_returns_the_job_uid_atomically_without_a_name_based_reread(self):
        self.assertIn("kubectl create -f - -o json", self.runner)
        create_at = self.runner.index("kubectl create -f - -o json")
        parse_at = self.runner.index("$create_result.stdout | from json", create_at)
        self.assertLess(create_at, parse_at)
        between = self.runner[create_at:parse_at]
        self.assertNotIn("kubectl get job", between)
        self.assertIn("metadata.uid", self.runner[parse_at:])
        self.assertIn("metadata.name", self.runner[parse_at:])

    def test_logs_are_read_by_pod_identity_not_by_re_resolving_the_job_name(self):
        """`kubectl logs job/<name>` resolves the fixed name a second time, so
        a Job replaced after the UID check returns another Job's logs."""
        self.assertIn("metadata.uid", self.runner)
        self.assertIn("select_owned_pod", self.runner)
        self.assertNotIn("kubectl logs $\"job/", self.runner,
                         "never re-resolve the Job name to fetch logs")
        # Match the real invocation, not prose in the surrounding comment.
        select_at = self.runner.index("(select_owned_pod ")
        logs_at = self.runner.index("kubectl logs $owned.name")
        self.assertLess(select_at, logs_at,
                        "the owning pod must be resolved before logs are read")
        self.assertIn("fail_bootstrap_job_after_cleanup", self.runner[select_at:logs_at],
                      "a rejected pod selection must abort (via checked cleanup) before logs are read")

    def test_logs_are_additionally_bound_to_the_pods_immutable_uid(self):
        """The pod UID is checked both immediately before and immediately
        after log retrieval. A same-name replacement in either race window
        invalidates the captured logs before they can be returned."""
        relist_at = self.runner.index("let relist_result")
        reowned_at = self.runner.index("let reowned")
        logs_at = self.runner.index("kubectl logs $owned.name")
        post_relist_at = self.runner.index("let post_log_relist_result")
        post_reowned_at = self.runner.index("let post_log_reowned")
        self.assertLess(relist_at, reowned_at)
        self.assertLess(reowned_at, logs_at)
        self.assertLess(logs_at, post_relist_at)
        self.assertLess(post_relist_at, post_reowned_at)
        self.assertIn("$reowned.uid != $owned.uid", self.runner)
        self.assertIn("$post_log_reowned.uid != $owned.uid", self.runner)
        self.assertIn("$post_log_reowned.name != $owned.name", self.runner)
        pre_job_at = self.runner.index("let pre_log_job_result")
        post_job_at = self.runner.index("let post_log_job_result")
        self.assertLess(pre_job_at, logs_at)
        self.assertGreater(post_job_at, logs_at)
        self.assertIn("job_identity_matches", self.runner[pre_job_at:logs_at])
        self.assertIn("job_identity_matches", self.runner[post_job_at:])

    def test_recovery_final_cleanup_reuses_checked_job_and_pod_absence_verification(self):
        teardown = func_body("harbor_recovery_final_teardown")
        leftovers = func_body("harbor_recovery_privilege_leftovers")
        self.assertIn(
            'cleanup_bootstrap_job_verified "harbor-credential-repair"', teardown,
            "outer recovery cleanup must use the shared checked Job+Pod cleanup",
        )
        self.assertNotIn("kubectl delete job harbor-credential-repair", teardown)
        self.assertIn("get_crossplane_system_pod_list", leftovers)
        self.assertNotIn('--selector "job-name=harbor-credential-repair"', leftovers)
        self.assertIn("serviceAccountName", leftovers)
        self.assertIn("ownerReferences", leftovers)

    def test_runner_fails_closed_on_create_and_wait_errors(self):
        for needle in ("kubectl create", "kubectl wait"):
            at = self.runner.index(needle)
            self.assertIn("fail_bootstrap_job_after_cleanup", self.runner[at:at + 700],
                          f"{needle} failure must fail closed via the checked-cleanup helper")

    def test_success_path_also_verifies_cleanup_afterward(self):
        """A stale probe Pod left behind after an otherwise-successful run is
        just as dangerous as one left behind after a failure -- it still
        mounts the credential Secret."""
        logs_at = self.runner.rindex("kubectl logs $owned.name")
        tail = self.runner[logs_at:]
        self.assertIn("cleanup_bootstrap_job_verified", tail)
        self.assertIn("not $cleanup.ok", tail)
        self.assertIn("error make", tail)


class TypedCorePodListRequestContractTest(unittest.TestCase):
    """Runtime-v11: kubectl's table printer can wrap Pods in kind=List.

    Every security boundary must bypass discovery/output negotiation and use
    the fixed typed Core API endpoint. The strict parser remains responsible
    for rejecting anything except a real v1/PodList with well-formed Pods.
    """

    SECURITY_CRITICAL_CALLERS = {
        "cleanup_bootstrap_job_verified": 1,
        "run_bootstrap_job": 3,
        "harbor_recovery_delete_leftover_pods": 1,
        "harbor_recovery_privilege_leftovers": 1,
    }

    def test_exactly_six_security_critical_fetches_route_through_one_helper(self):
        self.assertEqual(sum(self.SECURITY_CRITICAL_CALLERS.values()), 6)
        for function, expected_calls in self.SECURITY_CRITICAL_CALLERS.items():
            body = func_body(function)
            self.assertEqual(
                body.count("get_crossplane_system_pod_list"),
                expected_calls,
                f"{function} must route all {expected_calls} PodList fetches through the typed helper",
            )

    def test_helper_uses_exact_fixed_typed_core_api_request_and_captures_result(self):
        helper = func_body("get_crossplane_system_pod_list")
        kubectl_lines = [
            line.strip() for line in helper.splitlines()
            if line.strip().startswith("kubectl ")
        ]
        self.assertEqual(
            kubectl_lines,
            [f'kubectl get --raw "{POD_LIST_RAW_PATH}"'],
        )
        self.assertIn("| complete", helper)
        self.assertNotIn("$", POD_LIST_RAW_PATH, "the path must contain no interpolation")

    def test_no_legacy_table_negotiated_pod_list_remains_in_security_paths(self):
        for function in self.SECURITY_CRITICAL_CALLERS:
            body = func_body(function)
            self.assertNotIn("kubectl get pods -n crossplane-system -o json", body)


@unittest.skipIf(NU is None, "nushell (nu) is required")
class LiveEvidencePodListParserContractTest(unittest.TestCase):
    def parse(self, payload: dict) -> dict:
        encoded = json.dumps(payload).replace("\\", "\\\\").replace('"', '\\"')
        result = run_nu(f'parse_pod_list "{encoded}" | to json')
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_runtime_v11_generic_list_is_rejected_but_typed_podlist_passes(self):
        pod = {
            "metadata": {"name": "crossplane-0", "uid": "pod-uid", "labels": {}},
            "spec": {"serviceAccountName": "crossplane"},
        }
        generic = self.parse({"apiVersion": "v1", "kind": "List", "items": [pod]})
        typed = self.parse({"apiVersion": "v1", "kind": "PodList", "items": [pod]})
        self.assertFalse(generic["ok"], "generic kind=List must remain rejected")
        self.assertEqual(generic["reason"], "malformed PodList")
        self.assertTrue(typed["ok"], typed["reason"])


FAKE_KUBECTL = r"""#!/bin/sh
# Behavioural fixture for run_bootstrap_job(): a fake `kubectl` that logs
# every invocation to $KLOG and lets $FAIL_STEP make exactly one step fail,
# so cleanup-on-failure and pod-identity-rebinding behaviour is exercised
# for real (real Nushell `do {...} | complete`, real select_owned_pod jq-
# style filtering of real JSON) rather than modelled via source assertions.
LOG="${KLOG:?KLOG must be set}"
FAIL="${FAIL_STEP:-none}"
UID_VALUE="FAKEUID0001"
args="$*"
printf '%s\n' "$args" >> "$LOG"

count_file() {
  f="$LOG.$1"
  n=$(( $(cat "$f" 2>/dev/null || echo 0) + 1 ))
  echo "$n" > "$f"
  echo "$n"
}

verb="$1"
case "$verb" in
  delete)
    n=$(count_file deletecount)
    if [ "$FAIL" = "pre-delete-fails" ] && [ "$n" -eq 1 ]; then
      echo "boom: delete failed" >&2
      exit 1
    fi
    if [ "$FAIL" = "final-delete-fails" ] && [ "$n" -eq 2 ]; then
      echo "boom: delete failed" >&2
      exit 1
    fi
    exit 0
    ;;
  create)
    cat >/dev/null
    if [ "$FAIL" = "create-missing-uid" ]; then
      printf '{"apiVersion":"batch/v1","kind":"Job","metadata":{"name":"pr287-test-job"}}'
    else
      printf '{"apiVersion":"batch/v1","kind":"Job","metadata":{"name":"pr287-test-job","uid":"%s"}}' "$UID_VALUE"
    fi
    exit 0
    ;;
  wait)
    if [ "$FAIL" = "wait" ]; then
      echo "boom: wait failed" >&2
      exit 1
    fi
    exit 0
    ;;
  logs)
    if [ "$FAIL" = "logs" ]; then
      echo "boom: logs failed" >&2
      exit 1
    fi
    printf 'name=true\nsecret=true\nbasicAuth=true\n'
    exit 0
    ;;
  get)
    resource="$2"
    if [ "$resource" = "job" ]; then
      if printf '%s' "$args" | grep -q -- '-o name'; then
        n=$(count_file jobnamecount)
        if [ "$FAIL" = "job-lingers" ] && [ "$n" -eq 2 ]; then
          printf 'job.batch/%s\n' "$3"
        else
          printf ''
        fi
        exit 0
      fi
      if printf '%s' "$args" | grep -q -- '-o json'; then
        n=$(count_file jobjsoncount)
        job_uid="$UID_VALUE"
        if [ "$FAIL" = "job-replaced-before-logs" ]; then
          job_uid="REPLACEMENT-JOB-UID"
        fi
        if [ "$FAIL" = "job-replaced-after-logs" ] && [ "$n" -ge 2 ]; then
          job_uid="REPLACEMENT-JOB-UID"
        fi
        printf '{"apiVersion":"batch/v1","kind":"Job","metadata":{"name":"pr287-test-job","uid":"%s"}}' "$job_uid"
        exit 0
      fi
      exit 0
    fi
    if [ "$resource" = "--raw" ] && [ "$3" = "/api/v1/namespaces/crossplane-system/pods" ]; then
        deletes=$(cat "$LOG.deletecount" 2>/dev/null || echo 0)
        if [ "$deletes" -ge 2 ]; then
          if [ "$FAIL" = "pod-lingers" ]; then
            printf '{"apiVersion":"v1","kind":"PodList","items":[{"metadata":{"name":"job-pod-1","uid":"POD-UID-STABLE","labels":{},"ownerReferences":[{"kind":"Job","name":"pr287-test-job","uid":"%s","controller":true}]},"spec":{"serviceAccountName":"harbor-credential-recovery"}}]}' "$UID_VALUE"
          elif [ "$FAIL" = "tracked-pod-survives" ]; then
            printf '{"apiVersion":"v1","kind":"PodList","items":[{"metadata":{"name":"job-pod-1","uid":"POD-UID-STABLE","labels":{}},"spec":{"serviceAccountName":"default"},"status":{"phase":"Failed"}}]}'
          elif [ "$FAIL" = "tracked-job-owner-survives" ]; then
            printf '{"apiVersion":"v1","kind":"PodList","items":[{"metadata":{"name":"unrelated-name","uid":"UNRELATED-POD-UID","labels":{},"ownerReferences":[{"kind":"Job","name":"other-job","uid":"%s","controller":true}]},"spec":{"serviceAccountName":"default"},"status":{"phase":"Failed"}}]}' "$UID_VALUE"
          else
            printf '{"apiVersion":"v1","kind":"PodList","items":[]}'
          fi
          exit 0
        fi
        # The first namespace-wide pod list belongs to pre-create cleanup.
        if [ "$deletes" -eq 1 ] && [ ! -f "$LOG.runtimepods" ]; then
          : > "$LOG.runtimepods"
          case "$FAIL" in
            cleanup-missing-items) printf '{}' ;;
            cleanup-wrong-kind) printf '{"apiVersion":"v1","kind":"List","items":[]}' ;;
            cleanup-nonlist-items) printf '{"apiVersion":"v1","kind":"PodList","items":{}}' ;;
            *) printf '{"apiVersion":"v1","kind":"PodList","items":[]}' ;;
          esac
          exit 0
        fi
        if [ "$FAIL" = "list-pods" ]; then
          echo "boom: list pods failed" >&2
          exit 1
        fi
        n=$(count_file listcount)
        pod_uid="POD-UID-STABLE"
        if [ "$FAIL" = "pod-replaced" ] && [ "$n" -ge 2 ]; then
          pod_uid="POD-UID-REPLACED"
        fi
        if [ "$FAIL" = "pod-replaced-after-logs" ] && [ "$n" -ge 3 ]; then
          pod_uid="POD-UID-REPLACED-AFTER-LOGS"
        fi
        phase="Succeeded"
        case "$FAIL" in
          startup-missing-running)
            if [ "$n" -eq 1 ]; then
              printf '{"apiVersion":"v1","kind":"PodList","items":[]}'
              exit 0
            fi
            phase="Running"
            ;;
          startup-pending-running)
            if [ "$n" -le 2 ]; then phase="Pending"; else phase="Running"; fi
            ;;
          startup-never)
            phase="Pending"
            ;;
          startup-failed|tracked-pod-survives|tracked-job-owner-survives)
            phase="Failed"
            ;;
          startup-missing-status)
            printf '{"apiVersion":"v1","kind":"PodList","items":[{"metadata":{"name":"job-pod-1","uid":"%s","labels":{"job-name":"pr287-test-job"},"ownerReferences":[{"kind":"Job","name":"pr287-test-job","uid":"%s","controller":true}]},"spec":{"serviceAccountName":"harbor-credential-recovery","containers":[{"name":"probe"}]}}]}' "$pod_uid" "$UID_VALUE"
            exit 0
            ;;
          startup-malformed-phase)
            phase="Unknown"
            ;;
          startup-nonstring-phase)
            printf '{"apiVersion":"v1","kind":"PodList","items":[{"metadata":{"name":"job-pod-1","uid":"%s","labels":{"job-name":"pr287-test-job"},"ownerReferences":[{"kind":"Job","name":"pr287-test-job","uid":"%s","controller":true}]},"spec":{"serviceAccountName":"harbor-credential-recovery","containers":[{"name":"probe"}]},"status":{"phase":{"bad":true}}}]}' "$pod_uid" "$UID_VALUE"
            exit 0
            ;;
          startup-malformed-workload)
            printf '{"apiVersion":"v1","kind":"PodList","items":[{"metadata":{"name":"job-pod-1","uid":"%s","labels":{"job-name":"pr287-test-job"},"ownerReferences":[{"kind":"Job","name":"pr287-test-job","uid":"%s","controller":true}]},"spec":{"serviceAccountName":"harbor-credential-recovery","containers":[]},"status":{"phase":"Running"}}]}' "$pod_uid" "$UID_VALUE"
            exit 0
            ;;
        esac
        printf '{"apiVersion":"v1","kind":"PodList","items":[{"metadata":{"name":"job-pod-1","uid":"%s","labels":{"job-name":"pr287-test-job"},"ownerReferences":[{"kind":"Job","name":"pr287-test-job","uid":"%s","controller":true}]},"spec":{"serviceAccountName":"harbor-credential-recovery","containers":[{"name":"probe"}]},"status":{"phase":"%s"}}]}' "$pod_uid" "$UID_VALUE" "$phase"
        exit 0
    fi
    exit 0
    ;;
  *)
    exit 0
    ;;
esac
"""


@unittest.skipIf(NU is None, "nushell (nu) is required")
class RunBootstrapJobCleanupBehaviourTest(unittest.TestCase):
    """Issue #285 third review finding 2: real, end-to-end behavioural proof
    (against a fake `kubectl` on PATH, not source assertions) that:

    * a failure at the Job UID fetch/validation step -- previously the one
      path with NO cleanup call at all -- now triggers checked deletion,
    * a pod replaced (same name, different UID) between selection and the
      log read is detected and its logs are never read,
    * every other failure path (wait/list/select/logs) still cleans up, and
    * a cleanup call that itself fails is fatal, not swallowed, on both the
      pre-run and the post-success paths.
    """

    @classmethod
    def setUpClass(cls):
        cls.bindir = Path(tempfile.mkdtemp(prefix="pr287-fakekubectl-"))
        kubectl_path = cls.bindir / "kubectl"
        kubectl_path.write_text(FAKE_KUBECTL, encoding="utf-8")
        kubectl_path.chmod(0o755)

    def run_job(
        self,
        fail_step: str = "none",
        functional_timeout: str = "5s",
        startup_timeout: str | None = None,
    ):
        log_path = Path(tempfile.mkdtemp(prefix="pr287-klog-")) / "calls.log"
        env = {
            "PATH": f"{self.bindir}:/usr/bin:/bin",
            "KLOG": str(log_path),
            "FAIL_STEP": fail_step,
        }
        startup_arg = f' {startup_timeout}' if startup_timeout is not None else ""
        result = subprocess.run(
            [NU, "--no-config-file", "-c",
             f'source {SETUP_PATH.as_posix()}\n'
             f'run_bootstrap_job {{fake: "manifest"}} "pr287-test-job" '
             f'"{functional_timeout}"{startup_arg}'],
            cwd=ROOT, text=True, capture_output=True, timeout=30, env=env,
        )
        calls = log_path.read_text().splitlines() if log_path.exists() else []
        return result, calls

    def _runtime_raw_calls_before_wait(self, calls: list[str]) -> list[str]:
        create_at = next(i for i, call in enumerate(calls) if call.startswith("create "))
        wait_at = next(i for i, call in enumerate(calls) if call.startswith("wait "))
        return [
            call for call in calls[create_at + 1:wait_at]
            if call.startswith(
                "get --raw /api/v1/namespaces/crossplane-system/pods"
            )
        ]

    def test_delayed_pending_acquisition_precedes_the_full_functional_wait(self):
        """Two one-second Pending observations exceed this 1s functional
        budget. The functional clock must nevertheless start only after the
        Pod reaches Running, and its original argument must be unchanged."""
        result, calls = self.run_job(
            "startup-pending-running", functional_timeout="1s",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertGreaterEqual(len(self._runtime_raw_calls_before_wait(calls)), 3)
        wait = next(call for call in calls if call.startswith("wait "))
        self.assertIn("--timeout=1s", wait)

    def test_probe_functional_timeout_remains_exactly_60s_after_startup(self):
        result, calls = self.run_job(
            "startup-pending-running", functional_timeout="60s",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        wait = next(call for call in calls if call.startswith("wait "))
        self.assertIn("--timeout=60s", wait)
        self.assertGreaterEqual(len(self._runtime_raw_calls_before_wait(calls)), 3)

    def test_missing_pod_is_pending_until_one_owned_pod_is_running(self):
        result, calls = self.run_job("startup-missing-running")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertGreaterEqual(len(self._runtime_raw_calls_before_wait(calls)), 2)

    def test_an_instantly_succeeded_short_job_is_accepted(self):
        result, calls = self.run_job("none")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self._runtime_raw_calls_before_wait(calls)), 1)

    def test_missing_or_unknown_pod_phase_fails_closed_and_cleans(self):
        for scenario in (
            "startup-missing-status",
            "startup-malformed-phase",
            "startup-nonstring-phase",
            "startup-malformed-workload",
        ):
            with self.subTest(scenario=scenario):
                result, calls = self.run_job(scenario)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(sum(c.startswith("wait ") for c in calls), 0)
                self.assertGreaterEqual(sum(c.startswith("delete job") for c in calls), 2)

    def test_startup_budget_expiry_cleans_without_starting_functional_wait(self):
        result, calls = self.run_job("startup-never", startup_timeout="20ms")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(sum(c.startswith("wait ") for c in calls), 0)
        self.assertGreaterEqual(sum(c.startswith("delete job") for c in calls), 2)

    def test_failed_pod_fails_promptly_and_cleans(self):
        result, calls = self.run_job("startup-failed")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(sum(c.startswith("wait ") for c in calls), 0)
        self.assertGreaterEqual(sum(c.startswith("delete job") for c in calls), 2)

    def test_cleanup_receives_the_discovered_pod_identity(self):
        result, calls = self.run_job("tracked-pod-survives")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("additionally, cleanup failed", result.stderr)
        self.assertIn("traceable", result.stderr)
        self.assertEqual(sum(c.startswith("wait ") for c in calls), 0)

    def test_cleanup_receives_the_created_job_uid_after_pod_discovery(self):
        result, calls = self.run_job("tracked-job-owner-survives")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("additionally, cleanup failed", result.stderr)
        self.assertIn("traceable", result.stderr)
        self.assertEqual(sum(c.startswith("wait ") for c in calls), 0)

    def test_malformed_podlist_cleanup_aborts_before_create(self):
        for scenario in ("cleanup-missing-items", "cleanup-wrong-kind", "cleanup-nonlist-items"):
            with self.subTest(scenario=scenario):
                result, calls = self.run_job(scenario)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(sum(1 for c in calls if c.startswith("create ")), 0)

    def test_the_happy_path_returns_logs_and_cleans_up_afterward(self):
        result, calls = self.run_job("none")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("secret=true", result.stdout)
        self.assertGreaterEqual(sum(1 for c in calls if c.startswith("delete job")), 2,
                                "must delete both before starting and after finishing")
        self.assertEqual(sum(1 for c in calls if c.startswith("logs ")), 1)

    def test_an_invalid_atomic_create_response_triggers_cleanup(self):
        """The create response itself is the identity boundary; a missing UID
        must fail and still route through checked cleanup."""
        result, calls = self.run_job("create-missing-uid")
        self.assertNotEqual(result.returncode, 0)
        delete_calls = [c for c in calls if c.startswith("delete job")]
        self.assertGreaterEqual(len(delete_calls), 2)
        self.assertEqual(sum(1 for c in calls if c.startswith("logs ")), 0)

    def test_a_wait_failure_triggers_cleanup(self):
        result, calls = self.run_job("wait")
        self.assertNotEqual(result.returncode, 0)
        self.assertGreaterEqual(sum(1 for c in calls if c.startswith("delete job")), 2)

    def test_a_pod_list_failure_triggers_cleanup(self):
        result, calls = self.run_job("list-pods")
        self.assertNotEqual(result.returncode, 0)
        self.assertGreaterEqual(sum(1 for c in calls if c.startswith("delete job")), 2)

    def test_a_job_replaced_before_logs_is_detected_without_reading_logs(self):
        result, calls = self.run_job("job-replaced-before-logs")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Job identity changed before reading logs", result.stderr)
        self.assertEqual(sum(1 for c in calls if c.startswith("logs ")), 0)
        self.assertGreaterEqual(sum(1 for c in calls if c.startswith("delete job")), 2)

    def test_a_job_replaced_during_log_retrieval_invalidates_captured_logs(self):
        result, calls = self.run_job("job-replaced-after-logs")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Job identity changed after reading logs", result.stderr)
        self.assertEqual(sum(1 for c in calls if c.startswith("logs ")), 1)
        self.assertNotIn("secret=true", result.stdout)
        self.assertGreaterEqual(sum(1 for c in calls if c.startswith("delete job")), 2)

    def test_a_pod_replaced_between_selection_and_logs_is_detected_and_never_read(self):
        """The exact race finding 2 calls out: the pod selected for logs is
        replaced (by name) before `kubectl logs` runs. The re-verification
        by UID must catch this and logs must never be fetched."""
        result, calls = self.run_job("pod-replaced")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("re-verified by name, owner and", result.stderr)
        self.assertEqual(sum(1 for c in calls if c.startswith("logs ")), 0,
                         "a replaced pod's logs must never be read")
        self.assertGreaterEqual(sum(1 for c in calls if c.startswith("delete job")), 2,
                                "the run must still clean up after detecting the replacement")

    def test_a_pod_replaced_during_log_retrieval_invalidates_captured_logs(self):
        result, calls = self.run_job("pod-replaced-after-logs")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(sum(1 for c in calls if c.startswith("logs ")), 1,
                         "the race occurs after the one log retrieval")
        self.assertNotIn("secret=true", result.stdout,
                         "captured logs must be discarded when post-read UID verification fails")
        self.assertIn("after reading logs", result.stderr)
        self.assertGreaterEqual(sum(1 for c in calls if c.startswith("delete job")), 2)

    def test_a_logs_failure_triggers_cleanup(self):
        result, calls = self.run_job("logs")
        self.assertNotEqual(result.returncode, 0)
        self.assertGreaterEqual(sum(1 for c in calls if c.startswith("delete job")), 2)

    def test_pre_run_cleanup_failure_aborts_before_ever_creating_a_job(self):
        result, calls = self.run_job("pre-delete-fails")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(sum(1 for c in calls if c.startswith("create")), 0,
                         "a failed pre-run cleanup must never proceed to create a new Job")

    def test_a_job_lingering_after_the_final_delete_is_fatal_even_on_success(self):
        """Logs were read successfully, but the Job somehow survived its own
        deletion -- Issue #285 third review finding 2 requires this to fail
        the run, not report success with a stale Job (and its Secret-mounting
        Pod) left on the cluster."""
        result, calls = self.run_job("job-lingers")
        self.assertNotEqual(
            result.returncode, 0,
            "a Job that lingers after the post-success delete must fail the run",
        )
        self.assertIn("cleanup afterward failed", result.stderr)

    def test_a_pod_lingering_after_the_final_delete_is_fatal_even_on_success(self):
        result, calls = self.run_job("pod-lingers")
        self.assertNotEqual(
            result.returncode, 0,
            "a Pod that lingers after the post-success delete must fail the run "
            "-- it may still mount the credential Secret",
        )
        self.assertIn("cleanup afterward failed", result.stderr)

    def test_a_final_delete_call_failure_is_itself_fatal(self):
        result, calls = self.run_job("final-delete-fails")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("cleanup afterward failed", result.stderr)


@unittest.skipIf(NU is None, "nushell (nu) is required")
class StaleOwnerRelabelledPodSurvivorTest(unittest.TestCase):
    """PR#287 independent review finding 1: `cleanup_bootstrap_job_verified`
    only ever treated a Pod's controller Job owner as a survivor signal when
    that owner's UID equalled the *currently tracked* UID, and otherwise fell
    back to the mutable `job-name` label. A Pod that is still controller-owned
    by a Job named after this exact fixed job_name -- but carrying an OLD
    owner UID from an earlier incarnation of that same Job -- and that has
    since been relabelled (so its `job-name` label no longer matches) escapes
    detection both:

    * pre-cleanup, when `run_bootstrap_job` calls this with no tracked
      identity at all (job_uid/pod_name/pod_uid all empty), and
    * post-cleanup, when this run's own freshly-created Job/Pod identity is
      tracked -- the leftover's OLD owner UID can never equal the NEW one.

    Since the fixed job_name can only ever belong to one logical Job across
    incarnations, any controller Job owner reference naming it is itself
    sufficient proof of survivorship, independent of UID.
    """

    @classmethod
    def setUpClass(cls):
        cls.bindir = Path(tempfile.mkdtemp(prefix="pr287-staleowner-kubectl-"))
        fake = cls.bindir / "kubectl"
        fake.write_text(
            "#!/bin/sh\n"
            "if [ \"$1\" = delete ]; then exit 0; fi\n"
            "if [ \"$1\" = get ] && [ \"$2\" = job ]; then printf ''; exit 0; fi\n"
            "if [ \"$1\" = get ] && [ \"$2\" = --raw ] && [ \"$3\" = /api/v1/namespaces/crossplane-system/pods ]; then printf '%s' \"$PODS_JSON\"; exit 0; fi\n"
            "exit 0\n",
            encoding="utf-8",
        )
        fake.chmod(0o755)

    def cleanup(self, job_name: str, job_uid: str = "", pod_name: str = "",
                pod_uid: str = "", pods_json: str = "") -> dict:
        env = {"PATH": f"{self.bindir}:/usr/bin:/bin", "PODS_JSON": pods_json}
        args = " ".join(f'"{a}"' for a in (job_name, job_uid, pod_name, pod_uid))
        result = subprocess.run(
            [NU, "--no-config-file", "-c",
             f"source {SETUP_PATH.as_posix()}\n"
             f"cleanup_bootstrap_job_verified {args} | to json"],
            cwd=ROOT, text=True, capture_output=True, timeout=30, env=env,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def _relabelled_old_owner_pod(self, job_name: str, old_uid: str) -> str:
        pods = {
            "apiVersion": "v1", "kind": "PodList",
            "items": [{
                "metadata": {
                    "name": "leftover-from-earlier-incarnation",
                    "uid": "leftover-pod-uid",
                    # Relabelled away from job-name=<job_name>: the mutable
                    # label fallback alone must not be relied on.
                    "labels": {"job-name": "some-other-value"},
                    "ownerReferences": [{
                        "kind": "Job", "name": job_name, "uid": old_uid, "controller": True,
                    }],
                },
                "spec": {},
            }],
        }
        return json.dumps(pods)

    def test_relabelled_old_owner_pod_is_a_survivor_before_any_uid_is_tracked(self):
        pods_json = self._relabelled_old_owner_pod("harbor-credential-probe", "OLD-JOB-UID")
        result = self.cleanup("harbor-credential-probe", "", "", "", pods_json)
        self.assertFalse(
            result["ok"],
            "a Pod still controller-owned by a Job named after this fixed "
            "job_name must be treated as a survivor even before this run has "
            "created its own Job (no tracked UID yet) and even though its "
            "label has changed",
        )

    def test_relabelled_old_owner_pod_is_a_survivor_after_this_runs_own_job_exists(self):
        pods_json = self._relabelled_old_owner_pod("harbor-credential-probe", "OLD-JOB-UID")
        result = self.cleanup(
            "harbor-credential-probe",
            "NEW-JOB-UID", "this-runs-pod", "this-runs-pod-uid",
            pods_json,
        )
        self.assertFalse(
            result["ok"],
            "a Pod owned by an earlier incarnation of this fixed-name Job "
            "must still be treated as a survivor after this run's own "
            "Job/Pod identity is tracked, even though neither UID matches it",
        )

    def test_an_unrelated_job_name_is_not_treated_as_a_survivor(self):
        pods_json = self._relabelled_old_owner_pod("some-completely-different-job", "OLD-JOB-UID")
        result = self.cleanup("harbor-credential-probe", "", "", "", pods_json)
        self.assertTrue(result["ok"])


class RecoveryPrivilegeCleanupTest(unittest.TestCase):
    """Issue #285 review finding 5: temporary privilege must always be revoked.

    The RBAC multi-document apply sat *outside* the try block. A partial apply
    (ServiceAccount + Role created, RoleBinding rejected) aborted the function
    with privileged objects already on the cluster and no cleanup. Delete
    results were also discarded, so success could be printed while a
    ServiceAccount able to read the Harbor admin credential survived.
    """

    RBAC_OBJECTS = (
        ("serviceaccount", "harbor-credential-recovery", "crossplane-system"),
        ("role", "harbor-credential-recovery", "crossplane-system"),
        ("rolebinding", "harbor-credential-recovery", "crossplane-system"),
        ("role", "harbor-credential-recovery-admin-secret", "harbor"),
        ("rolebinding", "harbor-credential-recovery-admin-secret", "harbor"),
    )

    @classmethod
    def setUpClass(cls):
        cls.repair = func_body("repair_harbor_credential_secret")
        cls.teardown = func_body("harbor_recovery_final_teardown")
        cls.verify = func_body("harbor_recovery_privilege_leftovers")

    def test_rbac_apply_is_inside_the_guarded_region(self):
        try_at = self.repair.index("try {")
        apply_at = self.repair.index("kubectl apply -f -")
        self.assertLess(try_at, apply_at,
                        "a partial RBAC apply must still reach cleanup")

    def test_cleanup_runs_after_the_guarded_region_exactly_once(self):
        # The actual delete mechanics now live in the delegated teardown...
        cleanup_at = self.teardown.index("kubectl delete -f -")
        self.assertEqual(self.teardown.count("kubectl delete -f -"), 1)
        # ...and `repair` itself only ever delegates to it once, after the
        # guarded region -- it must not inline a second, competing cleanup.
        self.assertNotIn("kubectl delete -f -", self.repair)
        delegate_at = self.repair.index("(harbor_recovery_final_teardown")
        self.assertGreater(delegate_at, self.repair.index("catch"))
        self.assertEqual(self.repair.count("(harbor_recovery_final_teardown"), 1)

    def test_cleanup_delete_results_are_checked_not_discarded(self):
        cleanup_at = self.teardown.index("kubectl delete -f -")
        tail = self.teardown[cleanup_at:]
        self.assertIn("exit_code", tail, "delete results must be inspected")

    def test_absence_of_every_privileged_object_is_enumerated(self):
        for kind, name, namespace in self.RBAC_OBJECTS:
            self.assertIn(kind, self.verify)
            self.assertIn(name, self.verify)
            self.assertIn(namespace, self.verify)

    def test_cleanup_verification_is_not_skipped_when_recovery_itself_fails(self):
        """Issue #285 second review finding 3: the original failure used to be
        rethrown *before* the cleanup exit codes and the absence check ran, so
        a failed recovery could leave a ServiceAccount able to read the Harbor
        admin credential -- unverified and unreported."""
        # The guarded region legitimately raises inside `try` (those are
        # caught). What must not exist is a *rethrow* of the original error
        # ahead of the delegated teardown -- that was the defect. Find the
        # exact executable delegation call, not a comment mentioning it.
        self.assertNotIn(
            "error make {msg: $outcome.error}", self.repair,
            "the original error must not be rethrown before cleanup is verified",
        )
        catch_at = self.repair.index("catch")
        delegate_at = self.repair.index("(harbor_recovery_final_teardown")
        final_raise = self.repair.rindex("error make")
        self.assertLess(catch_at, delegate_at,
                        "the teardown must be delegated to after the guarded region, on every path")
        self.assertLess(delegate_at, final_raise,
                        "the single rethrow must carry the combined verdict, computed after teardown")
        self.assertIn("$verdict.msg", self.repair[final_raise:])

        # Inside the delegated teardown itself: leftovers are positively
        # re-verified after every cleanup attempt, and feed the combined
        # verdict -- not skipped just because the recovery itself failed.
        leftovers_at = self.teardown.index("harbor_recovery_privilege_leftovers")
        verdict_at = self.teardown.index("harbor_recovery_cleanup_verdict")
        self.assertLess(leftovers_at, verdict_at,
                        "leftovers must be enumerated before the combined verdict is computed")

    def test_original_failure_is_preserved_and_carries_no_credential_material(self):
        # `repair` hands the whole outcome record to the delegated teardown...
        self.assertIn("$outcome", self.repair,
                      "the outcome must be handed to the delegated teardown")
        self.assertIn("(harbor_recovery_final_teardown", self.repair)
        self.assertIn("error make", self.repair)
        # ...which is where the original error actually feeds the verdict.
        self.assertIn("$outcome.error", self.teardown,
                      "the original outcome error must feed the combined verdict")
        # The error path must not echo Job logs or Secret contents anywhere.
        self.assertNotIn("$logs", self.repair)
        self.assertNotIn("$logs", self.teardown)


class RecoveryResumePreflightOrderingTest(unittest.TestCase):
    """PR#287 independent review finding 2: `repair_harbor_credential_secret`
    (re)applied the recovery RBAC as its very first action, before checking
    whether an earlier crashed run's Job, Pod or RBAC grant was actually
    gone. Kubernetes RBAC is authorized live at request time, not baked into
    a Pod at start -- so a Pod that survived an earlier crash and still
    mounts the harbor-credential-recovery ServiceAccount token would regain
    (or simply retain) the ability to read the Harbor admin credential the
    instant this function re-granted it, before `run_bootstrap_job`'s own
    pre-cleanup ever ran. A resume preflight must revoke/delete any stale
    recovery RBAC and Pods and positively verify their absence BEFORE any
    fresh RBAC is applied, and must itself fail closed.
    """

    @classmethod
    def setUpClass(cls):
        cls.repair = func_body("repair_harbor_credential_secret")

    def test_a_resume_preflight_runs_before_rbac_is_applied(self):
        preflight_at = self.repair.index("harbor_recovery_resume_preflight")
        apply_at = self.repair.index("kubectl apply -f -")
        self.assertLess(
            preflight_at, apply_at,
            "stale recovery privilege must be revoked and verified absent "
            "before any fresh RBAC is granted",
        )

    def test_a_failed_preflight_aborts_before_the_rbac_apply(self):
        preflight_at = self.repair.index("harbor_recovery_resume_preflight")
        apply_at = self.repair.index("kubectl apply -f -")
        between = self.repair[preflight_at:apply_at]
        self.assertIn("not $preflight.ok", between)
        self.assertIn(
            "error make", between,
            "a preflight failure must abort before granting fresh RBAC, not merely warn",
        )

    def test_preflight_runs_inside_the_guarded_region(self):
        """So a preflight failure is still caught and folded into the same
        combined verdict/cleanup as every other failure path."""
        try_at = self.repair.index("try {")
        catch_at = self.repair.index("catch", try_at)
        preflight_at = self.repair.index("harbor_recovery_resume_preflight")
        self.assertTrue(try_at < preflight_at < catch_at)


class RecoveryResumePreflightRbacFirstOrderingTest(unittest.TestCase):
    """PR#287 independent review (round 2): inside
    `harbor_recovery_resume_preflight` itself, the stale recovery RBAC must be
    revoked BEFORE any stale Job or Pod cleanup is attempted -- not after.
    Kubernetes authorizes every request live against whatever RBAC state
    currently exists; it is never baked into a Pod's token at start. A Pod
    that survived an earlier crash and still mounts the
    harbor-credential-recovery ServiceAccount keeps (or regains) the ability
    to read the Harbor admin credential for as long as its Role/RoleBinding
    still exist on the cluster -- independent of whether or when that Pod
    itself gets deleted. An ordering that deletes the Job/Pod first and only
    revokes RBAC afterwards leaves that surviving Pod fully privileged for
    the entire span of the cleanup window, which is exactly the exposure this
    preflight exists to close.
    """

    @classmethod
    def setUpClass(cls):
        cls.preflight = func_body("harbor_recovery_resume_preflight")

    def test_rbac_is_revoked_before_the_stale_job_is_cleaned_up(self):
        rbac_at = self.preflight.index("kubectl delete -f -")
        job_at = self.preflight.index("cleanup_bootstrap_job_verified")
        self.assertLess(
            rbac_at, job_at,
            "stale recovery RBAC must be revoked before the stale "
            "harbor-credential-repair Job is cleaned up, not after",
        )

    def test_rbac_is_revoked_before_the_stale_pods_are_cleaned_up(self):
        rbac_at = self.preflight.index("kubectl delete -f -")
        pod_at = self.preflight.index("harbor_recovery_delete_leftover_pods")
        self.assertLess(
            rbac_at, pod_at,
            "a surviving stale Pod must never retain recovery RBAC while its "
            "own cleanup is still in progress -- RBAC must already be gone",
        )

    def test_rbac_delete_result_is_checked_before_any_cleanup_proceeds(self):
        rbac_at = self.preflight.index("kubectl delete -f -")
        job_at = self.preflight.index("cleanup_bootstrap_job_verified")
        between = self.preflight[rbac_at:job_at]
        self.assertIn(
            "exit_code", between,
            "the RBAC delete result must be checked (fail closed) before "
            "any Job or Pod cleanup is allowed to run",
        )

    def test_positive_absence_verification_still_runs_last(self):
        pod_at = self.preflight.index("harbor_recovery_delete_leftover_pods")
        verify_at = self.preflight.index("harbor_recovery_privilege_leftovers")
        self.assertLess(
            pod_at, verify_at,
            "final positive absence verification must remain the last step",
        )


@unittest.skipIf(NU is None, "nushell (nu) is required")
class RecoveryResumePreflightBehaviourTest(unittest.TestCase):
    """Real, executed behaviour of `harbor_recovery_resume_preflight` against
    a fake `kubectl`: stale Pods matched by recovery ServiceAccount or by
    Job owner name `harbor-credential-repair` are deleted (checked, not
    fire-and-forget), the recovery RBAC is deleted, and every one of those
    resources is positively re-verified absent -- any failure anywhere in
    that sequence is fail-closed, never a warning.
    """

    FAKE_KUBECTL = r"""#!/bin/sh
LOG="${KLOG:?KLOG must be set}"
printf '%s\n' "$*" >> "$LOG"
verb="$1"; shift
case "$verb" in
  delete)
    kind="$1"
    case "$kind" in
      pod)
        name="$2"
        if [ "$DELETE_POD_FAILS" = "$name" ]; then
          echo "boom: delete pod $name failed" >&2
          exit 1
        fi
        printf '%s\n' "$name" >> "$LOG.deletedpods"
        exit 0
        ;;
      job)
        jobname="$2"
        # Real `--cascade=foreground --wait=true` blocks until owned Pods are
        # actually gone, so simulate that cascade here too.
        names=$(printf '%s' "$PODS_JSON" | jq -r --arg jn "$jobname" \
          '.items[] | select((.metadata.ownerReferences // []) | any(.kind=="Job" and .name==$jn)) | .metadata.name')
        if [ -n "$names" ]; then
          printf '%s\n' "$names" >> "$LOG.deletedpods"
        fi
        exit 0
        ;;
      -f)
        cat >/dev/null
        if [ "$RBAC_DELETE_FAILS" = "true" ]; then
          echo "boom: rbac delete failed" >&2
          exit 1
        fi
        : > "$LOG.rbacdeleted"
        exit 0
        ;;
      *)
        exit 0
        ;;
    esac
    ;;
  get)
    kind="$1"
    if [ "$kind" = "job" ]; then
      printf ''
      exit 0
    fi
    if [ "$kind" = "--raw" ] && [ "$2" = "/api/v1/namespaces/crossplane-system/pods" ]; then
      filtered="$PODS_JSON"
      if [ -f "$LOG.deletedpods" ]; then
        while IFS= read -r name; do
          [ -z "$name" ] && continue
          filtered=$(printf '%s' "$filtered" | jq --arg n "$name" '.items |= map(select(.metadata.name != $n))')
        done < "$LOG.deletedpods"
      fi
      printf '%s' "$filtered"
      exit 0
    fi
    name="$2"
    if [ -n "$LEFTOVER_PRESENT_NAME" ] && [ "$name" = "$LEFTOVER_PRESENT_NAME" ]; then
      printf '%s/%s\n' "$kind" "$name"
      exit 0
    fi
    if [ -f "$LOG.rbacdeleted" ]; then
      printf ''
    else
      printf '%s/%s\n' "$kind" "$name"
    fi
    exit 0
    ;;
  *)
    exit 0
    ;;
esac
"""

    @classmethod
    def setUpClass(cls):
        cls.bindir = Path(tempfile.mkdtemp(prefix="pr287-preflight-kubectl-"))
        fake = cls.bindir / "kubectl"
        fake.write_text(cls.FAKE_KUBECTL, encoding="utf-8")
        fake.chmod(0o755)

    def run_preflight(self, pods_json: str, **extra_env) -> tuple[dict, list[str], Path]:
        log_path = Path(tempfile.mkdtemp(prefix="pr287-preflight-log-")) / "calls.log"
        env = {
            "PATH": f"{self.bindir}:/usr/bin:/bin",
            "KLOG": str(log_path),
            "PODS_JSON": pods_json,
            **extra_env,
        }
        result = subprocess.run(
            [NU, "--no-config-file", "-c",
             f"source {SETUP_PATH.as_posix()}\n"
             "harbor_recovery_resume_preflight | to json"],
            cwd=ROOT, text=True, capture_output=True, timeout=30, env=env,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = log_path.read_text().splitlines() if log_path.exists() else []
        return json.loads(result.stdout), calls, log_path

    def _empty_pods(self) -> str:
        return json.dumps({"apiVersion": "v1", "kind": "PodList", "items": []})

    def _pod(self, name: str, service_account: str = "default", owner_name: str = "") -> dict:
        pod = {"metadata": {"name": name, "uid": f"uid-{name}"}, "spec": {"serviceAccountName": service_account}}
        if owner_name:
            pod["metadata"]["ownerReferences"] = [
                {"kind": "Job", "name": owner_name, "uid": "some-uid", "controller": True}
            ]
        return pod

    def test_clean_state_succeeds_and_still_revokes_rbac(self):
        result, calls, _ = self.run_preflight(self._empty_pods())
        self.assertTrue(result["ok"], result.get("reason"))
        self.assertTrue(any(c.startswith("delete -f -") for c in calls),
                        "stale RBAC must be revoked even when nothing else is present")

    def test_a_pod_identified_only_by_the_recovery_service_account_is_deleted(self):
        pods = {"apiVersion": "v1", "kind": "PodList",
                "items": [self._pod("orphan-recovery-pod", service_account="harbor-credential-recovery")]}
        result, calls, _ = self.run_preflight(json.dumps(pods))
        self.assertTrue(result["ok"], result.get("reason"))
        self.assertTrue(
            any(c.startswith("delete pod orphan-recovery-pod") for c in calls),
            "a Pod using the recovery ServiceAccount must be deleted even without a matching Job owner",
        )

    def test_a_pod_owned_by_the_repair_job_is_removed_via_the_jobs_own_cascade_delete(self):
        """A Pod owned by the fixed-name repair Job is cleaned up as part of
        the Job's own foreground-cascade delete (the same shared
        `cleanup_bootstrap_job_verified` boundary Finding 1 hardened) --
        the preflight must still end with it verifiably gone."""
        pods = {"apiVersion": "v1", "kind": "PodList",
                "items": [self._pod("owned-leftover", owner_name="harbor-credential-repair")]}
        result, calls, _ = self.run_preflight(json.dumps(pods))
        self.assertTrue(result["ok"], result.get("reason"))
        self.assertTrue(any(c.startswith("delete job harbor-credential-repair") for c in calls))

    def test_a_pod_that_fails_to_delete_is_fatal_after_rbac_was_already_revoked(self):
        """RBAC is revoked FIRST, before any Job/Pod cleanup, so a Pod that
        cannot be deleted is never left privileged while this preflight is
        still trying to clean it up -- its RBAC is already gone by the time
        the delete is even attempted."""
        pods = {"apiVersion": "v1", "kind": "PodList",
                "items": [self._pod("stubborn-pod", service_account="harbor-credential-recovery")]}
        result, calls, _ = self.run_preflight(json.dumps(pods), DELETE_POD_FAILS="stubborn-pod")
        self.assertFalse(result["ok"])
        self.assertIn("stubborn-pod", result["reason"])
        rbac_calls = [i for i, c in enumerate(calls) if c.startswith("delete -f -")]
        pod_calls = [i for i, c in enumerate(calls) if c.startswith("delete pod stubborn-pod")]
        self.assertTrue(
            rbac_calls,
            "old recovery RBAC must already have been revoked by the time a "
            "stale Pod delete is attempted",
        )
        self.assertTrue(pod_calls, "the stale pod delete must still have been attempted")
        self.assertLess(
            rbac_calls[0], pod_calls[0],
            "RBAC revocation must happen before the stale Pod delete is even "
            "attempted, so this Pod is unprivileged for the entire cleanup window",
        )

    def test_an_rbac_delete_failure_is_fatal(self):
        result, calls, _ = self.run_preflight(self._empty_pods(), RBAC_DELETE_FAILS="true")
        self.assertFalse(result["ok"])
        self.assertIn("RBAC", result["reason"].replace("rbac", "RBAC"))

    def test_an_rbac_delete_failure_blocks_all_further_cleanup(self):
        """RBAC revocation is the first mutating action in this preflight, so
        its failure must abort before any Job or Pod cleanup ever runs -- not
        merely before a fresh RBAC apply."""
        pods = {"apiVersion": "v1", "kind": "PodList",
                "items": [self._pod("some-pod", service_account="harbor-credential-recovery")]}
        result, calls, _ = self.run_preflight(json.dumps(pods), RBAC_DELETE_FAILS="true")
        self.assertFalse(result["ok"])
        self.assertFalse(
            any(c.startswith("delete job") or c.startswith("delete pod") for c in calls),
            "a fatal RBAC delete must abort before any Job or Pod cleanup is attempted",
        )

    def test_a_leftover_privilege_object_surviving_deletion_is_fatal(self):
        """Even after the delete calls succeed, absence must be positively
        re-verified -- a stuck finalizer or a delete that silently no-ops
        must not be trusted."""
        result, calls, _ = self.run_preflight(
            self._empty_pods(), LEFTOVER_PRESENT_NAME="harbor-credential-recovery",
        )
        self.assertFalse(result["ok"])
        self.assertIn("harbor-credential-recovery", result["reason"])

    def test_malformed_consumed_pod_leaves_fail_closed_at_resume_preflight(self):
        """PR#287 independent review (round 8): record/container validation
        alone is insufficient. Kubernetes API leaves consumed by cleanup and
        identity predicates must also have their declared scalar shapes."""
        valid = {
            "metadata": {
                "name": "ordinary-pod",
                "uid": "uid-ordinary-pod",
                "labels": {"job-name": "ordinary-job"},
                "ownerReferences": [{
                    "kind": "Job",
                    "name": "ordinary-job",
                    "uid": "uid-ordinary-job",
                    "controller": True,
                }],
            },
            "spec": {"serviceAccountName": "default"},
        }
        mutations = {
            "metadata.name object": ("metadata", "name", {"not": "a string"}),
            "metadata.name empty": ("metadata", "name", ""),
            "metadata.uid object": ("metadata", "uid", {"not": "a string"}),
            "metadata.uid empty": ("metadata", "uid", ""),
            "serviceAccountName object": ("spec", "serviceAccountName", {"not": "a string"}),
            "job-name label object": ("metadata", "labels", {"job-name": {"not": "a string"}}),
            "owner kind object": ("metadata", "ownerReferences", [{
                "kind": {"not": "a string"}, "name": "ordinary-job",
                "uid": "uid-ordinary-job", "controller": True,
            }]),
            "owner kind empty": ("metadata", "ownerReferences", [{
                "kind": "", "name": "ordinary-job",
                "uid": "uid-ordinary-job", "controller": True,
            }]),
            "owner name object": ("metadata", "ownerReferences", [{
                "kind": "Job", "name": {"not": "a string"},
                "uid": "uid-ordinary-job", "controller": True,
            }]),
            "owner name empty": ("metadata", "ownerReferences", [{
                "kind": "Job", "name": "",
                "uid": "uid-ordinary-job", "controller": True,
            }]),
            "owner uid object": ("metadata", "ownerReferences", [{
                "kind": "Job", "name": "ordinary-job",
                "uid": {"not": "a string"}, "controller": True,
            }]),
            "owner uid empty": ("metadata", "ownerReferences", [{
                "kind": "Job", "name": "ordinary-job",
                "uid": "", "controller": True,
            }]),
            "owner controller string": ("metadata", "ownerReferences", [{
                "kind": "Job", "name": "ordinary-job",
                "uid": "uid-ordinary-job", "controller": "true",
            }]),
        }
        for label, (section, leaf, malformed) in mutations.items():
            with self.subTest(leaf=label):
                pod = json.loads(json.dumps(valid))
                if section == "metadata" and leaf in ("labels", "ownerReferences"):
                    pod["metadata"][leaf] = malformed
                else:
                    pod[section][leaf] = malformed
                pods = {"apiVersion": "v1", "kind": "PodList", "items": [pod]}
                result, _calls, _ = self.run_preflight(json.dumps(pods))
                self.assertFalse(result["ok"], f"{label} must never pass preflight as clean")

    def test_pod_field_presence_and_null_matrix_at_resume_preflight(self):
        """PR#287 independent review (round 9): optional Pod fields may be
        omitted, but explicit JSON null is malformed; metadata/spec containers
        required by the cleanup predicates must exist as records."""
        canonical = {
            "metadata": {
                "name": "ordinary-pod",
                "uid": "uid-ordinary-pod",
                "labels": {"job-name": "ordinary-job"},
                "ownerReferences": [{
                    "kind": "Job",
                    "name": "ordinary-job",
                    "uid": "uid-ordinary-job",
                    "controller": True,
                }],
            },
            "spec": {"serviceAccountName": "default"},
        }
        cases = {
            "canonical Pod": (canonical, True),
            "ownerReferences absent": ({
                "metadata": {
                    "name": "ordinary-pod",
                    "uid": "uid-ordinary-pod",
                    "labels": {"job-name": "ordinary-job"},
                },
                "spec": {"serviceAccountName": "default"},
            }, True),
            "ownerReferences null": ({
                "metadata": {
                    "name": "ordinary-pod",
                    "uid": "uid-ordinary-pod",
                    "labels": {"job-name": "ordinary-job"},
                    "ownerReferences": None,
                },
                "spec": {"serviceAccountName": "default"},
            }, False),
            "labels absent": ({
                "metadata": {
                    "name": "ordinary-pod",
                    "uid": "uid-ordinary-pod",
                    "ownerReferences": canonical["metadata"]["ownerReferences"],
                },
                "spec": {"serviceAccountName": "default"},
            }, True),
            "labels null": ({
                "metadata": {
                    "name": "ordinary-pod",
                    "uid": "uid-ordinary-pod",
                    "labels": None,
                    "ownerReferences": canonical["metadata"]["ownerReferences"],
                },
                "spec": {"serviceAccountName": "default"},
            }, False),
            "spec missing": ({
                "metadata": {
                    "name": "ordinary-pod",
                    "uid": "uid-ordinary-pod",
                },
            }, False),
            "spec null": ({
                "metadata": {
                    "name": "ordinary-pod",
                    "uid": "uid-ordinary-pod",
                },
                "spec": None,
            }, False),
            "metadata missing": ({
                "spec": {"serviceAccountName": "default"},
            }, False),
            "metadata null": ({
                "metadata": None,
                "spec": {"serviceAccountName": "default"},
            }, False),
        }
        for label, (pod, expected_ok) in cases.items():
            with self.subTest(case=label):
                pods = {"apiVersion": "v1", "kind": "PodList", "items": [pod]}
                result, calls, _ = self.run_preflight(json.dumps(pods))
                self.assertEqual(
                    result["ok"], expected_ok,
                    f"{label} produced unexpected preflight verdict: {result}",
                )
                if expected_ok:
                    self.assertFalse(
                        any(call.startswith("delete pod ordinary-pod") for call in calls),
                        f"{label} must not create a false cleanup target",
                    )


class RecoveryFinalTeardownRbacFirstOrderingTest(unittest.TestCase):
    """PR#287 independent review (round N): the FINAL teardown at the bottom
    of `repair_harbor_credential_secret` -- which runs whatever the recovery
    outcome was, success or failure -- called `cleanup_bootstrap_job_verified`
    (fixed-name Job cleanup) BEFORE it revoked the recovery RBAC. Kubernetes
    authorizes every request live against whatever RBAC state exists at
    request time; it is never baked into a Pod's token at start. A Pod that
    survived past this recovery attempt and still mounts the
    harbor-credential-recovery ServiceAccount therefore stayed fully
    privileged for the entire span of the Job/Pod cleanup, exactly the
    exposure `harbor_recovery_resume_preflight` was already hardened against
    on the *next* run's preflight -- but not here, at the end of *this* run.

    The fix extracts the final teardown into its own
    `harbor_recovery_final_teardown` function so it is independently
    testable, mirroring the existing preflight extraction.
    """

    @classmethod
    def setUpClass(cls):
        cls.teardown = func_body("harbor_recovery_final_teardown")
        cls.repair = func_body("repair_harbor_credential_secret")

    def test_rbac_delete_runs_before_job_cleanup(self):
        rbac_at = self.teardown.index("kubectl delete -f -")
        job_at = self.teardown.index("cleanup_bootstrap_job_verified")
        self.assertLess(
            rbac_at, job_at,
            "recovery RBAC must be revoked before the fixed-name repair Job "
            "is cleaned up, not after",
        )

    def test_rbac_delete_runs_before_pod_cleanup(self):
        rbac_at = self.teardown.index("kubectl delete -f -")
        pod_at = self.teardown.index("harbor_recovery_delete_leftover_pods")
        self.assertLess(
            rbac_at, pod_at,
            "recovery RBAC must be revoked before any recovery-identity Pod "
            "cleanup is even attempted",
        )

    def test_rbac_delete_result_is_recorded_and_used_without_gating_cleanup(self):
        """The requirement is NOT to short-circuit cleanup on an RBAC delete
        failure -- Job and Pod cleanup must still run even then. What IS
        required is that the RBAC delete's exit status is recorded (not
        discarded) and threaded into the final combined verdict."""
        rbac_at = self.teardown.index("kubectl delete -f -")
        job_at = self.teardown.index("cleanup_bootstrap_job_verified")
        pod_at = self.teardown.index("harbor_recovery_delete_leftover_pods")
        verdict_at = self.teardown.index("harbor_recovery_cleanup_verdict")

        between_rbac_and_job = self.teardown[rbac_at:job_at]
        self.assertNotIn("if $rbac_delete", between_rbac_and_job,
                         "an RBAC delete failure must not gate Job cleanup")
        self.assertNotIn("return", between_rbac_and_job,
                         "no early return may sit between RBAC delete and Job cleanup")

        between_job_and_pod = self.teardown[job_at:pod_at]
        self.assertNotIn("if $rbac_delete", between_job_and_pod,
                         "an RBAC delete failure must not gate Pod cleanup either")

        verdict_call = self.teardown[verdict_at:]
        self.assertIn("$rbac_delete.exit_code", verdict_call,
                      "the RBAC delete result must be recorded and passed into the final verdict")

    def test_job_cleanup_does_not_short_circuit_pod_cleanup(self):
        """Every cleanup step must be attempted independently -- a malformed
        or failed Job cleanup must never prevent the recovery-identity Pod
        cleanup from even being attempted."""
        job_at = self.teardown.index("cleanup_bootstrap_job_verified")
        pod_at = self.teardown.index("harbor_recovery_delete_leftover_pods")
        between = self.teardown[job_at:pod_at]
        self.assertNotIn("if not $job_cleanup.ok", between,
                         "job cleanup failure must not gate pod cleanup")
        self.assertNotIn("return", between,
                         "no early return may sit between the independent cleanup steps")

    def test_positive_absence_verification_runs_after_every_cleanup_attempt(self):
        job_at = self.teardown.index("cleanup_bootstrap_job_verified")
        pod_at = self.teardown.index("harbor_recovery_delete_leftover_pods")
        leftovers_at = self.teardown.index("harbor_recovery_privilege_leftovers")
        verdict_at = self.teardown.index("harbor_recovery_cleanup_verdict")
        self.assertLess(job_at, leftovers_at)
        self.assertLess(pod_at, leftovers_at)
        self.assertLess(leftovers_at, verdict_at,
                        "the combined verdict must be computed from the "
                        "positively re-verified leftovers, not before them")

    def test_verdict_combines_outcome_job_rbac_pod_and_leftovers(self):
        verdict_call = self.teardown[self.teardown.index("harbor_recovery_cleanup_verdict"):]
        self.assertIn("$outcome.ok", verdict_call)
        self.assertIn("$outcome.error", verdict_call)
        self.assertIn("$job_cleanup.ok", verdict_call)
        self.assertIn("$pod_cleanup.ok", verdict_call)
        self.assertIn("$leftovers", verdict_call)

    def test_final_teardown_never_interpolates_caught_error_messages(self):
        """Defense-in-depth catches expose only fixed failure categories;
        arbitrary thrown text can contain credentials, logs, or API output."""
        self.assertNotIn("$err.msg", self.teardown)

    def test_repair_delegates_the_final_teardown_after_the_guarded_region(self):
        # Locate the exact executable call site -- `(harbor_recovery_final_
        # teardown` -- not the earlier prose comment that merely mentions the
        # function name ahead of its actual invocation.
        catch_at = self.repair.index("catch")
        teardown_at = self.repair.index("(harbor_recovery_final_teardown")
        self.assertLess(catch_at, teardown_at,
                        "the final teardown must run after the guarded region, "
                        "on every path (success or failure)")
        self.assertIn("$outcome", self.repair[teardown_at:teardown_at + 60])

    def test_repair_raises_the_combined_verdict_message(self):
        teardown_at = self.repair.index("(harbor_recovery_final_teardown")
        final_raise = self.repair.rindex("error make")
        self.assertLess(teardown_at, final_raise)
        self.assertIn("$verdict.msg", self.repair[final_raise:])


@unittest.skipIf(NU is None, "nushell (nu) is required")
class RecoveryFinalTeardownBehaviourTest(unittest.TestCase):
    """Real, executed behaviour of `harbor_recovery_final_teardown` against a
    fake `kubectl`: proves (not merely asserts from source) that RBAC is
    revoked first; that a malformed Pod listing or a failed Job cleanup can
    never prevent or delay that revocation; that the fixed-name Job cleanup
    and the recovery-identity Pod cleanup (by ServiceAccount or by owning
    Job) are both independently attempted afterwards; that absence is then
    positively re-verified; and that every failure combines into one verdict
    without any step short-circuiting the rest.
    """

    FAKE_KUBECTL = r"""#!/bin/sh
LOG="${KLOG:?KLOG must be set}"
printf '%s\n' "$*" >> "$LOG"
verb="$1"; shift
case "$verb" in
  delete)
    kind="$1"
    case "$kind" in
      pod)
        name="$2"
        if [ "$POD_DELETE_FAILS" = "$name" ]; then
          echo "boom: delete pod $name failed" >&2
          exit 1
        fi
        printf '%s\n' "$name" >> "$LOG.deletedpods"
        exit 0
        ;;
      job)
        jobname="$2"
        if [ "$JOB_DELETE_FAILS" = "true" ]; then
          echo "boom: delete job $jobname failed" >&2
          exit 1
        fi
        names=$(printf '%s' "$PODS_JSON" | jq -r --arg jn "$jobname" \
          '.items[] | select((.metadata.ownerReferences // []) | any(.kind=="Job" and .name==$jn)) | .metadata.name')
        if [ -n "$names" ]; then
          printf '%s\n' "$names" >> "$LOG.deletedpods"
        fi
        : > "$LOG.jobdeleted"
        exit 0
        ;;
      -f)
        cat >/dev/null
        if [ "$RBAC_DELETE_FAILS" = "true" ]; then
          echo "boom: rbac delete failed" >&2
          exit 1
        fi
        : > "$LOG.rbacdeleted"
        exit 0
        ;;
      *)
        exit 0
        ;;
    esac
    ;;
  get)
    kind="$1"
    if [ "$kind" = "job" ]; then
      if [ -f "$LOG.jobdeleted" ]; then
        printf ''
      else
        printf 'job.batch/%s\n' "$2"
      fi
      exit 0
    fi
    if [ "$kind" = "--raw" ] && [ "$2" = "/api/v1/namespaces/crossplane-system/pods" ]; then
      if [ "$PODS_LIST_FAILS" = "true" ]; then
        echo "boom: list pods failed" >&2
        exit 1
      fi
      if [ "$PODS_MALFORMED" = "true" ]; then
        printf '{}'
        exit 0
      fi
      filtered="$PODS_JSON"
      if [ -f "$LOG.deletedpods" ]; then
        while IFS= read -r name; do
          [ -z "$name" ] && continue
          filtered=$(printf '%s' "$filtered" | jq --arg n "$name" '.items |= map(select(.metadata.name != $n))')
        done < "$LOG.deletedpods"
      fi
      printf '%s' "$filtered"
      exit 0
    fi
    name="$2"
    if [ -n "$LEFTOVER_PRESENT_NAME" ] && [ "$name" = "$LEFTOVER_PRESENT_NAME" ]; then
      printf '%s/%s\n' "$kind" "$name"
      exit 0
    fi
    if [ -f "$LOG.rbacdeleted" ]; then
      printf ''
    else
      printf '%s/%s\n' "$kind" "$name"
    fi
    exit 0
    ;;
  *)
    exit 0
    ;;
esac
"""

    @classmethod
    def setUpClass(cls):
        cls.bindir = Path(tempfile.mkdtemp(prefix="pr287-teardown-kubectl-"))
        fake = cls.bindir / "kubectl"
        fake.write_text(cls.FAKE_KUBECTL, encoding="utf-8")
        fake.chmod(0o755)

    def run_teardown(self, pods_json: str, outcome_ok: bool = True,
                      outcome_error: str = "", setup_path: Path = SETUP_PATH,
                      **extra_env) -> tuple[dict, list[str]]:
        log_path = Path(tempfile.mkdtemp(prefix="pr287-teardown-log-")) / "calls.log"
        env = {
            "PATH": f"{self.bindir}:/usr/bin:/bin",
            "KLOG": str(log_path),
            "PODS_JSON": pods_json,
            **extra_env,
        }
        result = subprocess.run(
            [NU, "--no-config-file", "-c",
             f"source {setup_path.as_posix()}\n"
             f'harbor_recovery_final_teardown '
             f'{{ok: {str(outcome_ok).lower()}, error: "{outcome_error}"}} '
             f'"fake-rbac-manifest" | to json'],
            cwd=ROOT, text=True, capture_output=True, timeout=30, env=env,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = log_path.read_text().splitlines() if log_path.exists() else []
        return json.loads(result.stdout), calls

    def _empty_pods(self) -> str:
        return json.dumps({"apiVersion": "v1", "kind": "PodList", "items": []})

    def _pod(self, name: str, service_account: str = "default", owner_name: str = "") -> dict:
        pod = {"metadata": {"name": name, "uid": f"uid-{name}"}, "spec": {"serviceAccountName": service_account}}
        if owner_name:
            pod["metadata"]["ownerReferences"] = [
                {"kind": "Job", "name": owner_name, "uid": "some-uid", "controller": True}
            ]
        return pod

    def test_clean_state_succeeds_and_still_revokes_rbac(self):
        verdict, calls = self.run_teardown(self._empty_pods())
        self.assertTrue(verdict["ok"], verdict.get("msg"))
        self.assertTrue(any(c.startswith("delete -f -") for c in calls),
                        "the recovery RBAC must be revoked even on a clean success")

    def test_rbac_delete_is_the_first_mutating_call(self):
        verdict, calls = self.run_teardown(self._empty_pods())
        self.assertTrue(verdict["ok"], verdict.get("msg"))
        delete_calls = [c for c in calls if c.startswith("delete ")]
        self.assertTrue(delete_calls)
        self.assertTrue(
            delete_calls[0].startswith("delete -f -"),
            f"RBAC delete must be the first delete call attempted, got: {delete_calls!r}",
        )

    def test_a_malformed_pod_listing_does_not_prevent_or_delay_rbac_delete(self):
        verdict, calls = self.run_teardown(self._empty_pods(), PODS_MALFORMED="true")
        rbac_calls = [i for i, c in enumerate(calls) if c.startswith("delete -f -")]
        self.assertTrue(rbac_calls, "RBAC must still be revoked when Pod listing is malformed")
        self.assertFalse(verdict["ok"], "a malformed Pod listing must still be reported as fatal")

    def test_an_unavailable_pod_listing_does_not_prevent_or_delay_rbac_delete(self):
        verdict, calls = self.run_teardown(self._empty_pods(), PODS_LIST_FAILS="true")
        rbac_calls = [i for i, c in enumerate(calls) if c.startswith("delete -f -")]
        self.assertTrue(rbac_calls, "RBAC must still be revoked when Pod listing fails outright")
        self.assertFalse(verdict["ok"])

    def test_a_job_cleanup_failure_does_not_prevent_or_delay_rbac_delete(self):
        verdict, calls = self.run_teardown(self._empty_pods(), JOB_DELETE_FAILS="true")
        rbac_calls = [i for i, c in enumerate(calls) if c.startswith("delete -f -")]
        job_calls = [i for i, c in enumerate(calls) if c.startswith("delete job")]
        self.assertTrue(rbac_calls, "RBAC must still be revoked when Job cleanup fails")
        self.assertTrue(job_calls, "the Job cleanup must still have been attempted")
        self.assertLess(rbac_calls[0], job_calls[0],
                        "RBAC revocation must precede the Job cleanup attempt")
        self.assertFalse(verdict["ok"])
        self.assertIn("recovery Job", verdict["msg"])

    def test_job_and_pod_cleanup_are_both_attempted_after_revocation(self):
        """The fixed-name Job cleanup and the recovery-identity Pod cleanup
        (here a Pod identified purely by ServiceAccount, with no Job owner
        at all) must both be independently attempted."""
        pods = {"apiVersion": "v1", "kind": "PodList",
                "items": [self._pod("orphan-recovery-pod", service_account="harbor-credential-recovery")]}
        verdict, calls = self.run_teardown(json.dumps(pods))
        self.assertTrue(verdict["ok"], verdict.get("msg"))
        self.assertTrue(any(c.startswith("delete job harbor-credential-repair") for c in calls),
                        "the fixed-name repair Job cleanup must be attempted")
        self.assertTrue(any(c.startswith("delete pod orphan-recovery-pod") for c in calls),
                        "a Pod identified only by the recovery ServiceAccount must be cleaned up too")

    def test_a_pod_cleanup_failure_does_not_prevent_job_cleanup_or_verification(self):
        pods = {"apiVersion": "v1", "kind": "PodList",
                "items": [self._pod("stubborn-pod", service_account="harbor-credential-recovery")]}
        verdict, calls = self.run_teardown(json.dumps(pods), POD_DELETE_FAILS="stubborn-pod")
        self.assertFalse(verdict["ok"])
        self.assertIn("recovery Pod", verdict["msg"])
        self.assertTrue(any(c.startswith("delete job harbor-credential-repair") for c in calls),
                        "Job cleanup must still be attempted despite the Pod cleanup failure")

    def test_every_independent_failure_is_combined_and_none_short_circuits_the_rest(self):
        """The headline finding: RBAC delete, Job cleanup and Pod cleanup are
        all attempted regardless of each other's outcome, and every failure
        -- including the original recovery outcome -- is combined into one
        final verdict."""
        pods = {"apiVersion": "v1", "kind": "PodList",
                "items": [self._pod("stubborn-pod", service_account="harbor-credential-recovery")]}
        verdict, calls = self.run_teardown(
            json.dumps(pods), outcome_ok=False, outcome_error="robot selection failed",
            RBAC_DELETE_FAILS="true", JOB_DELETE_FAILS="true", POD_DELETE_FAILS="stubborn-pod",
        )
        self.assertFalse(verdict["ok"])
        self.assertIn("robot selection failed", verdict["msg"])
        self.assertIn("RBAC", verdict["msg"])
        self.assertIn("recovery Job", verdict["msg"])
        self.assertIn("recovery Pod", verdict["msg"])
        self.assertTrue(any(c.startswith("delete -f -") for c in calls),
                        "RBAC delete must still be attempted")
        self.assertTrue(any(c.startswith("delete job harbor-credential-repair") for c in calls),
                        "Job cleanup must still be attempted despite the RBAC failure")
        self.assertTrue(any(c.startswith("delete pod stubborn-pod") for c in calls),
                        "Pod cleanup must still be attempted despite the RBAC and Job failures")

    def test_absence_is_positively_reverified_after_every_cleanup_attempt(self):
        verdict, calls = self.run_teardown(
            self._empty_pods(), LEFTOVER_PRESENT_NAME="harbor-credential-recovery",
        )
        self.assertFalse(verdict["ok"])
        self.assertIn("harbor-credential-recovery", verdict["msg"])

    def test_the_original_recovery_failure_survives_a_fully_clean_cleanup(self):
        verdict, _ = self.run_teardown(self._empty_pods(), outcome_ok=False,
                                        outcome_error="robot selection failed")
        self.assertFalse(verdict["ok"])
        self.assertIn("robot selection failed", verdict["msg"])

    def test_leftover_verification_exception_cannot_leak_its_message(self):
        """PR#287 independent review (round 8): force the final defense-in-depth
        leftovers catch itself to receive credential-like text. The user-facing
        verdict may identify the failed category, but not the thrown message."""
        marker = "LEAK_MARKER_supersecret"
        needle = (
            "    let leftovers = (try {\n"
            "        harbor_recovery_privilege_leftovers\n"
            "    } catch"
        )
        replacement = (
            "    let leftovers = (try {\n"
            f'        error make {{msg: "{marker}"}}\n'
            "    } catch"
        )
        self.assertEqual(SETUP.count(needle), 1,
                         "mutation must target exactly the final leftovers guard")
        mutated = SETUP.replace(needle, replacement, 1)
        setup_path = Path(tempfile.mkdtemp(prefix="pr287-teardown-source-")) / "local-setup.nu"
        setup_path.write_text(mutated, encoding="utf-8")

        verdict, _calls = self.run_teardown(self._empty_pods(), setup_path=setup_path)

        self.assertFalse(verdict["ok"])
        self.assertNotIn(marker, verdict["msg"])

    def _malformed_item_pods(self, item) -> str:
        return json.dumps({"apiVersion": "v1", "kind": "PodList", "items": [item]})

    def test_a_primitive_pod_item_does_not_crash_final_teardown(self):
        """PR#287 independent review (round 7): a PodList item that is a bare
        primitive (not an object) -- e.g. `items: ["not-an-object"]`, a shape
        `kubectl get pods -o json` itself would never emit, but which this
        function must not simply trust -- throws inside Nushell's cell-path
        access (`get -o metadata.name` and friends) the moment the survivor
        predicates try to read it. That is an uncaught Nushell error, not a
        value this function can inspect, and the review reproduced it
        aborting the whole teardown right after the RBAC delete, before the
        recovery Pod cleanup or the final privilege-leftover verification
        ever ran. This must instead surface as an ordinary failed verdict."""
        verdict, _calls = self.run_teardown(self._malformed_item_pods("not-an-object"))
        self.assertFalse(verdict["ok"])

    def test_a_primitive_pod_item_still_reaches_pod_cleanup_and_leftover_verification(self):
        _verdict, calls = self.run_teardown(self._malformed_item_pods("not-an-object"))
        get_pods_calls = [
            c for c in calls
            if c == f"get --raw {POD_LIST_RAW_PATH}"
        ]
        self.assertGreaterEqual(
            len(get_pods_calls), 3,
            "the Job cleanup's own residual-pod check, the recovery Pod "
            "cleanup's listing, and the final privilege-leftover listing "
            "must each independently list pods -- a throw triggered by the "
            "first must not prevent the other two from running at all",
        )

    def test_malformed_metadata_shape_does_not_crash_final_teardown(self):
        verdict, _calls = self.run_teardown(
            self._malformed_item_pods({"metadata": "oops"}))
        self.assertFalse(verdict["ok"])

    def test_malformed_owner_references_shape_does_not_crash_final_teardown(self):
        verdict, _calls = self.run_teardown(self._malformed_item_pods(
            {"metadata": {"name": "x", "ownerReferences": {"kind": "Job"}}}))
        self.assertFalse(verdict["ok"])

    def test_owner_reference_element_not_an_object_does_not_crash_final_teardown(self):
        verdict, _calls = self.run_teardown(self._malformed_item_pods(
            {"metadata": {"name": "x", "ownerReferences": ["not-an-object"]}}))
        self.assertFalse(verdict["ok"])

    def test_malformed_labels_shape_does_not_crash_final_teardown(self):
        verdict, _calls = self.run_teardown(self._malformed_item_pods(
            {"metadata": {"name": "x", "labels": [1, 2, 3]}}))
        self.assertFalse(verdict["ok"])

    def test_malformed_spec_shape_does_not_crash_final_teardown(self):
        verdict, _calls = self.run_teardown(self._malformed_item_pods({"spec": "weird"}))
        self.assertFalse(verdict["ok"])

    def test_malformed_consumed_pod_leaves_fail_closed_at_final_teardown(self):
        """PR#287 independent review (round 8): malformed scalar leaves are
        failed verdicts, never a throw and never a successful teardown."""
        valid = self._pod("ordinary-pod", owner_name="ordinary-job")
        valid["metadata"]["labels"] = {"job-name": "ordinary-job"}
        mutations = {
            "metadata.name object": ("metadata", "name", {"not": "a string"}),
            "metadata.name empty": ("metadata", "name", ""),
            "metadata.uid object": ("metadata", "uid", {"not": "a string"}),
            "metadata.uid empty": ("metadata", "uid", ""),
            "serviceAccountName object": ("spec", "serviceAccountName", {"not": "a string"}),
            "job-name label object": ("metadata", "labels", {"job-name": {"not": "a string"}}),
            "owner kind object": ("metadata", "ownerReferences", [{
                "kind": {"not": "a string"}, "name": "ordinary-job",
                "uid": "some-uid", "controller": True,
            }]),
            "owner kind empty": ("metadata", "ownerReferences", [{
                "kind": "", "name": "ordinary-job",
                "uid": "some-uid", "controller": True,
            }]),
            "owner name object": ("metadata", "ownerReferences", [{
                "kind": "Job", "name": {"not": "a string"},
                "uid": "some-uid", "controller": True,
            }]),
            "owner name empty": ("metadata", "ownerReferences", [{
                "kind": "Job", "name": "",
                "uid": "some-uid", "controller": True,
            }]),
            "owner uid object": ("metadata", "ownerReferences", [{
                "kind": "Job", "name": "ordinary-job",
                "uid": {"not": "a string"}, "controller": True,
            }]),
            "owner uid empty": ("metadata", "ownerReferences", [{
                "kind": "Job", "name": "ordinary-job",
                "uid": "", "controller": True,
            }]),
            "owner controller string": ("metadata", "ownerReferences", [{
                "kind": "Job", "name": "ordinary-job",
                "uid": "some-uid", "controller": "true",
            }]),
        }
        for label, (section, leaf, malformed) in mutations.items():
            with self.subTest(leaf=label):
                pod = json.loads(json.dumps(valid))
                if section == "metadata" and leaf in ("labels", "ownerReferences"):
                    pod["metadata"][leaf] = malformed
                else:
                    pod[section][leaf] = malformed
                verdict, _calls = self.run_teardown(self._malformed_item_pods(pod))
                self.assertFalse(verdict["ok"], f"{label} must never pass teardown as clean")

    def test_pod_field_presence_and_null_matrix_at_final_teardown(self):
        """PR#287 independent review (round 9): exercise presence separately
        from value shape at the unconditional final cleanup boundary."""
        canonical = {
            "metadata": {
                "name": "ordinary-pod",
                "uid": "uid-ordinary-pod",
                "labels": {"job-name": "ordinary-job"},
                "ownerReferences": [{
                    "kind": "Job",
                    "name": "ordinary-job",
                    "uid": "uid-ordinary-job",
                    "controller": True,
                }],
            },
            "spec": {"serviceAccountName": "default"},
        }
        cases = {
            "canonical Pod": (canonical, True),
            "ownerReferences absent": ({
                "metadata": {
                    "name": "ordinary-pod",
                    "uid": "uid-ordinary-pod",
                    "labels": {"job-name": "ordinary-job"},
                },
                "spec": {"serviceAccountName": "default"},
            }, True),
            "ownerReferences null": ({
                "metadata": {
                    "name": "ordinary-pod",
                    "uid": "uid-ordinary-pod",
                    "labels": {"job-name": "ordinary-job"},
                    "ownerReferences": None,
                },
                "spec": {"serviceAccountName": "default"},
            }, False),
            "labels absent": ({
                "metadata": {
                    "name": "ordinary-pod",
                    "uid": "uid-ordinary-pod",
                    "ownerReferences": canonical["metadata"]["ownerReferences"],
                },
                "spec": {"serviceAccountName": "default"},
            }, True),
            "labels null": ({
                "metadata": {
                    "name": "ordinary-pod",
                    "uid": "uid-ordinary-pod",
                    "labels": None,
                    "ownerReferences": canonical["metadata"]["ownerReferences"],
                },
                "spec": {"serviceAccountName": "default"},
            }, False),
            "spec missing": ({
                "metadata": {
                    "name": "ordinary-pod",
                    "uid": "uid-ordinary-pod",
                },
            }, False),
            "spec null": ({
                "metadata": {
                    "name": "ordinary-pod",
                    "uid": "uid-ordinary-pod",
                },
                "spec": None,
            }, False),
            "metadata missing": ({
                "spec": {"serviceAccountName": "default"},
            }, False),
            "metadata null": ({
                "metadata": None,
                "spec": {"serviceAccountName": "default"},
            }, False),
        }
        for label, (pod, expected_ok) in cases.items():
            with self.subTest(case=label):
                verdict, calls = self.run_teardown(self._malformed_item_pods(pod))
                self.assertEqual(
                    verdict["ok"], expected_ok,
                    f"{label} produced unexpected teardown verdict: {verdict}",
                )
                if expected_ok:
                    self.assertFalse(
                        any(call.startswith("delete pod ordinary-pod") for call in calls),
                        f"{label} must not create a false cleanup target",
                    )


@unittest.skipIf(NU is None, "nushell (nu) is required")
class RecoveryLeftoverPodDeleteAccumulationBehaviourTest(unittest.TestCase):
    """PR#287 independent review (round 7): `harbor_recovery_delete_leftover_pods`
    returned immediately after the first failed `kubectl delete pod`, so any
    later stale recovery Pod was never even attempted -- left running,
    still mounting the harbor-credential-recovery ServiceAccount, behind a
    failure message that only ever named the first one. Every matching Pod
    must be attempted, and every failure accumulated into one result.
    """

    FAKE_KUBECTL = r"""#!/bin/sh
LOG="${KLOG:?KLOG must be set}"
printf '%s\n' "$*" >> "$LOG"
verb="$1"; shift
case "$verb" in
  get)
    if [ "$1" != "--raw" ] || [ "$2" != "/api/v1/namespaces/crossplane-system/pods" ]; then
      echo "unexpected pod list request: $*" >&2
      exit 2
    fi
    printf '%s' "$PODS_JSON"
    exit 0
    ;;
  delete)
    kind="$1"; name="$2"
    if [ "$kind" = "pod" ]; then
      for failing in $POD_DELETE_FAILS; do
        if [ "$failing" = "$name" ]; then
          echo "boom: delete pod $name failed" >&2
          exit 1
        fi
      done
    fi
    exit 0
    ;;
  *)
    exit 0
    ;;
esac
"""

    @classmethod
    def setUpClass(cls):
        cls.bindir = Path(tempfile.mkdtemp(prefix="pr287-leftover-delete-kubectl-"))
        fake = cls.bindir / "kubectl"
        fake.write_text(cls.FAKE_KUBECTL, encoding="utf-8")
        fake.chmod(0o755)

    def _pod(self, name: str, service_account: str = "harbor-credential-recovery") -> dict:
        return {"metadata": {"name": name, "uid": f"uid-{name}"},
                "spec": {"serviceAccountName": service_account}}

    def run_delete(self, pods_json: str, **extra_env) -> tuple[dict, list[str]]:
        log_path = Path(tempfile.mkdtemp(prefix="pr287-leftover-delete-log-")) / "calls.log"
        env = {
            "PATH": f"{self.bindir}:/usr/bin:/bin",
            "KLOG": str(log_path),
            "PODS_JSON": pods_json,
            **extra_env,
        }
        result = subprocess.run(
            [NU, "--no-config-file", "-c",
             f"source {SETUP_PATH.as_posix()}\n"
             "harbor_recovery_delete_leftover_pods | to json"],
            cwd=ROOT, text=True, capture_output=True, timeout=30, env=env,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = log_path.read_text().splitlines() if log_path.exists() else []
        return json.loads(result.stdout), calls

    def test_a_failed_delete_of_the_first_of_two_matching_pods_still_attempts_the_second(self):
        pods = {"apiVersion": "v1", "kind": "PodList",
                "items": [self._pod("recovery-pod-a"), self._pod("recovery-pod-b")]}
        result, calls = self.run_delete(json.dumps(pods), POD_DELETE_FAILS="recovery-pod-a")
        self.assertFalse(result["ok"])
        self.assertTrue(
            any(c.startswith("delete pod recovery-pod-b") for c in calls),
            "the second matching recovery Pod must still be attempted after "
            "the first Pod's delete fails",
        )

    def test_a_failed_first_delete_is_still_named_in_the_combined_failure(self):
        pods = {"apiVersion": "v1", "kind": "PodList",
                "items": [self._pod("recovery-pod-a"), self._pod("recovery-pod-b")]}
        result, _calls = self.run_delete(json.dumps(pods), POD_DELETE_FAILS="recovery-pod-a")
        self.assertFalse(result["ok"])
        self.assertIn("recovery-pod-a", result["reason"])

    def test_both_pods_failing_are_both_named_in_the_combined_failure(self):
        pods = {"apiVersion": "v1", "kind": "PodList",
                "items": [self._pod("recovery-pod-a"), self._pod("recovery-pod-b")]}
        result, calls = self.run_delete(
            json.dumps(pods), POD_DELETE_FAILS="recovery-pod-a recovery-pod-b")
        self.assertFalse(result["ok"])
        self.assertIn("recovery-pod-a", result["reason"])
        self.assertIn("recovery-pod-b", result["reason"])
        self.assertTrue(any(c.startswith("delete pod recovery-pod-a") for c in calls))
        self.assertTrue(any(c.startswith("delete pod recovery-pod-b") for c in calls))

    def test_no_failures_still_succeeds(self):
        pods = {"apiVersion": "v1", "kind": "PodList",
                "items": [self._pod("recovery-pod-a"), self._pod("recovery-pod-b")]}
        result, calls = self.run_delete(json.dumps(pods))
        self.assertTrue(result["ok"], result.get("reason"))
        self.assertTrue(any(c.startswith("delete pod recovery-pod-a") for c in calls))
        self.assertTrue(any(c.startswith("delete pod recovery-pod-b") for c in calls))

    def test_failure_message_does_not_leak_kubectl_stderr(self):
        pods = {"apiVersion": "v1", "kind": "PodList", "items": [self._pod("recovery-pod-a")]}
        result, _calls = self.run_delete(json.dumps(pods), POD_DELETE_FAILS="recovery-pod-a")
        self.assertFalse(result["ok"])
        self.assertNotIn("boom", result["reason"])


@unittest.skipIf(NU is None, "nushell (nu) is required")
class RecoveryPodLeftoverBehaviourTest(unittest.TestCase):
    """The final privilege boundary must not trust a mutable job-name label."""

    @classmethod
    def setUpClass(cls):
        cls.bindir = Path(tempfile.mkdtemp(prefix="pr287-leftover-kubectl-"))
        fake = cls.bindir / "kubectl"
        fake.write_text(
            "#!/bin/sh\n"
            "if [ \"$1\" = get ] && [ \"$2\" = --raw ] && [ \"$3\" = /api/v1/namespaces/crossplane-system/pods ]; then\n"
            "  printf '%s' \"$PODS_JSON\"\n"
            "fi\n"
            "exit 0\n",
            encoding="utf-8",
        )
        fake.chmod(0o755)

    def leftovers(self, pod_doc: str):
        env = {
            "PATH": f"{self.bindir}:/usr/bin:/bin",
            "PODS_JSON": pod_doc,
        }
        result = subprocess.run(
            [str(NU), "--no-config-file", "-c",
             f"source {SETUP_PATH.as_posix()}\n"
             "harbor_recovery_privilege_leftovers | to json"],
            cwd=ROOT, text=True, capture_output=True, timeout=30, env=env,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_empty_namespace_is_clean(self):
        self.assertEqual(self.leftovers('{"apiVersion":"v1","kind":"PodList","items":[]}'), [])

    def test_relabelled_pod_is_found_by_recovery_job_owner_name(self):
        pods = {"apiVersion": "v1", "kind": "PodList", "items": [{"metadata": {"name": "orphaned", "labels": {},
            "ownerReferences": [{"kind": "Job", "name": "harbor-credential-repair",
                                  "uid": "old", "controller": True}]},
            "spec": {"serviceAccountName": "default"}}]}
        self.assertTrue(self.leftovers(json.dumps(pods)))

    def test_orphaned_pod_is_found_by_recovery_service_account(self):
        pods = {"apiVersion": "v1", "kind": "PodList", "items": [{"metadata": {"name": "orphaned", "labels": {}},
                            "spec": {"serviceAccountName": "harbor-credential-recovery"}}]}
        self.assertTrue(self.leftovers(json.dumps(pods)))

    def test_malformed_pod_list_is_unverifiable_not_clean(self):
        for malformed in (
            "not-json",
            "{}",
            '{"apiVersion":"v1","kind":"List","items":[]}',
            '{"apiVersion":"v1","kind":"PodList","items":{}}',
        ):
            with self.subTest(malformed=malformed):
                leftovers = self.leftovers(malformed)
                self.assertTrue(leftovers)
                self.assertTrue(any("unverifiable" in item for item in leftovers))


@unittest.skipIf(NU is None, "nushell (nu) is required")
class OwnedPodSelectionBehaviourTest(unittest.TestCase):
    """Issue #285 second review finding 4: logs must be bound to the Job this
    run created, by *identity* rather than by name.

    Re-reading `.metadata.uid` and then calling `kubectl logs job/<fixed-name>`
    resolves the name a second time: a Job replaced in between hands back
    another Job's logs. For the probe that means a stale "all keys present"
    report could satisfy the gate for an empty credential. The pod is now
    selected by its owning Job's UID and the logs are read by pod identity.
    """

    OURS = "11111111-1111-1111-1111-111111111111"
    THEIRS = "22222222-2222-2222-2222-222222222222"

    def select(self, pods: dict, job_uid: str | None = None, job_name: str = "pr287-test-job") -> dict:
        pod_list = {"apiVersion": "v1", "kind": "PodList", **pods}
        payload = json.dumps(pod_list).replace("\\", "\\\\").replace('"', '\\"')
        snippet = (f'select_owned_pod "{payload}" "{job_name}" "{job_uid or self.OURS}" | to json')
        result = run_nu(snippet)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def pod(self, name: str, owner_uid: str, kind: str = "Job",
            owner_name: str = "pr287-test-job", controller: bool = True) -> dict:
        return {"metadata": {"name": name, "uid": f"pod-{name}",
                             "ownerReferences": [{"kind": kind, "name": owner_name,
                                                  "uid": owner_uid,
                                                  "controller": controller}]},
                "spec": {"serviceAccountName": "default"}}

    def test_the_pod_owned_by_our_job_is_selected(self):
        result = self.select({"items": [self.pod("probe-abc", self.OURS)]})
        self.assertTrue(result["ok"])
        self.assertEqual(result["name"], "probe-abc")

    def test_a_pod_owned_by_a_replacement_job_is_rejected(self):
        """The exact race: the fixed-name Job was deleted and recreated, so the
        surviving pod belongs to a different Job UID."""
        result = self.select({"items": [self.pod("probe-xyz", self.THEIRS)]})
        self.assertFalse(result["ok"],
                         "logs from a replacement Job must never be accepted")

    def test_our_pod_is_picked_out_from_among_foreign_pods(self):
        result = self.select({"items": [self.pod("other", self.THEIRS),
                                        self.pod("ours", self.OURS)]})
        self.assertTrue(result["ok"])
        self.assertEqual(result["name"], "ours")

    def test_no_pods_at_all_fails_closed(self):
        self.assertFalse(self.select({"items": []})["ok"])

    def test_multiple_owned_pods_are_ambiguous_and_fail_closed(self):
        result = self.select({"items": [self.pod("a", self.OURS),
                                        self.pod("b", self.OURS)]})
        self.assertFalse(result["ok"], "an ambiguous pod set must not be trusted")

    def test_a_non_job_owner_with_the_same_uid_is_not_accepted(self):
        result = self.select({"items": [self.pod("p", self.OURS, kind="ReplicaSet")]})
        self.assertFalse(result["ok"])

    def test_a_job_owner_with_the_wrong_name_is_not_accepted(self):
        result = self.select({"items": [self.pod("p", self.OURS, owner_name="replacement-job")]})
        self.assertFalse(result["ok"])

    def test_a_non_controller_job_reference_is_not_accepted(self):
        result = self.select({"items": [self.pod("p", self.OURS, controller=False)]})
        self.assertFalse(result["ok"])

    def test_malformed_pod_payload_fails_closed(self):
        result = run_nu('select_owned_pod "not json at all" "job" "uid" | to json')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(json.loads(result.stdout)["ok"])

    def test_pod_without_owner_references_fails_closed(self):
        result = self.select({"items": [{
            "metadata": {"name": "orphan", "uid": "x"},
            "spec": {"serviceAccountName": "default"},
        }]})
        self.assertFalse(result["ok"])

    def test_the_owned_pods_own_uid_is_also_returned(self):
        """Issue #285 third review finding 2: logs must be bound to the pod's
        own immutable UID, not merely its (mutable, reusable) name -- the
        caller needs this uid to re-verify identity immediately before
        reading logs."""
        result = self.select({"items": [self.pod("probe-abc", self.OURS)]})
        self.assertTrue(result["ok"])
        self.assertEqual(result["uid"], "pod-probe-abc")

    def test_a_pod_with_no_uid_fails_closed(self):
        pod = self.pod("no-uid", self.OURS)
        pod["metadata"]["uid"] = ""
        result = self.select({"items": [pod]})
        self.assertFalse(result["ok"])


@unittest.skipIf(NU is None, "nushell (nu) is required")
class CleanupVerdictBehaviourTest(unittest.TestCase):
    """Issue #285 second review finding 3, executed rather than grepped.

    Cleanup verification must happen on EVERY path -- including when the
    recovery itself failed -- and both contexts must survive into the raised
    error, without any credential-bearing output.
    """

    def verdict(self, outcome_ok: bool, error: str, job_ok: bool,
                rbac_ok: bool, pod_ok: bool, leftovers: list) -> dict:
        left = "[" + " ".join(f'"{item}"' for item in leftovers) + "]"
        snippet = (
            f'harbor_recovery_cleanup_verdict '
            f'{str(outcome_ok).lower()} "{error}" '
            f'{str(job_ok).lower()} {str(rbac_ok).lower()} {str(pod_ok).lower()} {left} | to json'
        )
        result = run_nu(snippet)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_everything_clean_is_a_success(self):
        self.assertEqual(self.verdict(True, "", True, True, True, [])["ok"], True)

    def test_recovery_failure_alone_is_reported_with_its_original_message(self):
        v = self.verdict(False, "robot selection failed", True, True, True, [])
        self.assertFalse(v["ok"])
        self.assertIn("robot selection failed", v["msg"])

    def test_leftover_privilege_alone_is_fatal_even_when_recovery_succeeded(self):
        v = self.verdict(True, "", True, True, True,
                         ["crossplane-system/serviceaccount/harbor-credential-recovery"])
        self.assertFalse(v["ok"], "surviving privilege must never be reported as success")
        self.assertIn("harbor-credential-recovery", v["msg"])

    def test_failed_delete_calls_are_fatal(self):
        self.assertFalse(self.verdict(True, "", False, True, True, [])["ok"])
        self.assertFalse(self.verdict(True, "", True, False, True, [])["ok"])

    def test_failed_pod_cleanup_is_fatal(self):
        """PR#287 review (round N): the final teardown independently attempts
        a recovery-identity Pod cleanup (by ServiceAccount or owning Job,
        not only the fixed-name Job's own cascade). Its failure must be its
        own combinable verdict input, not silently absorbed into job/rbac."""
        v = self.verdict(True, "", True, True, False, [])
        self.assertFalse(v["ok"], "a failed recovery-identity pod cleanup must be fatal")

    def test_recovery_failure_and_leftovers_preserve_both_contexts(self):
        v = self.verdict(False, "job did not complete", True, True, True,
                         ["harbor/role/harbor-credential-recovery-admin-secret"])
        self.assertFalse(v["ok"])
        self.assertIn("job did not complete", v["msg"],
                      "the original cause must never be masked by the cleanup complaint")
        self.assertIn("harbor-credential-recovery-admin-secret", v["msg"],
                      "the surviving privilege must be named too")

    def test_every_independent_failure_combines_into_one_verdict(self):
        """No cleanup step's failure may mask or short-circuit reporting of
        any other: outcome, job, rbac and pod failures must all surface
        together in the same combined message."""
        v = self.verdict(False, "robot selection failed", False, False, False,
                         ["crossplane-system/serviceaccount/harbor-credential-recovery"])
        self.assertFalse(v["ok"])
        self.assertIn("robot selection failed", v["msg"])
        self.assertIn("recovery Job", v["msg"])
        self.assertIn("RBAC", v["msg"])
        self.assertIn("recovery Pod", v["msg"])
        self.assertIn("harbor-credential-recovery", v["msg"])

    def test_verdict_message_is_built_only_from_its_inputs(self):
        v = self.verdict(False, "SOME-CAUSE", True, True, True, ["NS/kind/NAME"])
        self.assertIn("SOME-CAUSE", v["msg"])
        self.assertIn("NS/kind/NAME", v["msg"])


class HealthyResumeStabilityTest(unittest.TestCase):
    """A complete credential must never be rotated by a resumed run."""

    def test_probe_runs_before_any_recovery_and_short_circuits_when_complete(self):
        gate = func_body("ensure_crossplane_harbor_credentials")
        self.assertLess(
            gate.index("probe_harbor_credential_keys"),
            gate.index("repair_harbor_credential_secret"),
            "the credential must be probed before Harbor is contacted at all",
        )
        self.assertIn("return", gate, "a complete credential must short-circuit the gate")
        missing_check = gate.index("harbor_credential_missing_keys")
        self.assertLess(
            missing_check,
            gate.index("repair_harbor_credential_secret"),
            "recovery must be conditional on genuinely missing keys",
        )

    def test_recovery_is_conditional_not_unconditional(self):
        gate = func_body("ensure_crossplane_harbor_credentials")
        repair_at = gate.index("repair_harbor_credential_secret")
        preceding = gate[:repair_at]
        self.assertIn("if ", preceding)
        self.assertIn("is-empty", preceding)


class DeclarativeRequestUnchangedTest(unittest.TestCase):
    """Scope guard: the Request keeps its verified least-privilege contract."""

    def test_secret_injection_and_permissions_are_untouched(self):
        forp = REQUEST["spec"]["forProvider"]
        injection = forp["secretInjectionConfigs"][0]
        self.assertEqual(injection["secretRef"],
                         {"name": CREDENTIAL_SECRET, "namespace": "crossplane-system"})
        self.assertEqual(
            {m["secretKey"] for m in injection["keyMappings"]}, set(REQUIRED_KEYS)
        )
        methods = {(m["method"], m.get("action")) for m in forp["mappings"]}
        self.assertEqual(methods, {("POST", "CREATE"), ("GET", "OBSERVE")},
                         "recovery lives in the bootstrap boundary, not in new mappings")
        self.assertEqual(REQUEST["spec"]["deletionPolicy"], "Orphan")


if __name__ == "__main__":
    unittest.main(verbosity=2)
