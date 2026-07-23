#!/usr/bin/env python3
# =============================================================================
# Regression tests for the DigiOrg production-optimized Keycloak image — #275
#
# Architecture under test (Issue #275, Tier 1 — DigiOrg-owned Keycloak image):
#
#   * The stock deployment ran `start-dev` on the raw upstream image. Dev mode
#     re-runs the augmentation/build step on every boot, disables production
#     safeguards and is explicitly unsupported for real use.
#
#   * Tier 1 replaces it with a pinned, multi-stage image built by
#     platform/images/keycloak/build.nu: stage 1 runs `kc.sh build` with the
#     platform's build-time options (db=postgres, health, metrics) against the
#     digest-pinned upstream base, stage 2 copies the optimized server from the
#     SAME digest-pinned base, and the deployment starts it with
#     `start --optimized`. Realm/user data is NEVER baked in — it stays in the
#     mounted realm-import configMap volume.
#
# These tests parse/assert the REAL build definition and the REAL deployment
# manifest (behaviour, not a single grepped string):
#
#   BuildContractTest      build.nu (Nushell, cross-platform) pins the upstream
#                          tag + immutable base digest, verifies the tag still
#                          resolves to that digest before building, bakes the
#                          build-time options for `--optimized`, passes
#                          version/base-digest provenance args, keeps local kind
#                          and Harbor coordinates aligned, exposes REGISTRY/
#                          KIND_CLUSTER/LOAD/PUSH overrides, and embeds no
#                          credentials.
#   DockerfileContractTest a multi-stage build FROM the digest-pinned base runs
#                          `kc.sh build` with db=postgres/health/metrics, the
#                          final stage copies the optimized build from the SAME
#                          pinned base, emits OCI provenance labels, and bakes NO
#                          realm/user JSON into the image.
#   DeploymentWiringTest   keycloak-deployment.yaml selects the exact allow-listed
#                          image with IfNotPresent, starts with `start
#                          --optimized` (not start-dev), preserves every env var/
#                          port/probe/relative-path/hostname setting, and keeps
#                          the realm data in the mounted configMap volume.
#
# Runs with the standard library + PyYAML + the repository-pinned Nushell
# binary (no pytest, cluster, Docker daemon or network):
#     python3 platform/tests/test_keycloak_image.py
# =============================================================================
import os
import re
import subprocess
import unittest

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
IMAGE_DIR = os.path.join(REPO_ROOT, "platform", "images", "keycloak")
BUILD_NU = os.path.join(IMAGE_DIR, "build.nu")
DOCKERFILE = os.path.join(IMAGE_DIR, "Dockerfile")
DEPLOYMENT = os.path.join(
    REPO_ROOT, "platform", "base", "keycloak", "keycloak-deployment.yaml"
)

