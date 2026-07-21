#!/usr/bin/env python3
"""CNPG's corrected domain model: optional future-app database infra only
(Issue #283).

CNPG is NOT a migration or replacement target for the internal platform
databases. Keycloak, Backstage, Gitea, SonarQube and Harbor permanently use
the legacy PostgreSQL StatefulSet (platform/base/postgresql). This module
locks the corrected `platform/base/cnpg` domain model:

  * the Cluster manifest carries no internal-platform database/user
    initialization and is not coupled to `postgresql-secrets` or any other
    legacy-platform credential — it uses only CNPG-owned, auto-generated
    Secrets;
  * there is no backup PVC, bind hook, or `pg_dump` CronJob targeting the
    internal platform databases;
  * there is no cutover/migration alias Service;
  * `scripts/local-setup.nu` no longer provisions a superuser Secret coupled
    to the legacy `POSTGRES_PASSWORD`.

Pure ``python3`` + PyYAML, no cluster access::

    python3 platform/tests/test_cnpg_future_app_infrastructure.py
"""

import os
import re
import sys
import unittest

try:
    import yaml
except ImportError:  # pragma: no cover - environment guard
    sys.stderr.write("PyYAML is required: pip install pyyaml\n")
    raise

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CNPG_DIR = os.path.join(REPO_ROOT, "platform", "base", "cnpg")
CLUSTER_YAML = os.path.join(CNPG_DIR, "cluster.yaml")
KUSTOMIZATION_YAML = os.path.join(CNPG_DIR, "kustomization.yaml")
INIT_DATABASES_YAML = os.path.join(CNPG_DIR, "init-databases.yaml")
SERVICE_YAML = os.path.join(CNPG_DIR, "service.yaml")
SETUP_SCRIPT = os.path.join(REPO_ROOT, "scripts", "local-setup.nu")
OPERATOR_APP_YAML = os.path.join(REPO_ROOT, "apps", "platform", "cnpg.yaml")
LEGACY_STATEFULSET_YAML = os.path.join(REPO_ROOT, "platform", "base", "postgresql", "statefulset.yaml")

EXACT_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")

# The digest-pinned PG16 image the Cluster must still use.
EXPECTED_IMAGE = (
    "ghcr.io/cloudnative-pg/postgresql:16.9"
    "@sha256:cca50a94e2da46ddcaefb23260c805ed3b466763bd2a25e1617410176d5fd0ab"
)

LEGACY_PLATFORM_DATABASES = ("keycloak", "backstage", "gitea", "sonarqube", "registry")
LEGACY_PLATFORM_USERS = ("keycloak", "backstage", "gitea", "sonarqube", "harbor")
LEGACY_SECRET_NAME = "postgresql-secrets"
LEGACY_SUPERUSER_SECRET_NAME = "postgresql-cnpg-superuser"


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _docs(path):
    with open(path, encoding="utf-8") as fh:
        return [d for d in yaml.safe_load_all(fh) if d]


def _find(docs, kind, name=None):
    for d in docs:
        if d.get("kind") == kind and (name is None or d.get("metadata", {}).get("name") == name):
            return d
    return None


class ClusterManifestTest(unittest.TestCase):
    def setUp(self):
        self.docs = _docs(CLUSTER_YAML)
        self.cluster = _find(self.docs, "Cluster")
        self.assertIsNotNone(self.cluster, "no CNPG Cluster document in cluster.yaml")

    def test_api_version_and_kind(self):
        self.assertEqual(self.cluster["apiVersion"], "postgresql.cnpg.io/v1")
        self.assertEqual(self.cluster["kind"], "Cluster")

    def test_namespace_is_platform_db(self):
        self.assertEqual(self.cluster["metadata"]["namespace"], "platform-db")

    def test_image_is_digest_pinned_and_exact(self):
        self.assertEqual(self.cluster["spec"]["imageName"], EXPECTED_IMAGE)

    def test_no_coupling_to_legacy_superuser_secret(self):
        spec = self.cluster["spec"]
        superuser_secret = spec.get("superuserSecret", {}).get("name", "")
        self.assertNotEqual(
            superuser_secret, LEGACY_SUPERUSER_SECRET_NAME,
            "the Cluster must not reference the legacy-coupled superuser secret",
        )

    def test_no_reference_to_legacy_platform_secret(self):
        text = _read(CLUSTER_YAML)
        self.assertNotIn(LEGACY_SECRET_NAME, text,
                         "the Cluster must not reference postgresql-secrets")

    def test_bootstrap_database_is_not_a_legacy_platform_database(self):
        initdb = self.cluster["spec"].get("bootstrap", {}).get("initdb", {})
        db = initdb.get("database", "")
        self.assertNotIn(db.lower(), [d.lower() for d in LEGACY_PLATFORM_DATABASES])


