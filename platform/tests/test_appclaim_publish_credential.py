#!/usr/bin/env python3
"""Independent-review follow-up fixes for Issue #285 (digiorg/core).

The first pass of #285 (locked by test_appclaim_delivery.py) wired the
GitOps sink Application, the claims/ path contract, and least-privilege
Gitea identities for Crossplane and ArgoCD. Independent review afterwards
found three remaining blockers this module locks:

  * Backstage's own create-pull-request publish action (core-portal
    app-config.yaml: integrations.gitea[0].password: ${GITEA_TOKEN}) has no
    credential wired into core/platform/base/backstage/deployment.yaml, and
    no dedicated least-privilege identity provisions it -- Backstage would
    crash-loop or silently publish with no credential.
  * `gitea admin user create --password "$generated"` puts a freshly
    generated secret directly into a Kubernetes exec process argument
    (visible via `ps` on the node) for the crossplane-provisioner and
    argocd-reader identities.
  * `configure_crossplane_gitea_credentials` / `configure_argocd_gitea_access`
    return immediately whenever their token Secret already exists, so a
    resumed run never repairs team/collaborator membership an operator (or
    Gitea data loss) may have revoked out-of-band -- only the token value is
    resume-safe, not the authorization it depends on.

Run:
    python3 platform/tests/test_appclaim_publish_credential.py
"""

import base64
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
DEPLOYMENT = os.path.join(REPO_ROOT, "platform", "base", "backstage", "deployment.yaml")


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _func_body(text, name):
    start = text.index(f"def {name} ")
    end = text.index("\ndef ", start + 10)
    return text[start:end]


class SetupTextFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = _read(SETUP)


class BackstageGiteaTokenDeploymentTest(unittest.TestCase):
    """Blocker #2: Backstage's Deployment must actually wire GITEA_TOKEN from
    a dedicated Secret, fail-closed (optional: false)."""

    @classmethod
    def setUpClass(cls):
        with open(DEPLOYMENT, encoding="utf-8") as fh:
            cls.doc = yaml.safe_load(fh)
        containers = cls.doc["spec"]["template"]["spec"]["containers"]
        cls.env = {e["name"]: e for e in containers[0]["env"]}

    def test_gitea_token_env_is_present(self):
        self.assertIn("GITEA_TOKEN", self.env)

    def test_gitea_token_is_sourced_from_a_dedicated_secret_not_backstage_secrets(self):
        ref = self.env["GITEA_TOKEN"]["valueFrom"]["secretKeyRef"]
        # Must NOT be the shared multi-key backstage-secrets object: a single-key
        # `persist_opaque_secret` apply against that object would silently wipe
        # its other keys (POSTGRES_PASSWORD, AUTH_SESSION_SECRET, ...).
        self.assertNotEqual(ref["name"], "backstage-secrets")
        self.assertEqual(ref["name"], "backstage-gitea-credentials")
        self.assertEqual(ref["key"], "GITEA_TOKEN")

    def test_gitea_token_is_required_not_optional(self):
        ref = self.env["GITEA_TOKEN"]["valueFrom"]["secretKeyRef"]
        self.assertEqual(ref.get("optional"), False)


class BackstageGiteaPublisherIdentityTest(SetupTextFixture):
    """A dedicated least-privilege Gitea identity provisions the GITEA_TOKEN
    Backstage's own publish action uses -- write access to DigiOrg/app-config
    only, never the platform bootstrap token or the other two #285
    identities (crossplane-provisioner, argocd-reader)."""

    def test_function_is_defined_exactly_once(self):
        self.assertEqual(
            self.text.count("def configure_backstage_gitea_publisher ["), 1
        )

    def test_configure_gitea_calls_it_after_argocd_access(self):
        body = _func_body(self.text, "configure_gitea")
        self.assertIn("configure_backstage_gitea_publisher", body)
        self.assertLess(
            body.index("configure_argocd_gitea_access"),
            body.index("configure_backstage_gitea_publisher"),
        )

    def test_token_is_write_repository_only(self):
        body = _func_body(self.text, "configure_backstage_gitea_publisher")
        self.assertIn("--scopes write:repository --raw", body)
        self.assertNotIn("write:admin", body)
        self.assertNotIn("write:organization", body)

    def test_is_a_write_collaborator_on_app_config_not_an_org_member(self):
        body = _func_body(self.text, "configure_backstage_gitea_publisher")
        self.assertIn("collaborators/backstage-appclaim-publisher", body)
        self.assertIn('\\"permission\\":\\"write\\"', body)
        self.assertNotIn("/teams/", body)

    def test_never_reuses_the_bootstrap_admin_or_sibling_identities(self):
        body = _func_body(self.text, "configure_backstage_gitea_publisher")
        self.assertNotIn("gitea_admin_password", body)
        self.assertNotIn("digiorgadmin", body)
        self.assertNotIn("crossplane-provisioner", body)
        self.assertNotIn("argocd-reader", body)

    def test_credential_never_appears_in_argv_only_stdin(self):
        body = _func_body(self.text, "configure_backstage_gitea_publisher")
        for match in re.finditer(r"curl[^\n']*", body):
            self.assertNotIn('-H "Authorization', match.group(0))

    def test_persists_into_the_dedicated_backstage_secret_key(self):
        body = _func_body(self.text, "configure_backstage_gitea_publisher")
        self.assertIn(
            'persist_opaque_secret "backstage" "backstage-gitea-credentials" "GITEA_TOKEN"',
            body,
        )


