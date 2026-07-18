#!/usr/bin/env python3
# =============================================================================
# Regression tests for the DigiOrg pinned Fluentd image — Issue #275 (Tier 1)
#
# Architecture under test (DigiOrg-owned Fluentd log-forwarder image):
#
#   * The stock daemonset ran fluent/fluentd-kubernetes-daemonset with a FLOATING
#     minor tag (v1.19-debian-opensearch-1) and relied on whatever plugin
#     versions the upstream image happened to ship — non-reproducible.
#
#   * Tier 1 replaces it with a pinned image built by
#     platform/images/fluentd/build.nu FROM the digest-pinned upstream base, into
#     which EXPLICITLY PINNED plugin gem versions are installed. The daemonset
#     selects the exact allow-listed local image with IfNotPresent and keeps all
#     of its env/volumes/ports/resources intact.
#
# These tests parse/assert the REAL build definition and the REAL daemonset
# (behaviour, not a single grepped string):
#
#   BuildContractTest      build.nu (Nushell, cross-platform) pins the upstream
#                          tag + immutable base digest, verifies the tag still
#                          resolves to that digest before building, passes
#                          version/base-digest provenance args, keeps local kind
#                          and Harbor coordinates aligned, exposes REGISTRY/
#                          KIND_CLUSTER/LOAD/PUSH overrides, and embeds no
#                          credentials.
#   DockerfileContractTest a build FROM the digest-pinned base installs EXACT,
#                          version-pinned plugin gems (opensearch +
#                          kubernetes_metadata_filter) and emits OCI labels.
#   DaemonsetWiringTest    daemonset.yaml selects the exact allow-listed image
#                          with IfNotPresent and preserves every env var, volume,
#                          port and resource request/limit.
#
# Runs with the standard library + PyYAML only (no pytest, cluster or network):
#     python3 platform/tests/test_fluentd_image.py
# =============================================================================
import os
import re
import unittest

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
IMAGE_DIR = os.path.join(REPO_ROOT, "platform", "images", "fluentd")
BUILD_NU = os.path.join(IMAGE_DIR, "build.nu")
DOCKERFILE = os.path.join(IMAGE_DIR, "Dockerfile")
DAEMONSET = os.path.join(REPO_ROOT, "platform", "base", "fluentd", "daemonset.yaml")

UPSTREAM_IMAGE = "fluent/fluentd-kubernetes-daemonset"
UPSTREAM_TAG = "v1.19.2-debian-opensearch-1.0"
UPSTREAM_DIGEST = "sha256:f7a636ae892cab78b0e63fcb4d89f68a84bd602609a7f38b00fcc5c65f487de2"
IMAGE = "digiorg/fluentd"
TAG = "v1.19.2-debian-opensearch-1.0"
FULL_IMAGE = "%s:%s" % (IMAGE, TAG)

# Plugins that MUST be explicitly version-pinned in the image.
REQUIRED_PINNED_PLUGINS = (
    "fluent-plugin-opensearch",
    "fluent-plugin-kubernetes_metadata_filter",
)


