#!/usr/bin/env python3
"""Issue #285 runtime blocker (JetStream subject collision): the Application
XRD's `spec.messaging.subjects[]` previously accepted any string with no
`uniqueItems` constraint. This locks the schema-level fix, mirroring
`test_appclaim_service_field_hardening.py`'s style: both a `pattern`
rejecting unsafe NATS subjects (e.g. a bare `>` is only valid as the final,
whole token -- `>>>` is never a valid subject) and `uniqueItems: true` on
the array (rejecting literal duplicate subjects outright), while still
accepting subjects that only *collide after sanitization* (case/separator
variants) -- the render-side fix for those lives in core-catalog's pipeline
Composition (index-based Stream/Consumer naming).

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

    def test_unique_items_is_declared(self):
        self.assertTrue(self.subjects_schema.get("uniqueItems"))

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

    def test_duplicate_subjects_are_rejected_by_unique_items(self):
        doc = _document_with_subjects(["myapp.events", "myapp.events"])
        self.assertFalse(_is_valid(self.validator, doc))

    def test_case_and_separator_variants_are_not_duplicates(self):
        # These are distinct, schema-valid subjects even though a naive
        # sanitizer would collapse them to the same string -- uniqueItems
        # must not reject them (that collision is a render-time naming
        # problem, not a schema-level duplicate).
        doc = _document_with_subjects(["myapp.events", "myapp-events", "myapp.Events"])
        self.assertTrue(_is_valid(self.validator, doc))


if __name__ == "__main__":
    unittest.main(verbosity=2)
