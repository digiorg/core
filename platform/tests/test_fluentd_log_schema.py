#!/usr/bin/env python3
"""Behavioral contracts for Fluentd log-schema isolation (Issue #345).

The deterministic suite parses the production YAML and executes the production
hook shell against a strict, stateful fake curl.  The optional real-image seam
is enabled only with RUN_FLUENTD_SCHEMA_INTEGRATION=1; it is networkless and
uses synthetic records exclusively.
"""

import json
from functools import lru_cache
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from urllib.parse import quote

import yaml


ROOT = Path(__file__).resolve().parents[2]
FLUENTD = ROOT / "platform/base/fluentd"
OPENSEARCH = ROOT / "platform/base/opensearch"
CONFIGMAP = FLUENTD / "configmap.yaml"
HOOK = FLUENTD / "log-schema-job.yaml"
FLUENTD_KUSTOMIZATION = FLUENTD / "kustomization.yaml"
OPENSEARCH_KUSTOMIZATION = OPENSEARCH / "kustomization.yaml"
OLD_HOOK = OPENSEARCH / "index-template-job.yaml"
FLUENTD_APP = ROOT / "apps/platform/fluentd.yaml"
OPENSEARCH_APP = ROOT / "apps/platform/opensearch.yaml"
IMAGE = "digiorg/fluentd:v1.19.2-debian-opensearch-1.0"
CURL_IMAGE = (
    "curlimages/curl:8.16.0@sha256:"
    "463eaf6072688fe96ac64fa623fe73e1dbe25d8ad6c34404a669ad3ce1f104b6"
)

CROSSPLANE = json.loads(
    '{"level":"info","request":{"method":"POST","body":{"safe":"fixture"}},'
    '"message":"reconcile complete"}'
)
EXTERNAL_SECRETS = json.loads(
    '{"level":"debug","ts":1725037200.25,"message":"refresh complete"}'
)
NON_STRING_LEVELS = [
    json.loads('{"level":7,"request":{"fixture":true}}'),
    json.loads('{"level":null,"ts":1.5}'),
    json.loads('{"request":{"fixture":true}}'),
]


def read(path):
    return path.read_text(encoding="utf-8")


def load(path):
    return yaml.safe_load(read(path))


def fluent_conf():
    return load(CONFIGMAP)["data"]["fluent.conf"]


def parser_filter(conf):
    match = re.search(
        r"(?ms)^# FILTER: Parse structured JSON logs.*?"
        r"^(<filter raw\.kubernetes\.\*\*>.*?^</filter>)",
        conf,
    )
    if not match:
        raise AssertionError("production structured-log parser filter was not found")
    return textwrap.dedent(match.group(1))


def promotion_filter(conf):
    match = re.search(
        r"(?ms)^# FILTER: Promote governed string log level.*?"
        r"^(<filter raw\.kubernetes\.\*\*>.*?^</filter>)",
        conf,
    )
    if not match:
        raise AssertionError("production governed-level promotion filter was not found")
    return textwrap.dedent(match.group(1))