class NoLegacyPlatformDatabaseInitTest(unittest.TestCase):
    """No file in the CNPG base may create the internal platform's databases/users."""

    def test_init_databases_file_removed(self):
        self.assertFalse(
            os.path.exists(INIT_DATABASES_YAML),
            "init-databases.yaml (legacy db/user init + backup PVC/hook/CronJob) "
            "must be removed — CNPG hosts no internal platform database",
        )

    def test_no_cnpg_base_file_creates_legacy_databases_or_users(self):
        for name in os.listdir(CNPG_DIR):
            if not name.endswith((".yaml", ".yml")):
                continue
            text = _read(os.path.join(CNPG_DIR, name))
            for db in LEGACY_PLATFORM_DATABASES:
                self.assertNotRegex(
                    text, rf"CREATE\s+DATABASE\s+{db}\b",
                    f"{name} must not create legacy-platform database {db}",
                )
            for user in LEGACY_PLATFORM_USERS:
                self.assertNotRegex(
                    text, rf"CREATE\s+(USER|ROLE)\s+{user}\b",
                    f"{name} must not create legacy-platform user/role {user}",
                )


class NoCutoverAliasTest(unittest.TestCase):
    def test_service_alias_removed(self):
        self.assertFalse(
            os.path.exists(SERVICE_YAML),
            "service.yaml (legacy cutover/migration alias) must be removed — "
            "CNPG is not a cutover target for the internal platform database",
        )


class KustomizationTest(unittest.TestCase):
    def test_kustomization_deploys_only_the_cluster(self):
        k = _docs(KUSTOMIZATION_YAML)[0]
        resources = k.get("resources", [])
        self.assertEqual(resources, ["cluster.yaml"])
        self.assertEqual(k.get("namespace"), "platform-db")

    def test_kustomization_comments_do_not_claim_migration(self):
        text = _read(KUSTOMIZATION_YAML).lower()
        self.assertNotIn("migration", text)
        self.assertNotIn("cutover", text)


class SetupScriptNoLongerCouplesSuperuserSecretTest(unittest.TestCase):
    """scripts/local-setup.nu must not provision a superuser Secret that reuses
    the legacy postgres_password (Issue #283: CNPG must use CNPG-owned secrets)."""

    def test_no_dedicated_superuser_secret_provisioning(self):
        text = _read(SETUP_SCRIPT)
        self.assertNotIn(LEGACY_SUPERUSER_SECRET_NAME, text)

    def test_no_kubernetes_io_basic_auth_secret_for_cnpg(self):
        text = _read(SETUP_SCRIPT)
        self.assertNotIn("kubernetes.io/basic-auth", text)


class OperatorChartPinTest(unittest.TestCase):
    """Preserved from the original CNPG migration test: the operator Helm
    chart pin and its reduced local-dev resources are unaffected by the
    domain-model correction."""

    def _operator_source(self):
        app = _find(_docs(OPERATOR_APP_YAML), "Application", "cnpg")
        self.assertIsNotNone(app)
        src = app["spec"]["source"]
        self.assertEqual(src["chart"], "cloudnative-pg")
        return src

    def test_operator_chart_pinned_to_exact_semver_0_29_0(self):
        rev = str(self._operator_source()["targetRevision"])
        self.assertRegex(rev, EXACT_SEMVER_RE, "operator chart must pin an exact SemVer")
        self.assertEqual(rev, "0.29.0")

    def test_reduced_resources_retained(self):
        values = self._operator_source()["helm"]["values"]
        self.assertIn("resources", values)
        self.assertIn("128Mi", values)


class LegacyStatefulSetRetainedTest(unittest.TestCase):
    """The legacy StatefulSet is the PERMANENT internal-platform database
    (Issue #283) — it is not a transitional artifact awaiting removal."""

    def test_legacy_statefulset_still_present(self):
        self.assertTrue(os.path.exists(LEGACY_STATEFULSET_YAML),
                        "the legacy StatefulSet must be retained permanently")


if __name__ == "__main__":
    unittest.main(verbosity=2)
