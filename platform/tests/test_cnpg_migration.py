#!/usr/bin/env python3
"""Tests for the CloudNativePG (CNPG) migration of the shared PostgreSQL (Issue #275).

The legacy raw ``StatefulSet`` under ``platform/base/postgresql`` hosts SIX
databases/users (keycloak, backstage, gitea, sonarqube and harbor -> db
``registry``/user ``harbor``) created by an ``init.sh`` ConfigMap and backed by
the ``postgresql-secrets`` Secret. This module locks the Tier-3 migration to a
CNPG ``Cluster`` that must reproduce ALL of them with the SAME passwords sourced
from the SAME secret, keep the ``postgresql`` service name reachable for every
consumer, and pin every reference immutably.

Pure ``python3`` + PyYAML — no pytest, cluster or network access::

    python3 platform/tests/test_cnpg_migration.py
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

CLUSTER_YAML = os.path.join(REPO_ROOT, "platform", "base", "cnpg", "cluster.yaml")
INIT_YAML = os.path.join(REPO_ROOT, "platform", "base", "cnpg", "init-databases.yaml")
SERVICE_YAML = os.path.join(REPO_ROOT, "platform", "base", "cnpg", "service.yaml")
KUSTOMIZATION_YAML = os.path.join(REPO_ROOT, "platform", "base", "cnpg", "kustomization.yaml")
OPERATOR_APP_YAML = os.path.join(REPO_ROOT, "apps", "platform", "cnpg.yaml")
CLUSTER_APP_YAML = os.path.join(REPO_ROOT, "apps", "platform", "cnpg-cluster.yaml")

# The digest-pinned PG16 image mandated by the issue (matches legacy postgres:16
# major, so no on-disk data-format migration is required).
EXPECTED_IMAGE = (
    "ghcr.io/cloudnative-pg/postgresql:16.9"
    "@sha256:cca50a94e2da46ddcaefb23260c805ed3b466763bd2a25e1617410176d5fd0ab"
)

# Every database + owning role the legacy StatefulSet creates. Note harbor owns
# database "registry".
EXPECTED_DATABASES = ("keycloak", "backstage", "gitea", "sonarqube", "registry")
EXPECTED_USERS = ("keycloak", "backstage", "gitea", "sonarqube", "harbor")

# The exact secret + keys the legacy init.sh consumes; the CNPG init Job must
# reuse them so no consumer password changes.
SECRET_NAME = "postgresql-secrets"

# CNPG requires the superuserSecret to be a `kubernetes.io/basic-auth` Secret
# with username=postgres + password (a Secret's type is immutable, so the
# existing Opaque `postgresql-secrets` cannot be reused for this). A dedicated
# basic-auth secret is provisioned by the setup script with the SAME password as
# postgresql-secrets.POSTGRES_PASSWORD, so the init Job (which authenticates with
# POSTGRES_PASSWORD) still connects. See docs/guides/postgres-cnpg-migration.md.
SUPERUSER_SECRET_NAME = "postgresql-cnpg-superuser"

SETUP_SCRIPT = os.path.join(REPO_ROOT, "scripts", "local-setup.nu")
PASSWORD_KEYS = (
    "POSTGRES_PASSWORD",
    "KEYCLOAK_DB_PASSWORD",
    "BACKSTAGE_DB_PASSWORD",
    "GITEA_DB_PASSWORD",
    "SONARQUBE_DB_PASSWORD",
    "HARBOR_DB_PASSWORD",
)

DIGEST_RE = re.compile(r"@sha256:[0-9a-f]{64}$")
EXACT_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")


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


class ClusterSpecTest(unittest.TestCase):
    def setUp(self):
        self.docs = _docs(CLUSTER_YAML)
        self.cluster = _find(self.docs, "Cluster")
        self.assertIsNotNone(self.cluster, "no CNPG Cluster document in cluster.yaml")

    def test_api_version_and_kind(self):
        self.assertEqual(self.cluster["apiVersion"], "postgresql.cnpg.io/v1")
        self.assertEqual(self.cluster["kind"], "Cluster")

    def test_namespace_is_platform_db(self):
        self.assertEqual(self.cluster["metadata"]["namespace"], "platform-db")

    def test_instances_set(self):
        self.assertEqual(self.cluster["spec"]["instances"], 1)

    def test_image_is_digest_pinned_and_exact(self):
        image = self.cluster["spec"]["imageName"]
        self.assertRegex(image, DIGEST_RE, "imageName must be pinned to an @sha256 digest")
        self.assertEqual(image, EXPECTED_IMAGE)

    def test_storage_size_matches_legacy(self):
        self.assertEqual(self.cluster["spec"]["storage"]["size"], "5Gi")

    def test_resources_are_modest(self):
        res = self.cluster["spec"]["resources"]
        self.assertEqual(res["requests"]["memory"], "256Mi")
        self.assertEqual(res["limits"]["memory"], "512Mi")

    def test_superuser_secret_is_dedicated_basic_auth(self):
        self.assertTrue(self.cluster["spec"].get("enableSuperuserAccess"),
                        "superuser access must be enabled for the init/migration path")
        # CNPG requires a kubernetes.io/basic-auth secret (username=postgres +
        # password). The existing Opaque `postgresql-secrets` cannot be reused
        # (secret type is immutable and it lacks username/password keys), so the
        # cluster must point at the dedicated basic-auth secret.
        self.assertEqual(
            self.cluster["spec"]["superuserSecret"]["name"], SUPERUSER_SECRET_NAME,
            "superuserSecret must be the dedicated basic-auth secret, not the "
            "Opaque postgresql-secrets",
        )
        self.assertNotEqual(
            self.cluster["spec"]["superuserSecret"]["name"], SECRET_NAME,
            "the Opaque postgresql-secrets is not a valid CNPG basic-auth secret",
        )


class SuperuserSecretProvisioningTest(unittest.TestCase):
    """The setup script must create the CNPG superuser secret as basic-auth,
    with username=postgres and the SAME password as postgresql-secrets so the
    init Job authenticates."""

    @classmethod
    def setUpClass(cls):
        cls.setup = _read(SETUP_SCRIPT)

    def test_creates_basic_auth_superuser_secret(self):
        self.assertIn(SUPERUSER_SECRET_NAME, self.setup,
                      "setup script must provision the CNPG superuser secret")
        # kubectl create secret ... --type=kubernetes.io/basic-auth (or an
        # equivalent typed secret) named postgresql-cnpg-superuser.
        m = re.search(
            r"secret[\s\S]{0,400}?" + re.escape(SUPERUSER_SECRET_NAME) + r"[\s\S]{0,400}",
            self.setup,
        )
        self.assertIsNotNone(m)
        block = m.group(0)
        self.assertIn("kubernetes.io/basic-auth", block,
                      "the superuser secret must be type kubernetes.io/basic-auth")
        self.assertRegex(block, r"username=postgres",
                         "CNPG requires the superuser username to be 'postgres'")

    def test_superuser_password_matches_postgres_password(self):
        # The init Job connects as postgres using postgresql-secrets.POSTGRES_PASSWORD,
        # so the basic-auth password must be the same value.
        m = re.search(
            re.escape(SUPERUSER_SECRET_NAME) + r"[\s\S]{0,400}?password=\(([^)]+)\)",
            self.setup,
        )
        self.assertIsNotNone(m, "superuser secret must set password=(...)")
        self.assertIn("postgres_password", m.group(1),
                      "superuser password must reuse the postgres_password variable")


class BootstrapCreatesAllDatabasesTest(unittest.TestCase):
    """All six db/users must be (re)created, sourcing passwords from the secret."""

    def setUp(self):
        # The init material may live in the Cluster (postInitApplicationSQL) or a
        # companion ConfigMap+Job; accept whichever carries the SQL, but the env
        # wiring to the secret must be present somewhere in the CNPG base.
        self.blob = _read(CLUSTER_YAML)
        if os.path.exists(INIT_YAML):
            self.blob += "\n" + _read(INIT_YAML)

    def test_every_database_is_created(self):
        for db in EXPECTED_DATABASES:
            self.assertRegex(
                self.blob,
                re.compile(rf"CREATE\s+DATABASE\s+{db}\b", re.IGNORECASE),
                f"missing CREATE DATABASE for {db}",
            )

    def test_every_user_is_created(self):
        for user in EXPECTED_USERS:
            self.assertRegex(
                self.blob,
                re.compile(rf"CREATE\s+(USER|ROLE)\s+{user}\b", re.IGNORECASE),
                f"missing CREATE USER/ROLE for {user}",
            )

    def test_harbor_owns_registry_database(self):
        self.assertRegex(
            self.blob,
            re.compile(r"CREATE\s+DATABASE\s+registry\s+OWNER\s+harbor", re.IGNORECASE),
        )

    def test_passwords_reference_secret_keys(self):
        # Each per-service password key from postgresql-secrets must be wired in
        # (as an env var sourced from the secret) so plaintext never appears.
        for key in PASSWORD_KEYS:
            self.assertIn(key, self.blob, f"password key {key} is not referenced")

    def test_passwords_are_psql_bound_not_shell_interpolated_into_sql(self):
        # Environment overrides may contain quotes or SQL metacharacters. Bind
        # them as psql variables (:'name') rather than expanding ${...} inside
        # SQL string literals.
        bindings = {
            "KEYCLOAK_DB_PASSWORD": "keycloak_password",
            "BACKSTAGE_DB_PASSWORD": "backstage_password",
            "GITEA_DB_PASSWORD": "gitea_password",
            "SONARQUBE_DB_PASSWORD": "sonarqube_password",
            "HARBOR_DB_PASSWORD": "harbor_password",
        }
        for env_name, variable in bindings.items():
            self.assertIn(f'-v {variable}="${env_name}"', self.blob)
            self.assertIn(f":'{variable}'", self.blob)
            self.assertNotIn(f"PASSWORD '${{{env_name}}}'", self.blob)

    def test_secret_name_referenced(self):
        self.assertIn(SECRET_NAME, self.blob)

    def test_init_env_sourced_from_secret(self):
        """The init workload must pull the password keys from the Secret, not inline."""
        docs = _docs(INIT_YAML) if os.path.exists(INIT_YAML) else _docs(CLUSTER_YAML)
        found = 0
        text = yaml.safe_dump_all(docs)
        for key in PASSWORD_KEYS:
            # secretKeyRef.key: <KEY> pairing
            if re.search(rf"key:\s*{key}\b", text):
                found += 1
        self.assertEqual(found, len(PASSWORD_KEYS),
                         "all password keys must be sourced via secretKeyRef")


class ConsumerServiceTest(unittest.TestCase):
    """Consumers connect to postgresql.platform-db.svc.cluster.local:5432."""

    def test_postgresql_service_exists_in_platform_db(self):
        docs = _docs(SERVICE_YAML)
        svc = _find(docs, "Service", "postgresql")
        self.assertIsNotNone(svc, "a Service named 'postgresql' must exist for consumers")
        self.assertEqual(svc["metadata"]["namespace"], "platform-db")

    def test_service_targets_the_cnpg_rw_service(self):
        svc = _find(_docs(SERVICE_YAML), "Service", "postgresql")
        spec = svc["spec"]
        # ExternalName alias pointing at the CNPG -rw service, or a selector-based
        # service onto the CNPG pods — either way it must reference the rw target.
        target = spec.get("externalName", "")
        self.assertIn("-rw", target, "alias must point at the CNPG <cluster>-rw service")
        self.assertIn("platform-db", target)

    def test_port_5432_reachable(self):
        svc = _find(_docs(SERVICE_YAML), "Service", "postgresql")
        ports = svc["spec"].get("ports", [])
        self.assertTrue(any(p.get("port") == 5432 for p in ports),
                        "service must expose 5432")


class OperatorChartPinTest(unittest.TestCase):
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


class KustomizationTest(unittest.TestCase):
    def test_kustomization_deploys_cluster_and_init_not_alias(self):
        k = _docs(KUSTOMIZATION_YAML)[0]
        resources = k.get("resources", [])
        self.assertIn("cluster.yaml", resources)
        self.assertIn("init-databases.yaml", resources)
        self.assertEqual(k.get("namespace"), "platform-db")
        # COEXISTENCE SAFETY: the ExternalName alias `service.yaml` must NOT be
        # synced while the legacy postgresql Application is still active. Both the
        # legacy base and service.yaml define a Service named `postgresql` in
        # platform-db; syncing both would make two ArgoCD Applications fight over
        # same object (selfHeal ping-pong). The alias is enabled only during the
        # observed, ordered handoff documented in the migration runbook.
        self.assertNotIn(
            "service.yaml", resources,
            "the postgresql alias Service must not be synced during coexistence "
            "with the legacy postgresql Application (ArgoCD ownership conflict)",
        )


class CoexistenceSafetyTest(unittest.TestCase):
    """The CNPG stack must be deployable ALONGSIDE the legacy postgresql
    Application without two ArgoCD apps claiming the same object."""

    def test_no_synced_cnpg_resource_named_postgresql(self):
        # Walk everything the kustomization actually deploys and prove none of it
        # is a Service named `postgresql` (which the legacy app owns).
        k = _docs(KUSTOMIZATION_YAML)[0]
        for rel in k.get("resources", []):
            path = os.path.join(REPO_ROOT, "platform", "base", "cnpg", rel)
            for doc in _docs(path):
                same = (doc.get("kind") == "Service"
                        and doc.get("metadata", {}).get("name") == "postgresql")
                self.assertFalse(
                    same,
                    f"{rel} is synced and defines Service/postgresql — this "
                    "collides with the legacy postgresql Application",
                )

    def test_alias_service_exists_as_cutover_artifact(self):
        # The alias file must still exist (committed cutover artifact) and be a
        # correct ExternalName onto the CNPG -rw service, ready to be enabled.
        self.assertTrue(os.path.exists(SERVICE_YAML),
                        "the alias service.yaml cutover artifact must exist")
        svc = _find(_docs(SERVICE_YAML), "Service", "postgresql")
        self.assertEqual(svc["spec"]["type"], "ExternalName")

    def test_cutover_documented(self):
        guide = os.path.join(REPO_ROOT, "docs", "guides", "postgres-cnpg-migration.md")
        self.assertTrue(os.path.exists(guide), "migration runbook must exist")
        text = _read(guide).lower()
        self.assertIn("cutover", text)
        self.assertIn("service.yaml", text)
        self.assertIn("rollback", text)
        self.assertIn("mandatory final full dump", text)
        self.assertIn("wait --for=delete service/postgresql", text)
        self.assertNotIn("final delta dump", text)
        self.assertNotIn("single atomic git commit", text)
        self.assertNotIn("--no-owner", text)
        self.assertIn("pg_get_userbyid", text)
        self.assertIn("backup_dir=./pgbackup-final", text)
        self.assertIn("rpo", text)

    def test_logical_backups_are_private_verified_atomic_and_retained(self):
        cron = _find(_docs(INIT_YAML), "CronJob")
        if cron is None:
            self.fail("CNPG logical-backup CronJob is missing")
        script = cron["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"][0]["command"][-1]
        self.assertIn("umask 077", script)
        self.assertIn("pg_restore --list", script)
        self.assertIn("COMPLETE", script)
        self.assertIn("mv \"$tmp\" \"$dest\"", script)
        self.assertIn("-mtime +7", script)


class ClusterAppTest(unittest.TestCase):
    """A dedicated Argo CD Application deploys the CNPG base after the operator."""

    def test_cluster_app_points_at_cnpg_base(self):
        app = _find(_docs(CLUSTER_APP_YAML), "Application")
        self.assertIsNotNone(app)
        src = app["spec"]["source"]
        self.assertEqual(src["path"], "platform/base/cnpg")
        self.assertEqual(app["spec"]["destination"]["namespace"], "platform-db")

    def test_cluster_app_is_in_local_bootstrap_health_wait(self):
        setup = _read(SETUP_SCRIPT)
        self.assertIn('"cnpg-cluster"', setup)

    def test_cluster_app_syncs_after_operator(self):
        app = _find(_docs(CLUSTER_APP_YAML), "Application")
        wave = int(app["metadata"]["annotations"]["argocd.argoproj.io/sync-wave"])
        self.assertGreaterEqual(wave, 1, "cluster must sync after the operator (wave 0)")


class LegacyStatefulSetRetainedTest(unittest.TestCase):
    """The legacy StatefulSet MUST remain until data is verified in a real cluster."""

    def test_legacy_statefulset_still_present(self):
        legacy = os.path.join(REPO_ROOT, "platform", "base", "postgresql", "statefulset.yaml")
        self.assertTrue(os.path.exists(legacy), "legacy StatefulSet must be retained")


if __name__ == "__main__":
    unittest.main(verbosity=2)