@lru_cache(maxsize=1)
def hook_doc():
    if not HOOK.exists():
        raise AssertionError(f"Fluentd-owned schema hook is missing: {HOOK}")
    executable = shutil.which("kustomize")
    if not executable:
        # The platform-regression CI job intentionally installs only Python and
        # PyYAML; its separate render job owns Kustomize availability. Locally
        # and in that render environment, always take the rendered path below.
        return load(HOOK)
    rendered = subprocess.run(
        [executable, "build", str(FLUENTD)],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if rendered.returncode:
        raise AssertionError("Fluentd base did not render: " + rendered.stderr)
    docs = [doc for doc in yaml.safe_load_all(rendered.stdout) if doc]
    matches = [
        doc for doc in docs
        if doc.get("kind") == "Job"
        and doc.get("metadata", {}).get("name") == "fluentd-log-schema"
    ]
    if len(matches) != 1:
        raise AssertionError("rendered Fluentd base must contain exactly one schema Job")
    return matches[0]


def hook_container(doc=None):
    doc = doc or hook_doc()
    return doc["spec"]["template"]["spec"]["containers"][0]


def hook_script():
    container = hook_container()
    args = container.get("args", [])
    if len(args) != 1 or not isinstance(args[0], str):
        raise AssertionError("schema hook must expose one rendered shell script argument")
    return args[0]


def desired_properties_from_script(script):
    payload_match = re.search(
        r"(?ms)TEMPLATE_PAYLOAD='(\{.*?\})'\s*$", script
    )
    if not payload_match:
        raise AssertionError("hook must define TEMPLATE_PAYLOAD as literal JSON")
    payload = json.loads(payload_match.group(1))
    return payload["template"]["mappings"]["properties"]


class RecordShapeContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.conf = fluent_conf()

    def test_parser_nests_complete_arbitrary_payload(self):
        block = parser_filter(self.conf)
        self.assertRegex(block, r"(?m)^\s*hash_value_field\s+structured\s*$")
        self.assertRegex(block, r"(?m)^\s*reserve_data\s+true\s*$")
        self.assertRegex(block, r"(?m)^\s*remove_key_name_field\s+false\s*$")
        for payload in (CROSSPLANE, EXTERNAL_SECRETS, *NON_STRING_LEVELS):
            self.assertIsInstance(payload, dict)
            self.assertTrue(payload)

    def test_no_arbitrary_payload_key_is_promoted(self):
        block = parser_filter(self.conf)
        self.assertIn("hash_value_field structured", block)
        for arbitrary in ("request", "ts", "message"):
            self.assertNotRegex(
                promotion_filter(self.conf),
                rf"record\[\"structured\"\]\[\"{arbitrary}\"\]",
            )

    def test_only_string_structured_level_is_promoted(self):
        block = promotion_filter(self.conf)
        self.assertIn('record["structured"]["level"].is_a?(String)', block)
        self.assertIn('? record["structured"]["level"] : nil', block)
        self.assertNotIn('record["level"] =', block)
        self.assertEqual(CROSSPLANE["level"], "info")
        self.assertTrue(all(not isinstance(p.get("level"), str) for p in NON_STRING_LEVELS))

    def test_raw_log_and_kubernetes_envelope_are_reserved(self):
        block = parser_filter(self.conf)
        self.assertIn("key_name log", block)
        self.assertIn("reserve_data true", block)
        self.assertIn("remove_key_name_field false", block)
        self.assertIn("kubernetes", self.conf)
        self.assertIn("namespace_name", self.conf)
        self.assertIn("pod_name", self.conf)
        self.assertIn("container_name", self.conf)

    def test_plain_text_fallback_invents_no_structured_schema(self):
        block = parser_filter(self.conf)
        self.assertIn("@type json", block)
        self.assertIn("emit_invalid_record_to_error false", block)
        self.assertNotRegex(block, r"(?m)^\s*format none\s*$")
        plain = "plain fixture log; no credentials or real data"
        self.assertRaises(json.JSONDecodeError, json.loads, plain)

    def test_label_sanitization_is_unchanged(self):
        self.assertIn("k[\"labels\"].map", self.conf)
        self.assertIn("k[\"namespace_labels\"].map", self.conf)
        self.assertIn("key.gsub(/[.\\/]/, '_')", self.conf)


class TemplateContractTest(unittest.TestCase):
    def test_complete_template_has_isolated_payload_and_compatible_level(self):
        props = desired_properties_from_script(hook_script())
        self.assertEqual(props["structured"], {"type": "flat_object"})
        self.assertEqual(props["level"]["type"], "text")
        self.assertEqual(props["level"]["fields"]["keyword"]["type"], "keyword")
        self.assertEqual(props["level"]["fields"]["keyword"]["ignore_above"], 256)
        self.assertEqual(props["@timestamp"]["type"], "date")
        self.assertEqual(props["stream"]["type"], "keyword")
        for field in ("log", "message"):
            self.assertEqual(props[field]["type"], "text")
            self.assertEqual(props[field]["fields"]["keyword"]["type"], "keyword")
        kube = props["kubernetes"]["properties"]
        for field in ("namespace_name", "pod_name", "container_name", "host"):
            self.assertEqual(kube[field]["type"], "keyword")
        for field in ("labels", "namespace_labels"):
            self.assertEqual(kube[field]["type"], "flat_object")

    def test_template_reconciliation_is_unconditional(self):
        script = hook_script()
        put = script.index('"${OPENSEARCH_URL}/_index_template/${TEMPLATE_NAME}"')
        self.assertNotIn("already exists", script[:put].lower())
        self.assertNotRegex(script, r'HTTP_STATUS=.*_index_template')


FAKE_CURL = r'''#!/usr/bin/env python3
import json
import os
import sys
from urllib.parse import unquote, urlsplit

state_path = os.environ["FAKE_OPENSEARCH_STATE"]
with open(state_path, encoding="utf-8") as fh:
    state = json.load(fh)

args = sys.argv[1:]
method = "GET"
data = None
url = None
required = {"--fail": False, "--silent": False, "--show-error": False,
            "--connect-timeout": False, "--max-time": False, "--retry": False}
i = 0
while i < len(args):
    arg = args[i]
    if arg in required:
        required[arg] = True
        if arg in ("--connect-timeout", "--max-time", "--retry"):
            i += 1
            if i >= len(args) or not args[i].isdigit():
                sys.stderr.write("bounded curl option lacks numeric value\n")
                sys.exit(97)
    elif arg in ("--retry-delay",):
        i += 1
        if i >= len(args) or not args[i].isdigit():
            sys.exit(97)
    elif arg == "--retry-connrefused":
        pass
    elif arg in ("-X", "--request"):
        i += 1
        method = args[i]
    elif arg in ("-H", "--header"):
        i += 1
        if args[i] != "Content-Type: application/json":
            sys.stderr.write("unexpected header\n")
            sys.exit(97)
    elif arg in ("-d", "--data", "--data-raw"):
        i += 1
        data = args[i]
    elif arg.startswith("http://"):
        if url is not None:
            sys.exit(97)
        url = arg
    else:
        sys.stderr.write("unexpected curl argument: %s\n" % arg)
        sys.exit(97)
    i += 1

missing = [key for key, present in required.items() if not present]
if missing or url is None:
    sys.stderr.write("unbounded curl call or missing URL: %r\n" % missing)
    sys.exit(97)

parts = urlsplit(url)
if parts.netloc != "opensearch-cluster-master.platform-db.svc.cluster.local:9200":
    sys.stderr.write("unexpected OpenSearch authority\n")
    sys.exit(97)
path = unquote(parts.path)
entry = {
    "method": method,
    "path": path,
    "raw_path": parts.path,
    "query": parts.query,
    "data": data,
}
state.setdefault("calls", []).append(entry)

def save():
    with open(state_path, "w", encoding="utf-8") as fh:
        json.dump(state, fh, sort_keys=True)

def fail(message, code=22):
    save()
    sys.stderr.write(message + "\n")
    sys.exit(code)

if method == "GET" and path == "/_cluster/health":
    if "wait_for_status=yellow" not in parts.query or "timeout=" not in parts.query:
        fail("health request is not bounded to acceptable status", 97)
    if state.get("health_failure"):
        fail("synthetic health failure")
    output = {"status": "yellow", "timed_out": False}
elif method == "PUT" and path == "/_index_template/digiorg-logs-template":
    try:
        state["template"] = json.loads(data)
    except Exception:
        fail("invalid template JSON", 97)
    output = {"acknowledged": True}
elif method == "GET" and path == "/_index_template/digiorg-logs-template":
    template = state.get("template")
    if template is None:
        fail("template absent")
    if state.get("lie_template_verify"):
        template = {"template": {"mappings": {"properties": {"structured": {"type": "object"}}}}}
    output = {"index_templates": [{"name": "digiorg-logs-template", "index_template": template}]}
elif method == "GET" and path == "/_cat/indices/digiorg-logs-*":
    if "expand_wildcards=open" not in parts.query or "h=index" not in parts.query:
        fail("index enumeration is not restricted to open named log indices", 97)
    include_hidden = "expand_wildcards=open,hidden" in parts.query
    names = sorted(
        name for name, value in state.get("indices", {}).items()
        if value.get("open") and (not value.get("hidden") or include_hidden)
    )
    save()
    sys.stdout.write("\n".join(names) + ("\n" if names else ""))
    sys.exit(0)
elif path.startswith("/digiorg-logs-") and path.endswith("/_mapping"):
    name = path[1:-len("/_mapping")]
    if name not in state.get("indices", {}) or not state["indices"][name].get("open"):
        fail("mapping endpoint is not an existing open log index", 97)
    mapping = state["indices"][name]
    if method == "PUT":
        if name in state.get("fail_mapping_put", []):
            fail("synthetic mapping update failure")
        try:
            desired = json.loads(data)["properties"]
        except Exception:
            fail("invalid mapping update JSON", 97)
        wanted = {
            "structured": desired["structured"]["type"],
            "level": desired["level"]["type"],
            "keyword": desired["level"]["fields"]["keyword"]["type"],
        }
        for key, value in wanted.items():
            if mapping.get(key) not in (None, value):
                fail("synthetic incompatible mapping for %s" % key)
        mapping.update(wanted)
        output = {"acknowledged": True}
    elif method == "GET":
        shown = dict(mapping)
        if name in state.get("lie_mapping_verify", []):
            shown["structured"] = "object"
        properties = {}
        if shown.get("structured"):
            properties["structured"] = {"type": shown["structured"]}
        if shown.get("level"):
            properties["level"] = {"type": shown["level"], "fields": {
                "keyword": {"type": shown.get("keyword", "keyword"), "ignore_above": 256}}}
        output = {name: {"mappings": {"properties": properties}}}
    else:
        fail("unsupported mapping method", 97)
else:
    fail("unexpected endpoint: %s %s" % (method, path), 97)

save()
sys.stdout.write(json.dumps(output, separators=(",", ":")))
'''


class HookHarness(unittest.TestCase):
    def initial_state(self, indices=None, template=None, **extra):
        state = {"template": template, "indices": indices or {}, "calls": []}
        state.update(extra)
        return state

    def run_hook(self, state, working_files=()):
        script = hook_script()
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)
            fake = temp_path / "curl"
            fake.write_text(FAKE_CURL, encoding="utf-8")
            fake.chmod(0o755)
            state_path = temp_path / "state.json"
            state_path.write_text(json.dumps(state), encoding="utf-8")
            env = os.environ.copy()
            env["PATH"] = str(temp_path) + os.pathsep + env["PATH"]
            env["FAKE_OPENSEARCH_STATE"] = str(state_path)
            for name in working_files:
                (temp_path / name).touch()
            result = subprocess.run(
                ["/bin/sh", "-c", script],
                cwd=temp_path,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=15,
                check=False,
            )
            final = json.loads(state_path.read_text(encoding="utf-8"))
        return result, final

    def assert_success(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_clean_bootstrap_upserts_and_verifies_template(self):
        result, state = self.run_hook(self.initial_state())
        self.assert_success(result)
        props = state["template"]["template"]["mappings"]["properties"]
        self.assertEqual(props["structured"]["type"], "flat_object")
        self.assertEqual(props["level"]["fields"]["keyword"]["type"], "keyword")
        methods = [(call["method"], call["path"]) for call in state["calls"]]
        self.assertIn(("GET", "/_index_template/digiorg-logs-template"), methods)
        self.assertFalse(any(path.endswith("/_mapping") for _, path in methods))

    def test_unacceptable_cluster_health_is_fatal(self):
        result, state = self.run_hook(self.initial_state(health_failure=True))
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(call["method"] == "PUT" for call in state["calls"]))

    def test_template_readback_verification_failure_is_fatal(self):
        result, _ = self.run_hook(self.initial_state(lie_template_verify=True))
        self.assertNotEqual(result.returncode, 0)

    def test_stale_template_is_replaced_not_skipped(self):
        stale = {"index_patterns": ["wrong-*"], "template": {"mappings": {"properties": {}}}}
        result, state = self.run_hook(self.initial_state(template=stale))
        self.assert_success(result)
        self.assertEqual(state["template"]["index_patterns"], ["digiorg-logs-*"])
        self.assertIn("structured", state["template"]["template"]["mappings"]["properties"])

    def test_compatible_indices_are_additively_idempotent(self):
        indices = {
            "digiorg-logs-2026.08.29": {"open": True, "structured": "flat_object", "level": "text", "keyword": "keyword", "historical": True},
            "digiorg-logs-2026.08.30": {"open": True, "historical": True},
            "digiorg-logs-closed": {"open": False, "historical": True},
        }
        result, state = self.run_hook(self.initial_state(indices=indices))
        self.assert_success(result)
        result, state = self.run_hook(state)
        self.assert_success(result)
        for name in ("digiorg-logs-2026.08.29", "digiorg-logs-2026.08.30"):
            self.assertEqual(state["indices"][name]["structured"], "flat_object")
            self.assertEqual(state["indices"][name]["level"], "text")
            self.assertTrue(state["indices"][name]["historical"])
        self.assertNotIn("structured", state["indices"]["digiorg-logs-closed"])

    def test_partial_failure_resumes_without_rewriting_history(self):
        indices = {
            "digiorg-logs-a": {"open": True, "historical": True},
            "digiorg-logs-b": {"open": True, "historical": True},
        }
        result, state = self.run_hook(
            self.initial_state(indices=indices, fail_mapping_put=["digiorg-logs-b"])
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(state["indices"]["digiorg-logs-a"]["structured"], "flat_object")
        self.assertNotIn("structured", state["indices"]["digiorg-logs-b"])
        state["fail_mapping_put"] = []
        result, state = self.run_hook(state)
        self.assert_success(result)
        self.assertEqual(state["indices"]["digiorg-logs-b"]["structured"], "flat_object")
        self.assertTrue(all(item["historical"] for item in state["indices"].values()))

    def test_valid_reserved_index_character_is_url_encoded_and_migrated(self):
        name = "digiorg-logs-a+b"
        result, state = self.run_hook(
            self.initial_state(indices={name: {"open": True, "historical": True}})
        )
        self.assert_success(result)
        self.assertEqual(state["indices"][name]["structured"], "flat_object")
        self.assertTrue(state["indices"][name]["historical"])
        mapping_calls = [
            call for call in state["calls"]
            if call["method"] == "PUT" and call["path"] == f"/{name}/_mapping"
        ]
        self.assertEqual(len(mapping_calls), 1)
        self.assertEqual(mapping_calls[0]["raw_path"], "/digiorg-logs-a%2Bb/_mapping")

    def test_valid_pattern_characters_are_not_expanded_by_the_shell(self):
        name = "digiorg-logs-a[bc]"
        result, state = self.run_hook(
            self.initial_state(indices={name: {"open": True, "historical": True}}),
            working_files=("digiorg-logs-ab",),
        )
        self.assert_success(result)
        self.assertEqual(state["indices"][name]["structured"], "flat_object")
        mapping_calls = [
            call for call in state["calls"]
            if call["method"] == "PUT" and call["path"] == f"/{name}/_mapping"
        ]
        self.assertEqual(len(mapping_calls), 1)
        self.assertEqual(
            mapping_calls[0]["raw_path"],
            "/digiorg-logs-a%5Bbc%5D/_mapping",
        )

    def test_open_hidden_matching_index_is_migrated(self):
        name = "digiorg-logs-hidden"
        result, state = self.run_hook(
            self.initial_state(
                indices={
                    name: {"open": True, "hidden": True, "historical": True},
                }
            )
        )
        self.assert_success(result)
        self.assertIn("structured", state["indices"][name])
        self.assertEqual(state["indices"][name]["structured"], "flat_object")
        enumeration = [
            call for call in state["calls"]
            if call["method"] == "GET" and call["path"] == "/_cat/indices/digiorg-logs-*"
        ]
        self.assertEqual(len(enumeration), 1)
        self.assertIn("expand_wildcards=open,hidden", enumeration[0]["query"])

    def test_incompatible_mapping_fails_closed_before_later_index(self):
        indices = {
            "digiorg-logs-a": {"open": True, "structured": "object", "historical": True},
            "digiorg-logs-z": {"open": True, "historical": True},
        }
        result, state = self.run_hook(self.initial_state(indices=indices))
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("structured", state["indices"]["digiorg-logs-z"])

    def test_readback_verification_failure_is_fatal(self):
        indices = {"digiorg-logs-a": {"open": True}}
        result, _ = self.run_hook(
            self.initial_state(indices=indices, lie_mapping_verify=["digiorg-logs-a"])
        )
        self.assertNotEqual(result.returncode, 0)

    def test_template_precedes_enumeration_and_every_index_update(self):
        indices = {"digiorg-logs-a": {"open": True}, "digiorg-logs-b": {"open": True}}
        result, state = self.run_hook(self.initial_state(indices=indices))
        self.assert_success(result)
        calls = state["calls"]
        template_put = next(i for i, c in enumerate(calls) if c["method"] == "PUT" and c["path"].startswith("/_index_template/"))
        enumeration = next(i for i, c in enumerate(calls) if c["path"] == "/_cat/indices/digiorg-logs-*")
        mapping_puts = [i for i, c in enumerate(calls) if c["method"] == "PUT" and c["path"].endswith("/_mapping")]
        self.assertLess(template_put, enumeration)
        self.assertTrue(mapping_puts)
        self.assertTrue(all(enumeration < i for i in mapping_puts))

    def test_no_destructive_or_broad_endpoints(self):
        script = hook_script().lower()
        for forbidden in ("_delete", "_close", "_rollover", "_reindex", "-x delete", "--request delete"):
            self.assertNotIn(forbidden, script)
        self.assertNotRegex(script, r'\$\{OPENSEARCH_URL\}/\*/')
        self.assertIn("/_cat/indices/digiorg-logs-*", script)
        self.assertIn("urlencode_path_segment", script)
        self.assertNotIn("unsafe log index name", script)


class OwnershipSecurityAndOrderingTest(unittest.TestCase):
    def setUp(self):
        self.assertTrue(HOOK.exists(), f"Fluentd-owned schema hook is missing: {HOOK}")
        self.doc = hook_doc()
        self.pod = self.doc["spec"]["template"]["spec"]
        self.container = hook_container(self.doc)

    def test_fluentd_owns_presync_hook_and_opensearch_keeps_only_ism(self):
        annotations = self.doc["metadata"]["annotations"]
        self.assertEqual(annotations["argocd.argoproj.io/hook"], "PreSync")
        self.assertIn("BeforeHookCreation", annotations["argocd.argoproj.io/hook-delete-policy"])
        self.assertIn(HOOK.name, load(FLUENTD_KUSTOMIZATION)["resources"])
        self.assertNotIn("index-template-job.yaml", load(OPENSEARCH_KUSTOMIZATION)["resources"])
        self.assertIn("ism-retention-job.yaml", load(OPENSEARCH_KUSTOMIZATION)["resources"])
        self.assertFalse(OLD_HOOK.exists())

    def test_hook_is_bounded_and_retry_safe(self):
        spec = self.doc["spec"]
        self.assertGreater(spec["activeDeadlineSeconds"], 0)
        self.assertLessEqual(spec["activeDeadlineSeconds"], 300)
        self.assertGreaterEqual(spec["backoffLimit"], 1)
        script = hook_script()
        for option in ("--connect-timeout", "--max-time", "--retry"):
            self.assertIn(option, script)

    def test_pod_and_container_are_hardened(self):
        self.assertFalse(self.pod["automountServiceAccountToken"])
        self.assertTrue(self.pod["securityContext"]["runAsNonRoot"])
        self.assertEqual(self.pod["securityContext"]["seccompProfile"]["type"], "RuntimeDefault")
        security = self.container["securityContext"]
        self.assertFalse(security["allowPrivilegeEscalation"])
        self.assertTrue(security["readOnlyRootFilesystem"])
        self.assertEqual(security["capabilities"]["drop"], ["ALL"])
        self.assertGreater(self.pod["securityContext"]["runAsUser"], 0)

    def test_image_endpoint_and_resources_are_exact(self):
        self.assertEqual(self.container["image"], CURL_IMAGE)
        resources = self.container["resources"]
        for scope in ("requests", "limits"):
            self.assertIn("cpu", resources[scope])
            self.assertIn("memory", resources[scope])
        script = hook_script()
        self.assertIn("http://opensearch-cluster-master.platform-db.svc.cluster.local:9200", script)
        self.assertNotRegex(script, re.compile(r"password|passwd|authorization|bearer", re.I))

    def test_same_application_presync_is_the_writer_safety_boundary(self):
        fluentd_text = read(FLUENTD_APP)
        opensearch_text = read(OPENSEARCH_APP)
        self.assertIn("PreSync", fluentd_text)
        self.assertIn("writer", fluentd_text.lower())
        self.assertIn("not", fluentd_text.lower())
        self.assertIn("readiness", fluentd_text.lower())
        self.assertIn("ISM", opensearch_text)
        self.assertIn("Fluentd", opensearch_text)


@unittest.skipUnless(
    os.environ.get("RUN_FLUENTD_SCHEMA_INTEGRATION") == "1",
    "set RUN_FLUENTD_SCHEMA_INTEGRATION=1 to run the pinned networkless image probe",
)
class PinnedFluentdImageIntegrationTest(unittest.TestCase):
    """Execute production filter fragments in the already-built pinned image."""

    def test_synthetic_records_have_the_production_shape(self):
        if not shutil.which("docker"):
            self.fail("Docker is required when RUN_FLUENTD_SCHEMA_INTEGRATION=1")
        inspect = subprocess.run(
            ["docker", "image", "inspect", IMAGE],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        self.assertEqual(inspect.returncode, 0, f"pinned local image missing: {IMAGE}")
        filters = parser_filter(fluent_conf()) + "\n" + promotion_filter(fluent_conf())
        fixtures = [CROSSPLANE, EXTERNAL_SECRETS, *NON_STRING_LEVELS]
        for payload in fixtures:
            envelope = {
                "stream": "stdout",
                "log": json.dumps(payload, separators=(",", ":")),
                "kubernetes": {
                    "namespace_name": "fixture-ns",
                    "pod_name": "fixture-pod",
                    "container_name": "fixture-container",
                    "labels": {"app.kubernetes.io/name": "fixture"},
                    "namespace_labels": {},
                },
            }
            config = textwrap.dedent(
                f"""
                <source>
                  @type dummy
                  tag raw.kubernetes.fixture
                  dummy {json.dumps(envelope, separators=(",", ":"))}
                </source>
                {filters}
                <match raw.kubernetes.**>
                  @type stdout
                </match>
                """
            )
            with tempfile.TemporaryDirectory() as temp:
                conf = Path(temp) / "fluent.conf"
                conf.write_text(config, encoding="utf-8")
                command = [
                    "docker", "run", "--rm", "--network", "none", "--read-only",
                    "--tmpfs", "/tmp", "--tmpfs", "/fluentd/log",
                    "--entrypoint", "fluentd",
                    "-v", f"{conf}:/fluentd/etc/fluent.conf:ro", IMAGE,
                    "-c", "/fluentd/etc/fluent.conf",
                ]
                try:
                    run = subprocess.run(
                        command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True, timeout=6, check=False,
                    )
                except subprocess.TimeoutExpired as exc:
                    output = exc.stdout or ""
                    if isinstance(output, bytes):
                        output = output.decode("utf-8", "replace")
                else:
                    output = run.stdout
            emitted = []
            for line in output.splitlines():
                start = line.find("{")
                if start < 0:
                    continue
                try:
                    candidate = json.loads(line[start:])
                except json.JSONDecodeError:
                    continue
                if candidate.get("structured") == payload:
                    emitted.append(candidate)
            self.assertTrue(emitted, output)
            record = emitted[0]
            self.assertEqual(record["log"], envelope["log"])
            self.assertEqual(record["kubernetes"], envelope["kubernetes"])
            self.assertNotIn("request", record)
            self.assertNotIn("ts", record)
            if isinstance(payload.get("level"), str):
                self.assertEqual(record.get("level"), payload["level"])
            else:
                self.assertIsNone(record.get("level"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
