#!/usr/bin/env python3
"""Core data-layer functional readiness gates (Issue #283).

Legacy PostgreSQL and OpenSearch are the platform's core data layer and must
become FUNCTIONALLY ready — not merely "Healthy" per Argo CD's generic
StatefulSet rollout check — before their consumers (Keycloak, Backstage,
Gitea, SonarQube, Harbor for PostgreSQL; Jaeger, Fluentd for OpenSearch) are
allowed to start. This module locks:

  * PostgreSQL: ``pg_isready`` succeeds AND every required internal database
    (keycloak, backstage, gitea, sonarqube, registry) already exists.
  * OpenSearch: a local (in-cluster) ``_cluster/health`` request answers with
    an acceptable status (green or yellow).

Both checks run entirely inside the cluster via ``kubectl exec`` and never
print or inspect Secret values — only enum-like status text (ready/not-ready,
database names, a JSON ``status`` field) is ever read.

Pure-Nushell behaviour checks of the parsing predicates plus fake-kubectl
runtime checks of the bounded wait loops::

    python3 platform/tests/test_core_data_layer_readiness.py
"""

import os
import re
import subprocess
import sys
import tempfile
import unittest

try:
    import yaml
except ImportError:  # pragma: no cover - environment guard
    sys.stderr.write("PyYAML is required: pip install pyyaml\n")
    raise

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SETUP = os.path.join(REPO_ROOT, "scripts", "local-setup.nu")


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _func_body(text, name):
    start = text.index(f"def {name} ")
    end = text.index("\ndef ", start + 10)
    return text[start:end]