class GiteaUserCreationNeverPutsPasswordInArgvTest(SetupTextFixture):
    """Blocker #3: generated passwords must never be interpolated into a
    `gitea admin user create --password ...` process argument. The identity
    authenticates only via its later admin-generated access token, so the
    Gitea CLI's own `--random-password` (stdout discarded, never printed) is
    sufficient -- and the exact upstream-validated mechanism."""

    def test_random_password_helper_is_defined(self):
        self.assertEqual(
            self.text.count("def gitea_create_user_random_password ["), 1
        )

    def test_helper_uses_random_password_flag_not_a_literal_argv_password(self):
        body = _func_body(self.text, "gitea_create_user_random_password")
        self.assertIn("--random-password", body)
        self.assertIn("--random-password-length", body)
        self.assertNotRegex(body, r'--password\s+"')

    def test_helper_never_prints_the_generated_password(self):
        body = _func_body(self.text, "gitea_create_user_random_password")
        self.assertNotIn("print $create_result.stdout", body)
        self.assertNotIn("print ($create_result.stdout", body)

    def test_crossplane_provisioner_and_argocd_reader_use_the_helper(self):
        for fn, username in (
            ("configure_crossplane_gitea_credentials", "crossplane-provisioner"),
            ("configure_argocd_gitea_access", "argocd-reader"),
        ):
            with self.subTest(fn=fn):
                body = _func_body(self.text, fn)
                self.assertIn("gitea_create_user_random_password", body)
                self.assertIn(username, body)
                # No leftover generated-password argv interpolation for this identity.
                self.assertNotRegex(body, r'--password\s+"\(\$\w*password\w*\)"')

    def test_generated_password_variables_are_gone_from_both_functions(self):
        for fn in ("configure_crossplane_gitea_credentials", "configure_argocd_gitea_access"):
            body = _func_body(self.text, fn)
            self.assertNotIn("(generate_password)", body)


class GiteaIdentityResumeDriftRepairTest(SetupTextFixture):
    """Blocker #9: an existing token Secret must not skip re-verifying that
    the underlying Gitea user/team/collaborator authorization still exists.
    Only token generation (which would rotate the credential) may be skipped
    once the Secret is already present -- membership repair must run first,
    unconditionally."""

    def _assert_membership_repaired_before_short_circuit(
        self, fn_name, secret_marker, membership_marker,
        return_gate_marker="if $secret_exists {",
    ):
        body = _func_body(self.text, fn_name)
        secret_exists_check = body.index("secret_exists")
        membership_index = body.index(membership_marker)
        # "secret_exists" must be *read* near the top (to compute the flag) but
        # the actual preserve gate must come strictly after the membership
        # repair call, not before it.
        return_gate = body.index(return_gate_marker)
        self.assertLess(secret_exists_check, membership_index)
        self.assertLess(
            membership_index,
            return_gate,
            f"{fn_name}: membership repair ({membership_marker!r}) must run "
            "before the resume short-circuit, not be skipped by it",
        )

    def test_crossplane_credentials_repairs_team_membership_before_returning(self):
        self._assert_membership_repaired_before_short_circuit(
            "configure_crossplane_gitea_credentials",
            "crossplane-gitea-credentials",
            "platform-provisioners",
            "if $secret_exists and ($scope_contract_current == $scope_contract)",
        )

    def test_argocd_access_repairs_collaborator_before_returning(self):
        self._assert_membership_repaired_before_short_circuit(
            "configure_argocd_gitea_access",
            "app-config-repo-creds",
            "collaborators/argocd-reader",
        )

    def test_backstage_publisher_repairs_collaborator_before_returning(self):
        self._assert_membership_repaired_before_short_circuit(
            "configure_backstage_gitea_publisher",
            "backstage-gitea-credentials",
            "collaborators/backstage-appclaim-publisher",
        )


