#!/usr/bin/env python3
"""AppClaim GitOps delivery + least-privilege credential lifecycle (Issue #285).

The catalog side (digiorg/core-catalog) already has a real-KCL render harness
proving the pipeline Composition's conditional/iterative behaviour and the
provider-http mapping contract. This module locks the `core`-side half of the
same issue that the catalog cannot prove on its own, because it depends on
`scripts/local-setup.nu` and `apps/platform/*.yaml`:

  * the two Secrets the Composition's `{{ name:namespace:key }}` placeholders
    require (`crossplane-gitea-credentials`, `crossplane-harbor-credentials`)
    are actually created, by a dedicated least-privilege identity -- never the
    Gitea platform-admin bootstrap token or the Harbor admin credential;
  * every credential-bearing value new in this issue travels only over stdin
    into `kubectl apply -f -`, exactly like the pre-existing
    `persist_gitea_bootstrap_token` (test_bootstrap_convergence.py already
    locks that one);
  * the declarative Harbor system-robot bootstrap Request only ever grants
    `project:create` (system-wide) and `robot:create`/`robot:read`
    (project-wildcard) -- never delete/user/registry/admin permissions;
  * the app-config GitOps sink Application, its dedicated read-only Gitea
    credential, and the `app-claims` namespace are wired together
    consistently.

Pure python3 + PyYAML, plus a few real-Nushell behaviour checks of the new
pure helpers (same technique as test_bootstrap_convergence.py)::

    python3 platform/tests/test_appclaim_delivery.py
"""

import json
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
APPS_DIR = os.path.join(REPO_ROOT, "apps", "platform")
HARBOR_REQUEST = os.path.join(REPO_ROOT, "crossplane", "bootstrap", "harbor-robot-request.yaml")
NAMESPACES_YAML = os.path.join(REPO_ROOT, "platform", "base", "namespaces", "namespaces.yaml")
KYVERNO_BLOCK_POLICY = os.path.join(
    REPO_ROOT, "policies", "kyverno", "cluster-policies", "block-appclaims-in-system-namespaces.yaml"
)
CHECK_PINS = os.path.join(REPO_ROOT, "scripts", "check_pins.py")
# Sibling multi-repo checkout convention (see docs/guides/local-development.md);
# core-portal is out of scope to edit for this issue, but its already-independently-
# tested publishPhase.git config is the authoritative contract this repo must match.
CORE_PORTAL_APP_CONFIG = os.path.abspath(
    os.path.join(REPO_ROOT, "..", "core-portal", "app-config.yaml")
)


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _func_body(text, name):
    start = text.index(f"def {name} ")
    end = text.index("\ndef ", start + 10)
    return text[start:end]


def _app(name):
    with open(os.path.join(APPS_DIR, f"{name}.yaml"), encoding="utf-8") as fh:
        doc = yaml.safe_load(fh)
    assert doc["kind"] == "Application"
    return doc


def _wave(name):
    return int(_app(name)["metadata"]["annotations"]["argocd.argoproj.io/sync-wave"])


class SetupTextFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = _read(SETUP)


class NewFunctionsExistTest(SetupTextFixture):
    """Every new helper/wiring point this issue introduces is present exactly once."""

    def test_new_functions_are_defined(self):
        for name in (
            "persist_opaque_secret",
            "persist_argocd_repo_secret",
            "configure_app_config_repo",
            "configure_crossplane_gitea_credentials",
            "configure_argocd_gitea_access",
        ):
            self.assertEqual(
                self.text.count(f"def {name} ["), 1, f"{name} must be defined exactly once"
            )

    def test_configure_gitea_calls_all_three_new_phases(self):
        body = _func_body(self.text, "configure_gitea")
        for call in (
            "configure_app_config_repo",
            "configure_crossplane_gitea_credentials",
            "configure_argocd_gitea_access",
        ):
            self.assertIn(call, body)