def _run_nu(expr: str) -> str:
    result = subprocess.run(
        ["nu", "-c", f"source {SETUP}; {expr}"],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        raise AssertionError(f"nu failed: {result.stderr}")
    return result.stdout.strip()


class PostgresRequiredDatabasesPredicateTest(unittest.TestCase):
    """postgresql_has_required_databases checks every internal-platform db is present."""

    def test_all_present_is_true(self):
        datnames = "postgres\ntemplate0\ntemplate1\nkeycloak\nbackstage\ngitea\nsonarqube\nregistry\n"
        self.assertEqual(
            _run_nu(f"postgresql_has_required_databases \"{datnames}\""), "true"
        )

    def test_missing_one_is_false(self):
        datnames = "postgres\nkeycloak\nbackstage\ngitea\nsonarqube\n"  # no registry
        self.assertEqual(
            _run_nu(f"postgresql_has_required_databases \"{datnames}\""), "false"
        )

    def test_empty_is_false(self):
        self.assertEqual(_run_nu('postgresql_has_required_databases ""'), "false")

    def test_required_databases_list_matches_legacy_init_script(self):
        # keycloak, backstage, gitea, sonarqube and harbor's db "registry" — the
        # same five databases platform/base/postgresql/statefulset.yaml creates.
        out = _run_nu("postgresql_required_databases | to json --raw")
        for db in ("keycloak", "backstage", "gitea", "sonarqube", "registry"):
            self.assertIn(db, out)


class OpensearchClusterHealthPredicateTest(unittest.TestCase):
    """opensearch_cluster_health_acceptable accepts green/yellow, rejects red/garbage."""

    def _call(self, payload):
        escaped = payload.replace("\\", "\\\\").replace('"', '\\"')
        return _run_nu(f'opensearch_cluster_health_acceptable "{escaped}"')

    def test_green_is_true(self):
        self.assertEqual(self._call('{"status":"green"}'), "true")

    def test_yellow_is_true(self):
        self.assertEqual(self._call('{"status":"yellow"}'), "true")

    def test_red_is_false(self):
        self.assertEqual(self._call('{"status":"red"}'), "false")

    def test_missing_status_is_false(self):
        self.assertEqual(self._call('{}'), "false")

    def test_garbage_is_false(self):
        self.assertEqual(self._call('not-json'), "false")


class WaitForPostgresqlReadyStructureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = _read(SETUP)
        cls.wait = _func_body(cls.text, "wait_for_postgresql_ready")
        cls.deploy = _func_body(cls.text, "deploy_root_app")
        cls.core_data = _func_body(cls.text, "deploy_core_data_layer")

    def test_checks_pg_isready_and_required_databases(self):
        self.assertIn("pg_isready", self.wait)
        self.assertIn("postgresql_has_required_databases", self.wait)

    def test_fails_closed_with_bounded_redacted_diagnostic(self):
        self.assertIn("error make", self.wait)
        self.assertIn("redact_sync_diagnostic", self.wait)

    def test_never_prints_password_env_vars(self):
        for key in ("POSTGRES_PASSWORD", "PGPASSWORD"):
            self.assertNotIn(key, self.wait)

    def test_runs_inside_core_data_layer_before_gated_sync(self):
        # P1 correction: waiting only happens AFTER deploy_root_app already
        # applied root-app.yaml is insufficient — most child Applications are
        # automated, so Argo begins reconciling Keycloak/Jaeger concurrently
        # the instant root-app creates them. The readiness check must instead
        # run as part of deploy_core_data_layer, which is called BEFORE
        # root-app is ever applied (see CoreDataLayerPrecedesRootAppTest).
        self.assertIn("wait_for_postgresql_ready", self.core_data)
        self.assertNotIn("wait_for_postgresql_ready", self.deploy,
                         "the readiness wait must live in deploy_core_data_layer, "
                         "not be re-invoked directly from deploy_root_app")
        gated_pos = self.deploy.index("sync_gated_apps_for_local_dev")
        core_data_pos = self.deploy.index("deploy_core_data_layer")
        self.assertLess(core_data_pos, gated_pos,
                        "the core data layer must be deployed before gated Applications sync")
        self.assertNotIn("promote_cnpg_cluster", self.deploy,
                         "CNPG must no longer be promoted inside deploy_root_app (Issue #283)")


class WaitForOpensearchReadyStructureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = _read(SETUP)
        cls.wait = _func_body(cls.text, "wait_for_opensearch_ready")
        cls.deploy = _func_body(cls.text, "deploy_root_app")
        cls.core_data = _func_body(cls.text, "deploy_core_data_layer")

    def test_checks_cluster_health_endpoint(self):
        self.assertIn("_cluster/health", self.wait)
        self.assertIn("opensearch_cluster_health_acceptable", self.wait)

    def test_fails_closed_with_bounded_redacted_diagnostic(self):
        self.assertIn("error make", self.wait)
        self.assertIn("redact_sync_diagnostic", self.wait)

    def test_never_prints_admin_password(self):
        self.assertNotIn("OPENSEARCH_ADMIN_PASSWORD", self.wait)

    def test_runs_inside_core_data_layer_before_gated_sync(self):
        self.assertIn("wait_for_opensearch_ready", self.core_data)
        self.assertNotIn("wait_for_opensearch_ready", self.deploy,
                         "the readiness wait must live in deploy_core_data_layer, "
                         "not be re-invoked directly from deploy_root_app")
        gated_pos = self.deploy.index("sync_gated_apps_for_local_dev")
        core_data_pos = self.deploy.index("deploy_core_data_layer")
        self.assertLess(core_data_pos, gated_pos)

    def test_runs_after_postgresql_readiness(self):
        pg_pos = self.core_data.index("wait_for_postgresql_ready")
        os_pos = self.core_data.index("wait_for_opensearch_ready")
        self.assertLess(pg_pos, os_pos)


class CoreDataLayerPrecedesRootAppTest(unittest.TestCase):
    """P1 correction: root-app must not be applied until the core data layer
    (legacy PostgreSQL + OpenSearch) has been explicitly applied AND proven
    functionally ready — otherwise automated consumer Applications (Keycloak,
    Jaeger, ...), created the instant root-app exists, can start racing them."""

    @classmethod
    def setUpClass(cls):
        cls.text = _read(SETUP)
        cls.deploy = _func_body(cls.text, "deploy_root_app")
        cls.core_data = _func_body(cls.text, "deploy_core_data_layer")

    def test_core_data_layer_applies_postgresql_and_opensearch_directly(self):
        # Applied as standalone Application manifests (not via root-app's
        # app-of-apps fan-out), so Argo can begin reconciling ONLY these two
        # before any consumer Application CR exists at all.
        self.assertIn("apps/platform/postgresql.yaml", self.core_data)
        self.assertIn("apps/platform/opensearch.yaml", self.core_data)

    def test_core_data_layer_applies_before_waiting(self):
        pg_apply = self.core_data.index("apps/platform/postgresql.yaml")
        os_apply = self.core_data.index("apps/platform/opensearch.yaml")
        pg_wait = self.core_data.index("wait_for_postgresql_ready")
        os_wait = self.core_data.index("wait_for_opensearch_ready")
        self.assertLess(pg_apply, pg_wait)
        self.assertLess(os_apply, os_wait)

    def test_core_data_layer_apply_failures_fail_closed(self):
        self.assertIn("error make", self.core_data)

    def test_deploy_root_app_calls_core_data_layer_before_applying_root_app(self):
        core_data_pos = self.deploy.index("deploy_core_data_layer")
        root_app_pos = self.deploy.index(
            "kubectl apply -f platform/base/argocd/applications/root-app.yaml"
        )
        self.assertLess(
            core_data_pos, root_app_pos,
            "the core data layer must be applied and proven ready BEFORE "
            "root-app is applied, otherwise automated consumer Applications "
            "race it the instant root-app creates them",
        )

    def test_deploy_root_app_calls_core_data_layer_exactly_once(self):
        # Count actual call sites (a line consisting of just the bare call),
        # not incidental mentions in comments/docstrings.
        calls = [
            line for line in self.deploy.splitlines()
            if line.strip() == "deploy_core_data_layer"
        ]
        self.assertEqual(len(calls), 1)


class CoreDataLayerRuntimeOrderingTest(unittest.TestCase):
    """Behavioral proof: deploy_core_data_layer applies PostgreSQL/OpenSearch
    and waits for BOTH to be functionally ready — and never touches root-app."""

    FAKE_KUBECTL = r'''#!/bin/sh
args="$*"
printf '%s\n' "$args" >> "$FAKE_LOG"
case "$args" in
  *"apply -f platform/base/argocd/applications/root-app.yaml"*)
    echo "FATAL: root-app applied from within deploy_core_data_layer" >&2
    exit 1
    ;;
  *"apply -f apps/platform/postgresql.yaml"*)
    exit 0
    ;;
  *"apply -f apps/platform/opensearch.yaml"*)
    exit 0
    ;;
  *"pg_isready"*)
    exit 0
    ;;
  *"SELECT datname FROM pg_database"*)
    printf 'postgres\nkeycloak\nbackstage\ngitea\nsonarqube\nregistry\n'
    exit 0
    ;;
  *"_cluster/health"*)
    printf '{"status":"green"}\n'
    exit 0
    ;;
  *)
    printf 'unexpected kubectl call: %s\n' "$args" >&2
    exit 2
    ;;
esac
'''

    def test_applies_both_applications_then_waits_for_both_then_never_touches_root_app(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        kubectl = os.path.join(tmp.name, "kubectl")
        with open(kubectl, "w", encoding="utf-8") as fh:
            fh.write(self.FAKE_KUBECTL)
        os.chmod(kubectl, 0o755)
        log = os.path.join(tmp.name, "calls.log")
        env = os.environ.copy()
        env["PATH"] = tmp.name + os.pathsep + env.get("PATH", "")
        env["FAKE_LOG"] = log
        result = subprocess.run(
            ["nu", "-c", f"source {SETUP}; deploy_core_data_layer"],
            capture_output=True, text=True, timeout=30, env=env,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        with open(log, encoding="utf-8") as fh:
            calls = fh.read().splitlines()
        pg_apply = next(i for i, c in enumerate(calls) if "apply -f apps/platform/postgresql.yaml" in c)
        os_apply = next(i for i, c in enumerate(calls) if "apply -f apps/platform/opensearch.yaml" in c)
        pg_check = next(i for i, c in enumerate(calls) if "pg_isready" in c)
        os_check = next(i for i, c in enumerate(calls) if "_cluster/health" in c)
        self.assertLess(pg_apply, pg_check, "PostgreSQL must be applied before its readiness is checked")
        self.assertLess(os_apply, os_check, "OpenSearch must be applied before its readiness is checked")
        self.assertFalse(
            any("root-app.yaml" in c for c in calls),
            "deploy_core_data_layer must never apply root-app.yaml itself",
        )


class WaitForPostgresqlReadyRuntimeTest(unittest.TestCase):
    """Exercise the bounded loop through the real Nushell function with a fake kubectl."""

    FAKE_KUBECTL = r'''#!/bin/sh
args="$*"
case "$args" in
  *"pg_isready"*)
    [ "$FAKE_SCENARIO" = "never_ready" ] && exit 1
    [ "$FAKE_SCENARIO" = "not_ready_once" ] && [ ! -f "$FAKE_MARKER" ] && { touch "$FAKE_MARKER"; exit 1; }
    exit 0
    ;;
  *"SELECT datname FROM pg_database"*)
    [ "$FAKE_SCENARIO" = "missing_db" ] && { printf 'postgres\nkeycloak\n'; exit 0; }
    printf 'postgres\nkeycloak\nbackstage\ngitea\nsonarqube\nregistry\n'
    exit 0
    ;;
  *)
    printf 'unexpected kubectl call: %s\n' "$args" >&2
    exit 2
    ;;
esac
'''

    def _run(self, scenario, timeout=12):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        kubectl = os.path.join(tmp.name, "kubectl")
        with open(kubectl, "w", encoding="utf-8") as fh:
            fh.write(self.FAKE_KUBECTL)
        os.chmod(kubectl, 0o755)
        env = os.environ.copy()
        env["PATH"] = tmp.name + os.pathsep + env.get("PATH", "")
        env["FAKE_SCENARIO"] = scenario
        env["FAKE_MARKER"] = os.path.join(tmp.name, "marker")
        return subprocess.run(
            ["nu", "-c", f"source {SETUP}; wait_for_postgresql_ready 0sec"],
            capture_output=True, text=True, timeout=timeout, env=env,
        )

    def test_ready_with_all_databases_succeeds(self):
        result = self._run("all_ready")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_not_yet_accepting_connections_then_ready_succeeds(self):
        result = self._run("not_ready_once")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_missing_database_fails_closed(self):
        result = self._run("missing_db", timeout=60)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("PostgreSQL", result.stderr)


class WaitForOpensearchReadyRuntimeTest(unittest.TestCase):
    FAKE_KUBECTL = r'''#!/bin/sh
args="$*"
case "$args" in
  *"_cluster/health"*)
    case "$FAKE_SCENARIO" in
      red) printf '{"status":"red"}\n'; exit 0 ;;
      unreachable) exit 1 ;;
      *) printf '{"status":"green"}\n'; exit 0 ;;
    esac
    ;;
  *)
    printf 'unexpected kubectl call: %s\n' "$args" >&2
    exit 2
    ;;
esac
'''

    def _run(self, scenario, timeout=12):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        kubectl = os.path.join(tmp.name, "kubectl")
        with open(kubectl, "w", encoding="utf-8") as fh:
            fh.write(self.FAKE_KUBECTL)
        os.chmod(kubectl, 0o755)
        env = os.environ.copy()
        env["PATH"] = tmp.name + os.pathsep + env.get("PATH", "")
        env["FAKE_SCENARIO"] = scenario
        return subprocess.run(
            ["nu", "-c", f"source {SETUP}; wait_for_opensearch_ready 0sec"],
            capture_output=True, text=True, timeout=timeout, env=env,
        )

    def test_green_succeeds(self):
        result = self._run("green")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_red_fails_closed(self):
        result = self._run("red", timeout=60)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("OpenSearch", result.stderr)

    def test_unreachable_fails_closed(self):
        result = self._run("unreachable", timeout=60)
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
