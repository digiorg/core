#!/usr/bin/env python3
"""Regression tests for the platform image/chart pin policy (Issue #275).

These tests guard the Tier-2 "Harbor mirror plus digest pinning" contract and
the platform-wide "no floating references" validation requirement:

  * every container `image:` in a rendered Kubernetes manifest is pinned to an
    immutable ``@sha256:`` digest (or is an explicitly documented exception);
  * every Argo CD Helm ``chart:`` source pins an exact SemVer (no ``latest``,
    no branch, no range/wildcard);
  * every non-first-party git source pins an immutable 40-hex commit.

They exercise the *behaviour* of ``scripts/check_pins.py`` on synthetic inputs
(so the rules themselves are proven, not just asserted to exist) and then run
the real checker over the real tree, which must be clean.

No cluster, pytest, or network access required::

    python3 platform/tests/test_pin_policy.py
"""

import os
import sys
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))

import check_pins  # noqa: E402  (path injected above)


def _images_from(text):
    """Violations for image refs written to a temp manifest with the given body."""
    return check_pins.scan_text("m.yaml", text)


class ImageDigestRuleTest(unittest.TestCase):
    def test_latest_tag_is_rejected(self):
        v = _images_from("spec:\n  containers:\n  - image: natsio/nats-surveyor:latest\n")
        self.assertTrue(any(x.kind == "image" for x in v))
        self.assertTrue(any("latest" in x.message or "digest" in x.message for x in v))

    def test_branch_tag_is_rejected(self):
        v = _images_from("    image: ghcr.io/digiorg/core-landingpage:main\n")
        self.assertTrue(any(x.kind == "image" for x in v))

    def test_bare_tag_without_digest_is_rejected(self):
        v = _images_from("    image: postgres:16-alpine\n")
        self.assertEqual([x.kind for x in v], ["image"])

    def test_digest_pinned_image_is_accepted(self):
        ref = "postgres:16-alpine@sha256:" + "0" * 64
        v = _images_from(f"    image: {ref}\n")
        self.assertEqual(v, [])

    def test_allowlisted_local_image_is_accepted(self):
        # The kind-loaded, locally built images are documented exceptions.
        allow = {"opencost-ui:v1.120.4-basename-opencost"}
        v = check_pins.scan_text(
            "m.yaml", "    image: opencost-ui:v1.120.4-basename-opencost\n", allow=allow
        )
        self.assertEqual(v, [])


class ChartVersionRuleTest(unittest.TestCase):
    def _chart(self, rev):
        return (
            "spec:\n  sources:\n"
            "  - repoURL: https://charts.example.com\n"
            "    chart: thing\n"
            f'    targetRevision: "{rev}"\n'
        )

    def test_exact_semver_chart_is_accepted(self):
        self.assertEqual(_images_from(self._chart("1.43.2")), [])

    def test_range_chart_is_rejected(self):
        v = _images_from(self._chart("^1.43.0"))
        self.assertTrue(any(x.kind == "chart" for x in v))

    def test_wildcard_chart_is_rejected(self):
        v = _images_from(self._chart("1.43.x"))
        self.assertTrue(any(x.kind == "chart" for x in v))

    def test_missing_chart_pin_is_rejected(self):
        text = (
            "spec:\n  source:\n"
            "    repoURL: https://charts.crossplane.io/stable\n"
            "    chart: crossplane\n"
        )
        v = _images_from(text)
        self.assertTrue(any(x.kind == "chart" for x in v))


class GitSourceRuleTest(unittest.TestCase):
    def test_first_party_main_is_accepted(self):
        text = (
            "spec:\n  source:\n"
            "    repoURL: https://github.com/digiorg/core.git\n"
            "    targetRevision: main\n"
            "    path: platform/base/keycloak\n"
        )
        self.assertEqual(_images_from(text), [])

    def test_third_party_branch_is_rejected(self):
        text = (
            "spec:\n  source:\n"
            "    repoURL: https://github.com/digiorg/core-catalog.git\n"
            "    targetRevision: main\n"
            "    path: compositions/local\n"
        )
        v = _images_from(text)
        self.assertTrue(any(x.kind == "git" for x in v))

    def test_third_party_commit_sha_is_accepted(self):
        text = (
            "spec:\n  source:\n"
            "    repoURL: https://github.com/digiorg/core-catalog.git\n"
            "    targetRevision: 0ced57eb40a50ec170352ce4a59bffedd4a16a6c\n"
            "    path: compositions/local\n"
        )
        self.assertEqual(_images_from(text), [])


class RealTreeIsCleanTest(unittest.TestCase):
    """The committed manifests must satisfy the pin policy end-to-end."""

    def test_repo_has_no_pin_violations(self):
        violations = check_pins.scan_repo(REPO_ROOT)
        detail = "\n".join(f"  {v.file}:{v.line} [{v.kind}] {v.ref} — {v.message}" for v in violations)
        self.assertEqual(violations, [], f"\nPin-policy violations:\n{detail}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