def read_text(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def load_yaml_docs(path):
    with open(path, "r", encoding="utf-8") as fh:
        return [d for d in yaml.safe_load_all(fh) if d is not None]


# =========================================================================== #
class BuildContractTest(unittest.TestCase):
    """The build must be pinned, reproducible, credential-free, cross-platform."""

    @classmethod
    def setUpClass(cls):
        cls.script = read_text(BUILD_NU)

    def _const(self, name):
        m = re.search(
            r'^\s*const\s+%s\s*=\s*"([^"\n]+)"' % re.escape(name), self.script, re.M
        )
        self.assertIsNotNone(m, "build.nu must define const %s" % name)
        return m.group(1).strip()

    def _env_default(self, name):
        m = re.search(
            r'\$env\.%s\??\s*\|\s*default\s+"([^"\n]+)"' % re.escape(name), self.script
        )
        self.assertIsNotNone(
            m, "build.nu must expose an $env.%s override with a default" % name
        )
        return m.group(1).strip()

    def test_is_cross_platform_nushell_not_bash(self):
        self.assertTrue(os.path.exists(BUILD_NU), "the builder must be build.nu (Nushell)")
        self.assertRegex(
            self.script.splitlines()[0], r"^#!.*\bnu\b",
            "build.nu must carry a Nushell (`nu`) shebang",
        )
        for raw in self.script.splitlines():
            line = raw.strip()
            if line.startswith("#"):
                continue
            for tool in ("bash", "awk", "sed", "grep"):
                self.assertNotRegex(
                    line, r"(^|[\s|(])%s\b" % tool,
                    "build.nu must not invoke the non-cross-platform tool "
                    "%r: %r" % (tool, raw),
                )

    def test_supports_environment_overrides(self):
        for name in ("REGISTRY", "KIND_CLUSTER", "LOAD", "PUSH"):
            self._env_default(name)

    def test_pins_upstream_image_tag_and_digest(self):
        self.assertEqual(self._const("UPSTREAM_IMAGE"), UPSTREAM_IMAGE)
        self.assertEqual(self._const("UPSTREAM_TAG"), UPSTREAM_TAG)
        digest = self._const("UPSTREAM_DIGEST")
        self.assertEqual(digest, UPSTREAM_DIGEST)
        self.assertRegex(
            digest, r"^sha256:[0-9a-f]{64}$",
            "UPSTREAM_DIGEST must be a full immutable sha256 digest for provenance",
        )

    def test_image_coordinates(self):
        self.assertEqual(self._const("IMAGE"), IMAGE)
        self.assertEqual(self._const("TAG"), TAG)

    def test_verifies_tag_resolves_to_pinned_digest_before_build(self):
        self.assertIn(
            "resolve_digest.py", self.script,
            "build.nu must resolve the pinned tag (scripts/resolve_digest.py) "
            "before building",
        )
        self.assertIn(
            "UPSTREAM_DIGEST", self.script,
            "the resolved digest must be compared against UPSTREAM_DIGEST",
        )
        self.assertRegex(
            self.script, r"exit\s+1",
            "build.nu must abort (exit 1) when the tag no longer resolves to "
            "the pinned digest",
        )

    def test_local_and_harbor_coordinates_distinct_and_aligned(self):
        image = self._const("IMAGE")
        registry = self._env_default("REGISTRY")
        registry_repository = self._const("REGISTRY_REPOSITORY")
        self.assertEqual(image, "digiorg/fluentd")
        self.assertIn("digiorg.local/library", registry)
        self.assertEqual(registry_repository, "fluentd")
        self.assertIn(
            '$"($registry)/($REGISTRY_REPOSITORY):($TAG)"', self.script,
            "Harbor push target must be digiorg.local/library/fluentd:<tag> "
            "without duplicating the local digiorg namespace",
        )
        self.assertNotIn(
            '$"($registry)/($IMAGE):($TAG)"', self.script,
            "Harbor target must not become library/digiorg/fluentd",
        )

    def test_passes_provenance_build_args(self):
        for arg in ("version", "base_digest"):
            self.assertRegex(
                self.script, r'--build-arg\s+\$?"?%s=' % arg,
                "build should pass provenance build arg %s=" % arg,
            )

    def test_no_embedded_credentials(self):
        for pat in (r"password\s*=", r"passwd\s*=", r"token\s*=",
                    r"-p\s+\S+", r"DOCKER_PASSWORD="):
            self.assertNotRegex(
                self.script, re.compile(pat, re.I),
                "build.nu must not embed credentials (matched %r)" % pat,
            )

    def test_kind_load_and_optional_push(self):
        self.assertIn("kind load docker-image", self.script,
                      "build.nu must kind-load the built image for local dev")
        self.assertIn("docker push", self.script,
                      "build.nu must support an optional Harbor push")


class DockerfileContractTest(unittest.TestCase):
    """Digest-pinned base + explicitly version-pinned plugin gems + OCI labels."""

    @classmethod
    def setUpClass(cls):
        cls.text = read_text(DOCKERFILE)

    def _from_lines(self):
        return [ln for ln in self.text.splitlines()
                if ln.strip().upper().startswith("FROM ")]

    def test_base_is_digest_pinned(self):
        self.assertIn(UPSTREAM_DIGEST, self.text,
                      "the pinned base digest must appear in the Dockerfile")
        for ln in self._from_lines():
            self.assertRegex(
                ln, r"@\$\{?UPSTREAM_DIGEST\}?|@%s" % re.escape(UPSTREAM_DIGEST),
                "every FROM must pin the base by @sha256 digest: %r" % ln,
            )

    def test_plugins_are_explicitly_version_pinned(self):
        for plugin in REQUIRED_PINNED_PLUGINS:
            # `gem install <plugin> -v <exact-version>` (or --version) with a real
            # semver — never an unpinned install.
            m = re.search(
                r"gem\s+install\s+%s\s+(?:-v|--version)\s+['\"]?"
                r"(\d+\.\d+\.\d+)" % re.escape(plugin),
                self.text,
            )
            self.assertIsNotNone(
                m, "%s must be installed with an explicit pinned version" % plugin
            )

    def test_emits_oci_provenance_labels(self):
        for label in (
            "org.opencontainers.image.source",
            "org.opencontainers.image.version",
            "org.opencontainers.image.revision",
        ):
            self.assertIn(label, self.text,
                          "Dockerfile must emit OCI label %s" % label)


class DaemonsetWiringTest(unittest.TestCase):
    """The daemonset must select the built image and keep everything else intact."""

    @classmethod
    def setUpClass(cls):
        docs = load_yaml_docs(DAEMONSET)
        cls.ds = next(d for d in docs if d.get("kind") == "DaemonSet")
        cls.container = cls.ds["spec"]["template"]["spec"]["containers"][0]
        cls.env = {e["name"]: e.get("value") for e in cls.container.get("env", [])}

    def test_selects_exact_allowlisted_image(self):
        self.assertEqual(self.container["image"], FULL_IMAGE)

    def test_pull_policy_never(self):
        self.assertEqual(self.container.get("imagePullPolicy"), "Never")

    def test_preserves_opensearch_env(self):
        self.assertEqual(
            self.env.get("FLUENT_ELASTICSEARCH_HOST"),
            "opensearch-cluster-master.platform-db.svc.cluster.local",
        )
        self.assertEqual(self.env.get("FLUENT_ELASTICSEARCH_PORT"), "9200")
        self.assertEqual(self.env.get("FLUENT_ELASTICSEARCH_LOGSTASH_PREFIX"), "digiorg-logs")
        self.assertEqual(self.env.get("FLUENT_ELASTICSEARCH_INDEX_NAME"), "digiorg-logs")
        self.assertEqual(self.env.get("KUBERNETES_URL"), "https://kubernetes.default.svc")

    def test_preserves_prometheus_port(self):
        ports = {p.get("name"): p["containerPort"] for p in self.container["ports"]}
        self.assertEqual(ports.get("prometheus"), 24231)

    def test_preserves_volumes(self):
        mounts = {m["name"]: m["mountPath"] for m in self.container["volumeMounts"]}
        self.assertEqual(mounts.get("varlog"), "/var/log")
        self.assertEqual(mounts.get("varlibdockercontainers"), "/var/lib/docker/containers")
        self.assertEqual(mounts.get("fluentd-config"), "/fluentd/etc/fluent.conf")

    def test_preserves_resources(self):
        res = self.container["resources"]
        self.assertEqual(res["requests"]["cpu"], "100m")
        self.assertEqual(res["requests"]["memory"], "200Mi")
        self.assertEqual(res["limits"]["cpu"], "500m")
        self.assertEqual(res["limits"]["memory"], "512Mi")


if __name__ == "__main__":
    unittest.main(verbosity=2)
