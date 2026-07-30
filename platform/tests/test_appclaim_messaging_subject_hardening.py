#!/usr/bin/env python3
"""Kubernetes-aware Application XRD messaging-subject regression.

Issue #290: Kubernetes 1.36 rejects the generated Application and AppClaim
CRDs when ``spec.messaging.subjects`` uses JSON Schema ``uniqueItems: true``.
Kubernetes' native ``x-kubernetes-list-type: set`` preserves exact duplicate
rejection without the quadratic ``uniqueItems`` validation cost. These tests
exercise that set contract, retain the NATS subject validation from Issue
#285, and inspect realistic generated CRD schemas for the incompatible keyword.

Run:
    python3 platform/tests/test_appclaim_messaging_subject_hardening.py
"""

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
    return {"appName": "myapp", "team": "platform-team", "size": "S"}


def _validator(schema):
    jsonschema.Draft7Validator.check_schema(schema)
    return jsonschema.Draft7Validator(schema)


def _document_with_subjects(subjects):
    spec = _base_spec()
    spec["messaging"] = {"enabled": True, "subjects": subjects}
    return {"spec": spec}


def _is_valid(validator, document):
    return len(list(validator.iter_errors(document))) == 0


def _is_admitted_by_kubernetes(validator, subjects_schema, document):
    """Apply JSON Schema plus Kubernetes scalar-set duplicate semantics."""
    if not _is_valid(validator, document):
        return False
    subjects = document["spec"]["messaging"]["subjects"]
    if subjects_schema.get("x-kubernetes-list-type") == "set":
        return len(subjects) == len(set(subjects))
    return True


def _generated_crds(schema):
    """Model the two CRD envelopes Crossplane generates from this XRD."""
    with open(XRD, encoding="utf-8") as fh:
        xrd = yaml.safe_load(fh)
    version = xrd["spec"]["versions"][0]
    common = {
        "apiVersion": "apiextensions.k8s.io/v1",
        "kind": "CustomResourceDefinition",
    }
    crds = []
    for names, scope in (
        (xrd["spec"]["names"], "Cluster"),
        (xrd["spec"]["claimNames"], "Namespaced"),
    ):
        crds.append(
            {
                **common,
                "metadata": {
                    "name": f"{names['plural']}.{xrd['spec']['group']}",
                },
                "spec": {
                    "group": xrd["spec"]["group"],
                    "scope": scope,
                    "names": {
                        "kind": names["kind"],
                        "plural": names["plural"],
                    },
                    "versions": [
                        {
                            "name": version["name"],
                            "served": version["served"],
                            "storage": True,
                            "schema": {"openAPIV3Schema": schema},
                        }
                    ],
                },
            }
        )
    return crds


def _find_keyword(value, keyword, path="$"):
    found = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if key == keyword:
                found.append(child_path)
            found.extend(_find_keyword(child, keyword, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(_find_keyword(child, keyword, f"{path}[{index}]"))
    return found


SAFE_SUBJECTS = [
    "myapp.events.>",
    "myapp.commands",
    "myapp.audit.v1",
    "myapp-events",
    "myapp.Events",
]

ADVERSARIAL_SUBJECTS = [
    ">>>",
    "",
    "..",
    ".leading-dot",
    "trailing-dot.",
    "a b",
    "a\nb",
    "a;touch pwned",
    "a$(touch pwned)",
]


class MessagingSubjectsSchemaTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        schema = _load_schema()
        cls.validator = _validator(schema)
        cls.subjects_schema = schema["properties"]["spec"]["properties"]["messaging"]["properties"][
            "subjects"
        ]

    def test_uses_kubernetes_scalar_set_semantics_not_unique_items(self):
        self.assertNotIn(
            "uniqueItems",
            self.subjects_schema,
            "uniqueItems is rejected by Kubernetes 1.36 for generated CRDs",
        )
        self.assertEqual(self.subjects_schema.get("x-kubernetes-list-type"), "set")
        self.assertEqual(self.subjects_schema["items"].get("type"), "string")

    def test_item_pattern_and_max_length_are_declared(self):
        item_schema = self.subjects_schema["items"]
        self.assertIn("pattern", item_schema)
        self.assertIn("maxLength", item_schema)
        pattern = item_schema["pattern"]
        for forbidden in ("(?=", "(?!", "(?<", "\\1", "\\2"):
            self.assertNotIn(forbidden, pattern)

    def test_safe_subjects_are_accepted(self):
        for subject in SAFE_SUBJECTS:
            with self.subTest(subject=repr(subject)):
                doc = _document_with_subjects([subject])
                self.assertTrue(_is_valid(self.validator, doc), "subject %r must be accepted" % (subject,))

    def test_adversarial_subjects_are_schema_rejected(self):
        for subject in ADVERSARIAL_SUBJECTS:
            with self.subTest(subject=repr(subject)):
                doc = _document_with_subjects([subject])
                self.assertFalse(
                    _is_valid(self.validator, doc), "subject %r must be schema-rejected" % (subject,)
                )

    def test_exact_duplicate_subjects_are_rejected_by_kubernetes_set(self):
        self.assertEqual(self.subjects_schema.get("x-kubernetes-list-type"), "set")
        doc = _document_with_subjects(["myapp.events", "myapp.events"])
        self.assertFalse(
            _is_admitted_by_kubernetes(self.validator, self.subjects_schema, doc)
        )

    def test_case_and_separator_variants_are_not_duplicates(self):
        # These are distinct, schema-valid subjects even though a naive
        # sanitizer would collapse them to the same string -- uniqueItems
        # must not reject them (that collision is a render-time naming
        # problem, not a schema-level duplicate).
        doc = _document_with_subjects(["myapp.events", "myapp-events", "myapp.Events"])
        self.assertTrue(
            _is_admitted_by_kubernetes(self.validator, self.subjects_schema, doc)
        )

    def test_generated_crd_schemas_are_kubernetes_1_36_compatible(self):
        crds = _generated_crds(_load_schema())
        self.assertEqual(
            [crd["metadata"]["name"] for crd in crds],
            [
                "applications.platform.digiorg.io",
                "appclaims.platform.digiorg.io",
            ],
        )
        for crd in crds:
            with self.subTest(crd=crd["metadata"]["name"]):
                generated_schema = crd["spec"]["versions"][0]["schema"][
                    "openAPIV3Schema"
                ]
                jsonschema.Draft7Validator.check_schema(generated_schema)
                self.assertEqual(
                    _find_keyword(generated_schema, "uniqueItems"),
                    [],
                    "generated CRD schema contains Kubernetes 1.36-incompatible "
                    "uniqueItems",
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
