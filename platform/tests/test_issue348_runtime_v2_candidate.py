#!/usr/bin/env python3
"""Immutable source-graph contracts for the Issue #348 runtime-v2 candidate."""

from pathlib import Path
from importlib.util import module_from_spec, spec_from_file_location
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[2]
CORE_REPO = "https://github.com/digiorg/core.git"
CANDIDATE_TAG = "issue348-runtime-v3-20260918T080337Z"
OLD_TAG = "issue301-runtime-v16-20260817T130820Z"
CATALOG_REVISION = "d531180b322dc0128477ecb9bb0fc9071b41d631"
TRANSITION = ROOT / "scripts/issue348_runtime_v2_transition.py"


def source_list(application):
    spec = application["spec"]
    return spec.get("sources", [spec.get("source")])


def source_identity(source):
    return tuple(
        source.get(key)
        for key in ("repoURL", "chart", "path", "ref", "targetRevision")
    )


class RuntimeSourceGraphTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        paths = [
            ROOT / "platform/base/argocd/applications/root-app.yaml",
            *sorted((ROOT / "apps/platform").glob("*.yaml")),
        ]
        cls.applications = {}
        for path in paths:
            application = yaml.safe_load(path.read_text(encoding="utf-8"))
            cls.applications[application["metadata"]["name"]] = application

    def test_candidate_promotes_only_required_ownership_and_schema_sources(self):
        candidate_sources = set()
        old_sources = set()
        other_sources = []
        for name, application in self.applications.items():
            for source in source_list(application):
                if source.get("repoURL") != CORE_REPO:
                    continue
                identity = (name, source.get("path"), source.get("ref"))
                target = source.get("targetRevision")
                if target == CANDIDATE_TAG:
                    candidate_sources.add(identity)
                elif target == OLD_TAG:
                    old_sources.add(identity)
                else:
                    other_sources.append((identity, target))

        self.assertEqual(
            candidate_sources,
            {
                ("root-app", "apps", None),
                ("argocd", "platform/base/argocd", None),
                ("fluentd", "platform/base/fluentd", None),
                ("opensearch", None, "values"),
                ("opensearch", "platform/base/opensearch", None),
            },
        )
        self.assertEqual(len(old_sources), 27)
        self.assertEqual(other_sources, [])

    def test_external_consumer_pins_are_unchanged(self):
        app_config = source_list(self.applications["app-config"])[0]
        self.assertEqual(
            source_identity(app_config),
            (
                "https://digiorg.local/gitea/DigiOrg/app-config.git",
                None,
                "claims",
                None,
                "main",
            ),
        )
        catalog = source_list(self.applications["core-catalog"])[0]
        self.assertEqual(
            source_identity(catalog),
            (
                "https://github.com/digiorg/core-catalog.git",
                None,
                "compositions/local",
                None,
                CATALOG_REVISION,
            ),
        )

    def test_issue350_memory_headroom_remains_exact(self):
        values = yaml.safe_load(
            (ROOT / "platform/base/opensearch/values.yaml").read_text(encoding="utf-8")
        )
        self.assertEqual(values["resources"]["requests"]["memory"], "1Gi")
        self.assertEqual(values["resources"]["limits"]["memory"], "2Gi")
        self.assertEqual(values["opensearchJavaOpts"], "-Xmx512M -Xms512M")


class TransitionModuleContractTest(unittest.TestCase):
    def test_transition_binds_current_candidate_and_retained_runtime(self):
        self.assertTrue(TRANSITION.exists(), "Issue #348 transition module is missing")
        spec = spec_from_file_location("issue348_transition", TRANSITION)
        assert spec and spec.loader
        module = module_from_spec(spec)
        spec.loader.exec_module(module)

        self.assertEqual(module.RUNTIME_TAG, CANDIDATE_TAG)
        self.assertEqual(module.PRODUCT_BASE_COMMIT, "ff25a5083059412f82525ace73e7c20b322fddbf")
        self.assertEqual(module.CANDIDATE_BASE_COMMIT, "86ddf1484c79cbf49233787a5a023009f3577181")
        self.assertEqual(module.PREVIOUS_TAG, "issue350-352-runtime-v3-20260904T195619Z")
        self.assertEqual(module.PREVIOUS_COMMIT, "f6e7d58c0b03ee6a3ec6ed9e1e22e5023f861549")
        self.assertEqual(module.OLD_TAG, OLD_TAG)
        clean_targets = [
            identity[-1]
            for identities in module.CLEAN_SOURCE_GRAPH.values()
            for identity in identities
            if identity[0] == CORE_REPO
        ]
        preflight_targets = [
            identity[-1]
            for identities in module.PREFLIGHT_SOURCE_GRAPH.values()
            for identity in identities
            if identity[0] == CORE_REPO
        ]
        self.assertEqual(clean_targets.count(CANDIDATE_TAG), 5)
        self.assertEqual(clean_targets.count(OLD_TAG), 27)
        self.assertEqual(preflight_targets.count(module.PREVIOUS_TAG), 3)
        self.assertEqual(preflight_targets.count(OLD_TAG), 29)


if __name__ == "__main__":
    unittest.main(verbosity=2)