class CrossplaneGiteaSecretStateBehaviourTest(unittest.TestCase):
    ANNOTATION = "platform.digiorg.io/gitea-token-scopes"
    CONTRACT = "write:organization,write:repository"

    def _run(self, payload):
        env = os.environ.copy()
        env["SECRET_JSON"] = payload
        return subprocess.run(
            [
                "nu",
                "--no-config-file",
                "-c",
                f"source {SETUP}; parse_crossplane_gitea_secret_state "
                "$env.SECRET_JSON 'platform.digiorg.io/gitea-token-scopes' | to json",
            ],
            capture_output=True,
            text=True,
            env=env,
            timeout=15,
        )

    def _secret(self, token=b"usable-token", annotation=True):
        annotations = {self.ANNOTATION: self.CONTRACT} if annotation else {}
        return json.dumps(
            {
                "metadata": {"annotations": annotations},
                "data": {"token": base64.b64encode(token).decode()},
            }
        )

    def test_valid_secret_returns_only_the_scope_contract(self):
        result = self._run(self._secret())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"scope_contract_current": self.CONTRACT})
        self.assertNotIn("usable-token", result.stdout + result.stderr)

    def test_valid_unmarked_secret_is_classified_as_stale(self):
        result = self._run(self._secret(annotation=False))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"scope_contract_current": ""})

    def test_malformed_or_unusable_secret_fails_closed(self):
        cases = {
            "malformed-json": "{",
            "missing-token": json.dumps({"metadata": {"annotations": {}}}),
            "invalid-base64": json.dumps({"metadata": {"annotations": {}}, "data": {"token": "%%%"}}),
            "empty-token": self._secret(token=b""),
            "whitespace-token": self._secret(token=b"  \n"),
        }
        for name, payload in cases.items():
            with self.subTest(name=name):
                result = self._run(payload)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("usable-token", result.stdout + result.stderr)


class CrossplaneGiteaTeamContractBehaviourTest(unittest.TestCase):
    EXACT = {
        "name": "platform-provisioners",
        "permission": "none",
        "includes_all_repositories": False,
        "can_create_org_repo": True,
        "units": ["repo.code"],
        "units_map": {"repo.code": "write"},
    }

    def _matches(self, team):
        env = os.environ.copy()
        env["TEAM_JSON"] = json.dumps(team)
        result = subprocess.run(
            [
                "nu",
                "--no-config-file",
                "-c",
                f"source {SETUP}; $env.TEAM_JSON | from json | "
                "crossplane_gitea_team_is_exact | to json",
            ],
            capture_output=True,
            text=True,
            env=env,
            timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_exact_team_is_accepted(self):
        self.assertTrue(self._matches(self.EXACT))

    def test_each_team_permission_drift_is_rejected(self):
        drifts = {
            "name": "other",
            "permission": "write",
            "includes_all_repositories": True,
            "can_create_org_repo": False,
            "units": ["repo.code", "repo.issues"],
            "units_map": {"repo.code": "admin"},
        }
        for field, value in drifts.items():
            with self.subTest(field=field):
                team = dict(self.EXACT)
                team[field] = value
                self.assertFalse(self._matches(team))

    def test_reconciliation_uses_pinned_gitea_patch_contract_and_readback(self):
        body = _func_body(_read(SETUP), "configure_crossplane_gitea_credentials")
        self.assertIn("-X PATCH", body)
        self.assertIn(
            'let team_url = $"https://digiorg.local/gitea/api/v1/teams/($provisioners_team_id)"',
            body,
        )
        self.assertIn("sh $team_url", body)
        for fragment in (
            '\\"permission\\":\\"none\\"',
            '\\"includes_all_repositories\\":false',
            '\\"can_create_org_repo\\":true',
            '\\"units_map\\":{\\"repo.code\\":\\"write\\"}',
        ):
            self.assertIn(fragment, body)
        self.assertGreaterEqual(body.count('\\"permission\\":\\"none\\"'), 2)
        self.assertNotIn('\\"permission\\":\\"write\\"', body)
        self.assertIn("crossplane_gitea_team_is_exact", body)
        self.assertIn("platform-provisioners Gitea team did not match", body)


class CrossplaneProvisionerTokenScopeUpgradeTest(SetupTextFixture):
    """Issue #301: org-repository creation needs both PAT scopes, and an
    already persisted pre-fix token must rotate once rather than being trusted
    forever merely because the Secret exists."""

    def setUp(self):
        self.body = _func_body(
            self.text, "configure_crossplane_gitea_credentials"
        )

    def test_scope_contract_is_versioned_on_the_secret(self):
        self.assertIn(
            'let scope_contract = "write:organization,write:repository"',
            self.body,
        )
        self.assertIn(
            'let scope_annotation = "platform.digiorg.io/gitea-token-scopes"',
            self.body,
        )
        self.assertIn("--annotation-key $scope_annotation", self.body)
        self.assertIn("--annotation-value $scope_contract", self.body)

    def test_resume_preserves_only_a_matching_scope_contract(self):
        self.assertIn(
            "if $secret_exists and ($scope_contract_current == $scope_contract)",
            self.body,
        )
        preserve_gate = self.body.index(
            "if $secret_exists and ($scope_contract_current == $scope_contract)"
        )
        self.assertIn("return", self.body[preserve_gate:preserve_gate + 400])

    def test_team_repair_precedes_scope_preserve_or_rotation(self):
        self.assertLess(
            self.body.index("platform-provisioners"),
            self.body.index("$scope_contract_current == $scope_contract"),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