class SecretTransportTest(unittest.TestCase):
    """`persist_opaque_secret`/`persist_argocd_repo_secret` never put a
    credential value in argv, and are fail-closed on apply/readback failure
    or mismatch -- the same discipline test_bootstrap_convergence.py already
    locks for `persist_gitea_bootstrap_token`, generalized for the new
    multi-caller helper and the ArgoCD multi-key repository Secret shape."""

    def _fake_kubectl_single_key(self, tmp, scenario):
        fake = os.path.join(tmp, "kubectl")
        with open(fake, "w", encoding="utf-8") as fh:
            fh.write(
                "#!/usr/bin/env python3\n"
                "import json, os, sys\n"
                "args = sys.argv[1:]\n"
                "with open(os.environ['KUBECTL_ARGV_LOG'], 'a', encoding='utf-8') as log: "
                "log.write(json.dumps(args) + '\\n')\n"
                "scenario = os.environ.get('FAKE_SCENARIO', 'success')\n"
                "if 'apply' in args:\n"
                "    data = sys.stdin.read()\n"
                "    if scenario == 'apply_failure': sys.exit(1)\n"
                "    open(os.environ['MANIFEST_LOG'], 'w', encoding='utf-8').write(data)\n"
                "    print('secret/x configured')\n"
                "elif 'get' in args:\n"
                "    if scenario == 'readback_failure': sys.exit(1)\n"
                "    obj = json.load(open(os.environ['MANIFEST_LOG'], encoding='utf-8'))\n"
                "    key = list(obj['data'].keys())[0]\n"
                "    value = obj['data'][key]\n"
                "    print('d3Jvbmc=' if scenario == 'readback_mismatch' else value, end='')\n"
                "else:\n"
                "    sys.exit(2)\n"
            )
        os.chmod(fake, 0o755)
        return fake

    def _run_opaque_secret_transport(self, scenario):
        sentinel = "sentinel-opaque-secret-value-never-in-argv"
        with tempfile.TemporaryDirectory() as tmp:
            self._fake_kubectl_single_key(tmp, scenario)
            argv_log = os.path.join(tmp, "kubectl-argv")
            manifest_log = os.path.join(tmp, "manifest.json")
            env = os.environ.copy()
            env["PATH"] = tmp + os.pathsep + env.get("PATH", "")
            env["KUBECTL_ARGV_LOG"] = argv_log
            env["MANIFEST_LOG"] = manifest_log
            env["FAKE_SCENARIO"] = scenario
            env["TEST_VALUE"] = sentinel
            result = subprocess.run(
                ["nu", "-c", f"source {SETUP}; persist_opaque_secret crossplane-system "
                              f"crossplane-gitea-credentials token $env.TEST_VALUE"],
                capture_output=True, text=True, env=env, timeout=15,
            )
            argv = _read(argv_log) if os.path.exists(argv_log) else ""
            manifest = None
            if os.path.exists(manifest_log):
                manifest = json.loads(_read(manifest_log))
            return result, sentinel, argv, manifest

    def test_opaque_secret_transport_is_argv_free_and_correct(self):
        result, sentinel, argv, manifest = self._run_opaque_secret_transport("success")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(sentinel, argv)
        self.assertNotIn(sentinel, result.stdout + result.stderr)
        self.assertIsNotNone(manifest)
        assert manifest is not None
        self.assertEqual(manifest["metadata"]["name"], "crossplane-gitea-credentials")
        self.assertEqual(manifest["metadata"]["namespace"], "crossplane-system")
        import base64 as b64
        self.assertEqual(b64.b64decode(manifest["data"]["token"]).decode(), sentinel)

    def test_opaque_secret_apply_failure_is_fatal(self):
        result, _, _, _ = self._run_opaque_secret_transport("apply_failure")
        self.assertNotEqual(result.returncode, 0)

    def test_opaque_secret_readback_failure_and_mismatch_are_fatal(self):
        for scenario in ("readback_failure", "readback_mismatch"):
            with self.subTest(scenario=scenario):
                result, _, _, _ = self._run_opaque_secret_transport(scenario)
                self.assertNotEqual(result.returncode, 0)

    def test_opaque_secret_rejects_empty_value(self):
        result = subprocess.run(
            ["nu", "-c", f"source {SETUP}; persist_opaque_secret crossplane-system x y ''"],
            capture_output=True, text=True, timeout=15,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("empty", result.stderr.lower())

    # --- persist_argocd_repo_secret: multi-key repository Secret shape ---

    def _fake_kubectl_repo_secret(self, tmp, scenario, pre_existing):
        fake = os.path.join(tmp, "kubectl")
        with open(fake, "w", encoding="utf-8") as fh:
            fh.write(
                "#!/usr/bin/env python3\n"
                "import json, os, sys\n"
                "args = sys.argv[1:]\n"
                "with open(os.environ['KUBECTL_ARGV_LOG'], 'a', encoding='utf-8') as log: "
                "log.write(json.dumps(args) + '\\n')\n"
                "scenario = os.environ.get('FAKE_SCENARIO', 'success')\n"
                "pre_existing = os.environ.get('PRE_EXISTING') == '1'\n"
                "if 'get' in args and 'jsonpath={.data.password}' not in args:\n"
                # existence probe used by the resume-preserve short-circuit
                "    sys.exit(0 if pre_existing else 1)\n"
                "elif 'apply' in args:\n"
                "    data = sys.stdin.read()\n"
                "    if scenario == 'apply_failure': sys.exit(1)\n"
                "    open(os.environ['MANIFEST_LOG'], 'w', encoding='utf-8').write(data)\n"
                "    print('secret/x configured')\n"
                "elif 'get' in args:\n"
                "    if scenario == 'readback_failure': sys.exit(1)\n"
                "    obj = json.load(open(os.environ['MANIFEST_LOG'], encoding='utf-8'))\n"
                "    value = obj['data']['password']\n"
                "    print('d3Jvbmc=' if scenario == 'readback_mismatch' else value, end='')\n"
                "else:\n"
                "    sys.exit(2)\n"
            )
        os.chmod(fake, 0o755)
        return fake

    def _run_repo_secret_transport(self, scenario, pre_existing=False):
        sentinel = "sentinel-argocd-repo-password-never-in-argv"
        with tempfile.TemporaryDirectory() as tmp:
            self._fake_kubectl_repo_secret(tmp, scenario, pre_existing)
            argv_log = os.path.join(tmp, "kubectl-argv")
            manifest_log = os.path.join(tmp, "manifest.json")
            env = os.environ.copy()
            env["PATH"] = tmp + os.pathsep + env.get("PATH", "")
            env["KUBECTL_ARGV_LOG"] = argv_log
            env["MANIFEST_LOG"] = manifest_log
            env["FAKE_SCENARIO"] = scenario
            env["PRE_EXISTING"] = "1" if pre_existing else "0"
            env["TEST_VALUE"] = sentinel
            result = subprocess.run(
                ["nu", "-c", f"source {SETUP}; persist_argocd_repo_secret app-config-repo-creds "
                              f"https://digiorg.local/gitea/DigiOrg/app-config.git argocd-reader $env.TEST_VALUE"],
                capture_output=True, text=True, env=env, timeout=15,
            )
            argv = _read(argv_log) if os.path.exists(argv_log) else ""
            manifest = None
            if os.path.exists(manifest_log):
                manifest = json.loads(_read(manifest_log))
            return result, sentinel, argv, manifest

    def test_repo_secret_has_argocd_repository_label_and_all_three_keys(self):
        result, sentinel, argv, manifest = self._run_repo_secret_transport("success")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(sentinel, argv)
        self.assertIsNotNone(manifest)
        assert manifest is not None
        self.assertEqual(
            manifest["metadata"]["labels"]["argocd.argoproj.io/secret-type"], "repository"
        )
        self.assertEqual(manifest["metadata"]["namespace"], "argocd")
        self.assertEqual(set(manifest["data"].keys()), {"url", "username", "password"})
        import base64 as b64
        self.assertEqual(
            b64.b64decode(manifest["data"]["url"]).decode(),
            "https://digiorg.local/gitea/DigiOrg/app-config.git",
        )
        self.assertEqual(b64.b64decode(manifest["data"]["username"]).decode(), "argocd-reader")

    def test_repo_secret_is_preserved_when_already_present(self):
        result, sentinel, argv, manifest = self._run_repo_secret_transport("success", pre_existing=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        # No apply/readback should have run at all -- the short-circuit exits
        # before ever touching the manifest.
        self.assertIsNone(manifest)
        self.assertNotIn("apply", argv)

    def test_repo_secret_apply_failure_is_fatal(self):
        result, _, _, _ = self._run_repo_secret_transport("apply_failure")
        self.assertNotEqual(result.returncode, 0)

    def test_repo_secret_readback_mismatch_is_fatal(self):
        result, _, _, _ = self._run_repo_secret_transport("readback_mismatch")
        self.assertNotEqual(result.returncode, 0)


class LeastPrivilegeGiteaScopeTest(SetupTextFixture):
    """The two new Gitea identities must never carry the broad admin scope
    list used for the one-time platform bootstrap token."""

    def test_crossplane_provisioner_token_is_write_repository_only(self):
        body = _func_body(self.text, "configure_crossplane_gitea_credentials")
        self.assertIn("--scopes write:repository --raw", body)
        self.assertNotIn("write:admin", body)
        self.assertNotIn("write:user", body)
        self.assertNotIn("write:organization", body)

    def test_argocd_reader_token_is_read_repository_only(self):
        body = _func_body(self.text, "configure_argocd_gitea_access")
        self.assertIn("--scopes read:repository --raw", body)
        self.assertNotIn("write:", body)

    def test_provisioner_team_is_restricted_to_repo_code_unit(self):
        body = _func_body(self.text, "configure_crossplane_gitea_credentials")
        self.assertIn('\\"units\\":[\\"repo.code\\"]', body)
        self.assertIn("can_create_org_repo", body)

    def test_argocd_reader_is_repo_collaborator_not_org_member(self):
        body = _func_body(self.text, "configure_argocd_gitea_access")
        self.assertIn("collaborators/argocd-reader", body)
        self.assertNotIn("/teams/", body)

    def test_neither_new_identity_reuses_the_bootstrap_admin_token(self):
        for fn in ("configure_crossplane_gitea_credentials", "configure_argocd_gitea_access"):
            body = _func_body(self.text, fn)
            self.assertNotIn("gitea_admin_password", body)
            self.assertNotIn("digiorgadmin", body)

    def test_new_credentials_never_appear_in_argv_only_stdin(self):
        # Same discipline as the existing org-creation/team-membership calls:
        # the bootstrap token and generated passwords/tokens travel via a
        # `read -r token` stdin pipe into `curl --config -`, never as a `curl
        # -H "Authorization: ..."` CLI argument.
        for fn in ("configure_app_config_repo", "configure_crossplane_gitea_credentials",
                   "configure_argocd_gitea_access"):
            body = _func_body(self.text, fn)
            for match in re.finditer(r"curl[^\n']*", body):
                self.assertNotIn("-H \"Authorization", match.group(0))


class HarborBootstrapLeastPrivilegeTest(unittest.TestCase):
    """The declarative system-robot Request must only ever be able to create
    projects and manage robots -- never delete/read/update anything, and
    never touch users, registries, or system configuration."""

    @classmethod
    def setUpClass(cls):
        with open(HARBOR_REQUEST, encoding="utf-8") as fh:
            cls.doc = yaml.safe_load(fh)
        cls.body = json.loads(cls.doc["spec"]["forProvider"]["payload"]["body"])

    def test_is_a_system_level_robot(self):
        self.assertEqual(self.body["level"], "system")

    def test_permissions_are_exactly_project_create_and_robot_create_read(self):
        seen = set()
        for perm in self.body["permissions"]:
            for access in perm["access"]:
                seen.add((perm["kind"], perm["namespace"], access["resource"], access["action"]))
        self.assertEqual(
            seen,
            {
                ("system", "/", "project", "create"),
                ("project", "*", "robot", "create"),
                ("project", "*", "robot", "read"),
                ("project", "*", "artifact", "read"),
            },
        )

    def test_no_delete_update_or_wildcard_actions(self):
        for perm in self.body["permissions"]:
            for access in perm["access"]:
                self.assertNotIn(access["action"], ("delete", "update", "*"))

    def test_no_user_registry_or_system_configuration_access(self):
        forbidden_resources = {"user", "user-group", "registry", "configuration", "replication",
                                "garbage-collection", "scanner", "ldap-user"}
        for perm in self.body["permissions"]:
            for access in perm["access"]:
                self.assertNotIn(access["resource"], forbidden_resources)

    def test_authorization_header_is_a_secret_placeholder_not_a_literal(self):
        auth = self.doc["spec"]["forProvider"]["headers"]["Authorization"]
        self.assertEqual(len(auth), 1)
        self.assertRegex(auth[0], r"^Basic \{\{\s*[^:{}\s]+:[^:{}\s]+:[^:{}\s]+\s*\}\}$")
        self.assertNotIn("changeme", auth[0].lower())

    def test_basic_auth_secret_key_is_computed_via_base64_not_stored_plaintext_twice(self):
        injections = self.doc["spec"]["forProvider"]["secretInjectionConfigs"]
        self.assertEqual(len(injections), 1)
        keys = {m["secretKey"]: m["responseJQ"] for m in injections[0]["keyMappings"]}
        self.assertEqual(set(keys), {"name", "secret", "basicAuth"})
        self.assertIn("@base64", keys["basicAuth"])
        self.assertIn(".body.name", keys["basicAuth"])
        self.assertIn(".body.secret", keys["basicAuth"])

    def test_secret_injected_into_crossplane_system_namespace(self):
        ref = self.doc["spec"]["forProvider"]["secretInjectionConfigs"][0]["secretRef"]
        self.assertEqual(ref["name"], "crossplane-harbor-credentials")
        self.assertEqual(ref["namespace"], "crossplane-system")

    def test_deletion_policy_is_orphan_not_delete(self):
        # A one-time bootstrap identity must not be deleted if the Application
        # is ever removed/recreated -- that would silently invalidate every
        # AppClaim mid-flight relying on it.
        self.assertEqual(self.doc["spec"]["deletionPolicy"], "Orphan")

    def test_payload_body_is_valid_json(self):
        # Already loaded in setUpClass; a JSONDecodeError there would have
        # failed the whole test class, so this documents the invariant.
        self.assertIsInstance(self.body, dict)


class HarborAdminBasicAuthSecretTest(SetupTextFixture):
    """local-setup.nu must precompute base64(admin:password) once, out of
    argv, because provider-http's `{{ name:namespace:key }}` templating
    substitutes a secret value verbatim -- it does not encode."""

    def test_harbor_admin_basic_secret_is_created_via_persist_opaque_secret(self):
        body = _func_body(self.text, "create_platform_namespaces_secrets")
        self.assertIn(
            'persist_opaque_secret "harbor" "harbor-admin-basic-auth" "value"', body
        )
        self.assertIn("encode base64", body)

    def test_basic_auth_value_is_never_passed_via_from_literal(self):
        body = _func_body(self.text, "create_platform_namespaces_secrets")
        self.assertNotIn("--from-literal=value=", body)
        self.assertNotIn("--from-literal=HARBOR_ADMIN_BASIC", body)


class AppConfigDeliveryWiringTest(unittest.TestCase):
    """The Argo Application, its repository credential, and the app-claims
    namespace are all consistent with each other."""

    def test_app_config_repo_url_matches_the_persisted_credential_url(self):
        app = _app("app-config")
        repo_url = app["spec"]["source"]["repoURL"]
        setup_text = _read(SETUP)
        access_body = _func_body(setup_text, "configure_argocd_gitea_access")
        self.assertIn(repo_url, access_body)

    def test_app_config_destination_namespace_is_app_claims(self):
        app = _app("app-config")
        self.assertEqual(app["spec"]["destination"]["namespace"], "app-claims")

    def test_app_config_uses_automated_sync_so_merged_prs_actually_reconcile(self):
        app = _app("app-config")
        self.assertIn("automated", app["spec"]["syncPolicy"])

    def test_app_claims_namespace_is_declared(self):
        namespaces = [
            doc for doc in yaml.safe_load_all(_read(NAMESPACES_YAML)) if doc
        ]
        names = {ns["metadata"]["name"] for ns in namespaces}
        self.assertIn("app-claims", names)

    def test_app_claims_namespace_is_not_blocked_for_appclaims(self):
        # If app-claims were ever added to the Kyverno block-list, the
        # reconciliation contract above would silently reject every AppClaim.
        policy = yaml.safe_load(_read(KYVERNO_BLOCK_POLICY))
        blocked = policy["spec"]["rules"][0]["match"]["any"][0]["resources"]["namespaces"]
        self.assertNotIn("app-claims", blocked)

    def test_crossplane_harbor_bootstrap_syncs_after_provider_configs(self):
        self.assertGreater(_wave("crossplane-harbor-bootstrap"), _wave("crossplane-provider-configs"))

    def test_app_config_syncs_after_xrds_and_core_catalog(self):
        self.assertGreater(_wave("app-config"), _wave("crossplane-xrds"))
        self.assertGreater(_wave("app-config"), _wave("core-catalog"))

    def test_app_config_syncs_after_the_optional_cnpg_prerequisite(self):
        # A database-enabled AppClaim fails closed on the CNPG prerequisite
        # (core-catalog pipeline.yaml); placing app-config after cnpg/cnpg-cluster
        # means that, when future-infra has already been promoted, an AppClaim
        # merged the same session never races an unresolved CNPG CRD/webhook.
        self.assertGreater(_wave("app-config"), _wave("cnpg"))
        self.assertGreater(_wave("app-config"), _wave("cnpg-cluster"))

    def test_pinned_functions_backing_the_pipeline_are_installed(self):
        # Issue #285 architecture decision #3: the catalog's single pipeline
        # Composition (core-catalog/compositions/local/pipeline.yaml) needs
        # both crossplane Functions installed here for its two-step pipeline.
        kustom = yaml.safe_load(_read(os.path.join(REPO_ROOT, "crossplane", "providers", "packages", "kustomization.yaml")))
        self.assertIn("function-kcl.yaml", kustom["resources"])
        self.assertIn("function-auto-ready.yaml", kustom["resources"])


class GitOpsPathContractTest(unittest.TestCase):
    """The app-config Application must watch the exact directory core-portal's
    (already independently tested) publishPhase.git.targetPath publishes
    generated AppClaim manifests to. core-portal publishes to `claims/`; core
    previously watched/seeded `appclaims/` -- a P0 contract mismatch that
    would silently strand every merged AppClaim PR unreconciled."""

    EXPECTED_PATH = "claims"

    def test_app_config_application_watches_claims_directory(self):
        app = _app("app-config")
        self.assertEqual(app["spec"]["source"]["path"], self.EXPECTED_PATH)

    def test_bootstrap_seeds_the_claims_directory_not_appclaims(self):
        body = _func_body(_read(SETUP), "configure_app_config_repo")
        self.assertIn("contents/claims/.gitkeep", body)
        self.assertNotIn("appclaims", body)


class GitOpsRepoURLTest(unittest.TestCase):
    """The Argo Application source, the ArgoCD repository-credential Secret
    URL, and the pin-policy first-party allowlist must all agree on the exact
    same Gitea git URL for the app-config GitOps sink -- and, per the Issue
    #285 TLS hardening pass, it must be the trusted digiorg.local ingress
    over HTTPS (CA: cert-manager/digiorg-local-ca-secret), not a raw
    in-cluster plain-HTTP Service address. This matches core-portal's own
    already-tested Gitea integration (host: digiorg.local, baseUrl:
    https://digiorg.local/gitea) and the pipeline Composition's own
    giteaApiBase, both of which now talk to Gitea only through the trusted
    ingress."""

    EXPECTED_REPO_URL = "https://digiorg.local/gitea/DigiOrg/app-config.git"

    def test_app_config_application_source_uses_the_trusted_ingress_url(self):
        app = _app("app-config")
        self.assertEqual(app["spec"]["source"]["repoURL"], self.EXPECTED_REPO_URL)

    def test_app_config_source_is_not_the_in_cluster_service_dns_name(self):
        app = _app("app-config")
        self.assertNotIn("svc.cluster.local", app["spec"]["source"]["repoURL"])
        self.assertNotIn("http://", app["spec"]["source"]["repoURL"])

    def test_argocd_reader_credential_uses_the_same_url(self):
        body = _func_body(_read(SETUP), "configure_argocd_gitea_access")
        self.assertIn(self.EXPECTED_REPO_URL, body)

    def test_check_pins_first_party_allowlist_matches_and_drops_the_old_url(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location("check_pins", CHECK_PINS)
        check_pins = importlib.util.module_from_spec(spec)
        sys.modules["check_pins"] = check_pins
        spec.loader.exec_module(check_pins)
        self.assertIn(self.EXPECTED_REPO_URL, check_pins.FIRST_PARTY_REPOS)
        self.assertNotIn(
            "http://gitea-http.gitea.svc.cluster.local:3000/DigiOrg/app-config.git",
            check_pins.FIRST_PARTY_REPOS,
        )

    def test_argocd_trusts_the_digiorg_local_ca_for_this_repo(self):
        # The GitOps sink is now cloned via the digiorg.local ingress over
        # HTTPS -- ArgoCD's repo-server must trust the same CA already
        # registered for OIDC, via configs.tls.certificates (the argo-cd
        # chart's argocd-tls-certs-cm mechanism), keyed by the exact
        # hostname the repo URL uses.
        body = _func_body(_read(SETUP), "patch_argocd_oidc_ca")
        compact = body.replace(" ", "").replace("\n", "")
        self.assertIn('tls:{certificates:{"digiorg.local"', compact)


class CrossRepoPublishPathContractTest(unittest.TestCase):
    """Static, read-only cross-repo contract check against core-portal's own
    publishPhase.git config. core-portal is out of scope to edit for this
    issue; this only reads it. Skips cleanly when core-portal is not checked
    out at the conventional sibling path (e.g. a CI checkout of core alone)."""

    @classmethod
    def setUpClass(cls):
        if not os.path.isfile(CORE_PORTAL_APP_CONFIG):
            raise unittest.SkipTest(
                "core-portal not checked out at %s -- skipping cross-repo contract check"
                % CORE_PORTAL_APP_CONFIG
            )
        with open(CORE_PORTAL_APP_CONFIG, encoding="utf-8") as fh:
            portal_config = yaml.safe_load(fh)
        cls.publish_git = portal_config["kubernetesIngestor"]["crossplane"]["xrds"]["publishPhase"]["git"]

    def _parse_gitea_ingestor_repo_url(self, repo_url):
        # kubernetesIngestor's Gitea repoUrl shape: "<host>?owner=<org>&repo=<repo>"
        host, _, query = repo_url.partition("?")
        params = dict(pair.split("=", 1) for pair in query.split("&") if "=" in pair)
        return host, params.get("owner"), params.get("repo")

    def test_target_path_matches_the_app_config_applications_watched_path(self):
        app = _app("app-config")
        self.assertEqual(self.publish_git["targetPath"], app["spec"]["source"]["path"])

    def test_target_branch_matches_the_app_config_applications_revision(self):
        app = _app("app-config")
        self.assertEqual(self.publish_git["targetBranch"], app["spec"]["source"]["targetRevision"])

    def test_repo_host_owner_and_name_match_the_app_config_applications_source(self):
        app = _app("app-config")
        host, owner, repo = self._parse_gitea_ingestor_repo_url(self.publish_git["repoUrl"])
        self.assertEqual(f"https://{host}/gitea/{owner}/{repo}.git", app["spec"]["source"]["repoURL"])


class ControlFlowOrderingTest(SetupTextFixture):
    """The three new Phase-3 steps run after Gitea's own org/OIDC setup
    (they need the DigiOrg org + Owners team to exist) and before the
    Phase-6 global convergence gate that waits on their Applications."""

    def test_new_steps_run_after_org_creation_inside_configure_gitea(self):
        body = _func_body(self.text, "configure_gitea")
        org_step = body.index("Ensuring DigiOrg organisation exists")
        app_config_step = body.index("configure_app_config_repo")
        gitea_creds_step = body.index("configure_crossplane_gitea_credentials")
        argocd_access_step = body.index("configure_argocd_gitea_access")
        self.assertTrue(org_step < app_config_step < gitea_creds_step < argocd_access_step)

    def test_configure_gitea_runs_before_the_final_gate(self):
        up = _func_body(self.text, '"main up"')
        self.assertLess(up.index("configure_gitea"), up.index("wait_for_argocd_apps"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