# The exact coordinates fixed by the issue and the pin-policy allowlist.
UPSTREAM_IMAGE = "quay.io/keycloak/keycloak"
UPSTREAM_TAG = "26.7.0"
UPSTREAM_DIGEST = "sha256:0f198be292568439d700cdbfb893e69a6009bb43a94a06a945b1d3d506c76b13"
IMAGE = "digiorg/keycloak"
TAG = "26.7.0-optimized"
FULL_IMAGE = "%s:%s" % (IMAGE, TAG)


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

    def _validate_persisted_result(self, output, exit_code=0):
        env = os.environ.copy()
        env["KEYCLOAK_TEST_OUTPUT"] = output
        env["KEYCLOAK_TEST_EXIT_CODE"] = str(exit_code)
        source_path = BUILD_NU.replace("\\", "/")
        command = (
            'source "%s"; '
            'validate_persisted_result {'
            'exit_code: ($env.KEYCLOAK_TEST_EXIT_CODE | into int), '
            'stdout: $env.KEYCLOAK_TEST_OUTPUT}'
        ) % source_path
        return subprocess.run(
            ["nu", "-c", command],
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

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
        # A re-tag upstream must not silently change the base: build.nu must
        # resolve the tag and compare it to UPSTREAM_DIGEST before building.
        self.assertIn(
            "resolve_digest.py", self.script,
            "build.nu must resolve the pinned tag (scripts/resolve_digest.py) "
            "before building",
        )
        self.assertIn(
            "UPSTREAM_DIGEST", self.script,
            "the resolved digest must be compared against UPSTREAM_DIGEST",
        )
        # The comparison must be able to abort the build on mismatch.
        self.assertRegex(
            self.script, r"exit\s+1",
            "build.nu must abort (exit 1) when the tag no longer resolves to "
            "the pinned digest",
        )

    def test_local_and_harbor_coordinates_distinct_and_aligned(self):
        image = self._const("IMAGE")
        registry = self._env_default("REGISTRY")
        registry_repository = self._const("REGISTRY_REPOSITORY")
        self.assertEqual(image, "digiorg/keycloak")
        self.assertIn("digiorg.local/library", registry)
        self.assertEqual(registry_repository, "keycloak")
        self.assertIn(
            '$"($registry)/($REGISTRY_REPOSITORY):($TAG)"', self.script,
            "Harbor push target must be digiorg.local/library/keycloak:<tag> "
            "without duplicating the local digiorg namespace",
        )
        self.assertNotIn(
            '$"($registry)/($IMAGE):($TAG)"', self.script,
            "Harbor target must not become library/digiorg/keycloak",
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

    def test_validates_finished_image_persisted_configuration(self):
        validator = re.search(
            r"def validate_persisted_config \[image: string\] \{(?P<body>.*?)\n\}",
            self.script,
            re.S,
        )
        self.assertIsNotNone(
            validator,
            "build.nu must define a post-build persisted-config validator",
        )
        body = validator.group("body")
        self.assertIn("docker run", body)
        self.assertIn("--rm", body)
        self.assertIn("--entrypoint /opt/keycloak/bin/kc.sh", body)
        self.assertIn("show-config", body)
        self.assertIn("| complete", body, "show-config failures must be captured")
        self.assertIn("validate_persisted_result $result", body)

        result_validator = re.search(
            r"def validate_persisted_result \[result: record\] \{(?P<body>.*?)\n\}",
            self.script,
            re.S,
        )
        self.assertIsNotNone(
            result_validator,
            "build.nu must expose the result validation as testable Nushell behavior",
        )
        result_body = result_validator.group("body")
        for name, value in (
            ("db", "postgres"),
            ("health-enabled", "true"),
            ("metrics-enabled", "true"),
            ("http-relative-path", "/keycloak"),
            ("http-management-relative-path", "/"),
        ):
            self.assertRegex(
                result_body,
                r'name:\s*"%s"\s*,?\s*value:\s*"%s"'
                % (re.escape(name), re.escape(value)),
                "validator must require kc.%s = %s" % (name, value),
            )
        self.assertIn("(Persisted)", result_body)
        self.assertIn("error make", result_body)

        validation_call = 'validate_persisted_config $"($IMAGE):($TAG)"'
        self.assertIn(validation_call, self.script)
        self.assertLess(self.script.index("docker build"), self.script.index(validation_call))
        self.assertLess(
            self.script.index(validation_call),
            self.script.index("kind load docker-image"),
            "the finished image must be validated before it is loaded or pushed",
        )
        self.assertLess(
            self.script.index(validation_call),
            self.script.index("docker push"),
            "the finished image must be validated before it is pushed",
        )

    def test_persisted_config_validator_accepts_real_variable_whitespace(self):
        output = """
            kc.health-enabled =  true (Persisted)
        kc.metrics-enabled    = true   (Persisted)
        kc.db =  postgres (Persisted)
        kc.http-relative-path =   /keycloak (Persisted)
        kc.http-management-relative-path = / (Persisted)
        """
        result = self._validate_persisted_result(output)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_persisted_config_validator_rejects_wrong_value(self):
        output = """
        kc.db = postgres (Persisted)
        kc.health-enabled = true (Persisted)
        kc.metrics-enabled = true (Persisted)
        kc.http-relative-path = / (Persisted)
        kc.http-management-relative-path = / (Persisted)
        """
        result = self._validate_persisted_result(output)
        self.assertNotEqual(result.returncode, 0)

    def test_persisted_config_validator_requires_persisted_marker(self):
        output = """
        kc.db = postgres (Persisted)
        kc.health-enabled = true (Persisted)
        kc.metrics-enabled = true (Persisted)
        kc.http-relative-path = /keycloak
        kc.http-management-relative-path = / (Persisted)
        """
        result = self._validate_persisted_result(output)
        self.assertNotEqual(result.returncode, 0)

    def test_persisted_config_validator_rejects_show_config_failure(self):
        result = self._validate_persisted_result("", exit_code=2)
        self.assertNotEqual(result.returncode, 0)


class DockerfileContractTest(unittest.TestCase):
    """Multi-stage, digest-pinned, production-optimized, realm-data-free."""

    @classmethod
    def setUpClass(cls):
        cls.text = read_text(DOCKERFILE)

    def _from_lines(self):
        return [ln for ln in self.text.splitlines()
                if ln.strip().upper().startswith("FROM ")]

    def test_multi_stage_build_with_builder(self):
        froms = self._from_lines()
        self.assertGreaterEqual(len(froms), 2,
                                "Dockerfile must be multi-stage (builder + final)")
        self.assertRegex(
            froms[0], r"(?i)\bAS\s+builder\b",
            "the first stage must be named `builder`",
        )

    def test_both_stages_pin_the_same_base_digest(self):
        # Every FROM must reference the immutable digest (directly or via an ARG
        # that defaults to it) so the base can never float.
        self.assertGreaterEqual(
            self.text.count(UPSTREAM_DIGEST), 1,
            "the pinned base digest must appear in the Dockerfile",
        )
        for ln in self._from_lines():
            self.assertRegex(
                ln, r"@\$\{?UPSTREAM_DIGEST\}?|@%s" % re.escape(UPSTREAM_DIGEST),
                "every FROM must pin the base by @sha256 digest: %r" % ln,
            )

    def test_runs_optimized_kc_build_with_platform_options(self):
        self.assertRegex(
            self.text, r"kc\.sh\s+build",
            "the builder stage must run `kc.sh build` to produce an optimized server",
        )
        # The build-time options that `start --optimized` then relies on.
        self.assertRegex(self.text, r"KC_DB[= ]+postgres",
                          "must fix KC_DB=postgres at build time for --optimized")
        self.assertRegex(self.text, r"KC_HEALTH_ENABLED[= ]+true",
                          "must enable health at build time")
        self.assertRegex(self.text, r"KC_METRICS_ENABLED[= ]+true",
                          "must enable metrics at build time")

    def test_builder_persists_complete_deployment_build_time_contract(self):
        docs = load_yaml_docs(DEPLOYMENT)
        deploy = next(d for d in docs if d.get("kind") == "Deployment")
        container = deploy["spec"]["template"]["spec"]["containers"][0]
        deployment_env = {
            entry["name"]: str(entry["value"])
            for entry in container.get("env", [])
            if "value" in entry
        }
        expected_build_time = {
            "KC_DB": "postgres",
            "KC_HEALTH_ENABLED": "true",
            "KC_METRICS_ENABLED": "true",
            "KC_HTTP_RELATIVE_PATH": "/keycloak",
            "KC_HTTP_MANAGEMENT_RELATIVE_PATH": "/",
        }

        builder_before_build = self.text.split("RUN /opt/keycloak/bin/kc.sh build", 1)[0]
        builder_env = dict(
            re.findall(r"^ENV\s+(KC_[A-Z_]+)[= ]([^\s]+)\s*$", builder_before_build, re.M)
        )

        for name, expected in expected_build_time.items():
            if name in deployment_env:
                self.assertEqual(
                    deployment_env[name], expected,
                    "%s must retain the image's persisted value at runtime" % name,
                )
            self.assertEqual(
                builder_env.get(name),
                expected,
                "%s must be persisted before kc.sh build with the platform value"
                % name,
            )

    def test_final_stage_copies_optimized_build(self):
        self.assertRegex(
            self.text, r"COPY\s+--from=builder\s+/opt/keycloak",
            "the final stage must copy the optimized /opt/keycloak from the builder",
        )

    def test_emits_oci_provenance_labels(self):
        for label in (
            "org.opencontainers.image.source",
            "org.opencontainers.image.version",
            "org.opencontainers.image.revision",
        ):
            self.assertIn(label, self.text,
                          "Dockerfile must emit OCI label %s" % label)

    def test_no_realm_or_user_json_baked_in(self):
        # Realm/user data must stay in the mounted configMap, never in the image.
        # Inspect the actual COPY/ADD *instructions* (comments may reference the
        # runtime mount path to explain why it is deliberately absent here).
        copy_adds = [
            ln for ln in self.text.splitlines()
            if ln.strip().upper().startswith(("COPY ", "ADD "))
        ]
        for ln in copy_adds:
            self.assertNotRegex(
                ln, r"(?i)realm.*\.json",
                "realm JSON must NOT be baked into the image: %r" % ln,
            )
            self.assertNotRegex(
                ln, r"/opt/keycloak/data/import",
                "the realm-import dir must stay a runtime mount, not a COPY "
                "target: %r" % ln,
            )


class DeploymentWiringTest(unittest.TestCase):
    """The deployment must select the built image and start --optimized."""

    @classmethod
    def setUpClass(cls):
        docs = load_yaml_docs(DEPLOYMENT)
        cls.deploy = next(d for d in docs if d.get("kind") == "Deployment")
        cls.container = cls.deploy["spec"]["template"]["spec"]["containers"][0]
        cls.env = {e["name"]: e for e in cls.container.get("env", [])}

    def test_selects_exact_allowlisted_image(self):
        self.assertEqual(self.container["image"], FULL_IMAGE)

    def test_pull_policy_never(self):
        self.assertEqual(self.container.get("imagePullPolicy"), "Never")

    def test_starts_optimized_not_dev(self):
        args = self.container.get("args", [])
        self.assertIn("start", args, "must run production `start`")
        self.assertIn("--optimized", args, "must pass --optimized")
        self.assertNotIn("start-dev", args, "dev mode must be gone")

    def test_realm_data_stays_a_mounted_configmap(self):
        mounts = self.container.get("volumeMounts", [])
        realm_mount = next(
            (m for m in mounts if m.get("mountPath") == "/opt/keycloak/data/import"),
            None,
        )
        self.assertIsNotNone(realm_mount, "realm-import volume mount must be preserved")
        vols = {v["name"]: v for v in self.deploy["spec"]["template"]["spec"]["volumes"]}
        vol = vols.get(realm_mount["name"])
        self.assertIsNotNone(vol, "the realm mount must reference a declared volume")
        self.assertIn("configMap", vol, "realm data must come from a configMap volume")

    def test_preserves_relative_path_and_http_and_db(self):
        self.assertEqual(self.env["KC_HTTP_RELATIVE_PATH"]["value"], "/keycloak")
        self.assertEqual(self.env["KC_HTTP_ENABLED"]["value"], "true")
        self.assertEqual(self.env["KC_DB"]["value"], "postgres")
        self.assertEqual(self.env["KC_HEALTH_ENABLED"]["value"], "true")

    def test_preserves_hostname_config(self):
        self.assertEqual(
            self.env["KC_HOSTNAME"]["value"], "https://digiorg.local/keycloak"
        )
        self.assertEqual(
            self.env["KC_HOSTNAME_ADMIN"]["value"], "https://digiorg.local/keycloak"
        )

    def test_preserves_ports(self):
        ports = {p.get("name"): p["containerPort"] for p in self.container["ports"]}
        self.assertEqual(ports.get("http"), 8080)
        self.assertEqual(ports.get("health"), 9000)

    def test_preserves_probes(self):
        for probe in ("startupProbe", "livenessProbe", "readinessProbe"):
            self.assertIn(probe, self.container, "%s must be preserved" % probe)
            self.assertEqual(self.container[probe]["httpGet"]["port"], 9000)


if __name__ == "__main__":
    unittest.main(verbosity=2)
