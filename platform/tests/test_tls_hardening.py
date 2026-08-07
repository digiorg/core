#!/usr/bin/env python3
"""Issue #285 TLS hardening: every credential-bearing first-party path
(Gitea API/Git, Harbor API, the OCI registry) must go through the trusted
digiorg.local ingress (CA: cert-manager/digiorg-local-ca-secret) over HTTPS.
No plain-HTTP credential transport, `curl -k`/`insecureSkipVerify`, or
`NODE_TLS_REJECT_UNAUTHORIZED=0` anywhere in that path.

This file covers the pieces that don't already have a dedicated home:
provider-http's shared ProviderConfig TLS trust, Backstage's CA mount, and
the bootstrap script's CA-copy-to-namespace + explicit-`--cacert` behavior.
(The Argo app-config repo URL and the Gitea Actions runner have their own
existing test files -- test_appclaim_delivery.py's GitOpsRepoURLTest and
test_gitea_actions_runner.py respectively -- and were extended in place.)

Run:
    python3 platform/tests/test_tls_hardening.py
"""

import os
import sys
import unittest

try:
    import yaml
except ImportError:  # pragma: no cover - environment guard
    sys.stderr.write("PyYAML is required: pip install pyyaml\n")
    raise

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SETUP = os.path.join(REPO_ROOT, "scripts", "local-setup.nu")
PROVIDER_HTTP_CONFIG = os.path.join(
    REPO_ROOT, "crossplane", "providers", "configs", "provider-http-config.yaml"
)
HARBOR_BOOTSTRAP_REQUEST = os.path.join(
    REPO_ROOT, "crossplane", "bootstrap", "harbor-robot-request.yaml"
)
BACKSTAGE_DEPLOYMENT = os.path.join(
    REPO_ROOT, "platform", "base", "backstage", "deployment.yaml"
)


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _func_body(text, name):
    start = text.index(f"def {name} ")
    end = text.index("\ndef ", start + 10)
    return text[start:end]


def _yaml_docs(path):
    return [d for d in yaml.safe_load_all(_read(path)) if d]


class SetupTextFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = _read(SETUP)


class ProviderHttpProviderConfigTlsTest(unittest.TestCase):
    """The shared provider-http ProviderConfig (referenced by every Request
    in both core's bootstrap and core-catalog's AppClaim pipeline) must
    carry spec.tls.caCertSecretRef, verified against the pinned v1.0.14
    ProviderConfigSpec (apis/cluster/v1alpha1/providerconfig_types.go:
    `TLS *common.TLSConfig \\`json:"tls,omitempty"\\``) so every Request
    inherits verified TLS without repeating trust config per-Request."""

    @classmethod
    def setUpClass(cls):
        cls.doc = yaml.safe_load(_read(PROVIDER_HTTP_CONFIG))

    def test_provider_config_declares_tls_ca_cert_secret_ref(self):
        tls = self.doc["spec"].get("tls")
        self.assertIsNotNone(tls, "spec.tls must be set")
        ref = tls["caCertSecretRef"]
        self.assertEqual(ref["key"], "ca.crt")
        self.assertTrue(ref["name"])
        self.assertTrue(ref["namespace"])

    def test_never_sets_insecure_skip_verify(self):
        tls = self.doc["spec"].get("tls") or {}
        self.assertFalse(tls.get("insecureSkipVerify", False))

    def test_ca_secret_referenced_lives_in_the_crossplane_provider_namespace(self):
        # crossplane-system is where every other credential Secret this
        # provider already reads (crossplane-gitea-credentials,
        # crossplane-harbor-credentials) also lives.
        ref = self.doc["spec"]["tls"]["caCertSecretRef"]
        self.assertEqual(ref["namespace"], "crossplane-system")


class HarborBootstrapRobotTransportTest(unittest.TestCase):
    """The one-time system-robot bootstrap Request must talk to Harbor
    through the trusted digiorg.local ingress, not the raw in-cluster
    harbor-core Service address."""

    @classmethod
    def setUpClass(cls):
        cls.doc = yaml.safe_load(_read(HARBOR_BOOTSTRAP_REQUEST))

    def test_base_url_is_https_digiorg_local(self):
        base_url = self.doc["spec"]["forProvider"]["payload"]["baseUrl"]
        self.assertEqual(base_url, "https://digiorg.local/api/v2.0")

    def test_no_plain_http_or_in_cluster_service_dns_remains(self):
        text = _read(HARBOR_BOOTSTRAP_REQUEST)
        self.assertNotIn("http://", text)
        self.assertNotIn(".svc.cluster.local", text)


class BackstageCaTrustTest(unittest.TestCase):
    """Backstage's own outbound HTTPS calls (Keycloak OIDC discovery, and
    the Gitea integration once core-portal's app-config.yaml points at
    https://digiorg.local/gitea) must trust the digiorg-local CA via a
    mounted copy of the public certificate and NODE_EXTRA_CA_CERTS -- never
    NODE_TLS_REJECT_UNAUTHORIZED=0, which disables certificate verification
    for every outbound HTTPS call the process makes, not just the intended
    one."""

    @classmethod
    def setUpClass(cls):
        cls.doc = yaml.safe_load(_read(BACKSTAGE_DEPLOYMENT))
        cls.container = cls.doc["spec"]["template"]["spec"]["containers"][0]

    def test_node_tls_reject_unauthorized_is_not_set(self):
        env_names = {e["name"] for e in self.container["env"]}
        self.assertNotIn("NODE_TLS_REJECT_UNAUTHORIZED", env_names)

    def test_node_extra_ca_certs_points_at_a_mounted_file(self):
        env = {e["name"]: e for e in self.container["env"]}
        self.assertIn("NODE_EXTRA_CA_CERTS", env)
        ca_path = env["NODE_EXTRA_CA_CERTS"]["value"]
        mount_paths = {vm["mountPath"] for vm in self.container["volumeMounts"]}
        self.assertTrue(
            any(ca_path == p or ca_path.startswith(p.rstrip("/") + "/") for p in mount_paths),
            f"NODE_EXTRA_CA_CERTS={ca_path!r} is not under any volumeMount path {mount_paths!r}",
        )

    def test_ca_volume_sources_a_secret_not_a_literal(self):
        volumes = self.doc["spec"]["template"]["spec"]["volumes"]
        ca_volumes = [v for v in volumes if "secret" in v]
        self.assertGreater(len(ca_volumes), 0, "expected at least one Secret-backed volume for the CA")


class CaCopyToNamespacesTest(SetupTextFixture):
    """The bootstrap script must copy only the public CA certificate (never
    cert-manager's private key) from cert-manager/digiorg-local-ca-secret
    into every namespace running a client that needs to verify the
    digiorg.local ingress's certificate: crossplane-system (provider-http's
    ProviderConfig), backstage, and gitea (the Actions runner)."""

    def test_copy_function_is_defined_exactly_once(self):
        self.assertEqual(self.text.count("def copy_digiorg_local_ca_to_namespace ["), 1)

    def test_copy_function_never_reads_the_ca_private_key(self):
        body = _func_body(self.text, "copy_digiorg_local_ca_to_namespace")
        self.assertNotIn("tls.key", body)
        self.assertIn("ca.crt", body)

    def test_ca_copies_follow_the_bootstrap_call_graph(self):
        main_up = _func_body(self.text, '"main up"')
        for namespace in ("crossplane-system", "backstage"):
            self.assertIn(
                f'copy_digiorg_local_ca_to_namespace "{namespace}"',
                main_up,
                f"main up must copy the CA into the {namespace} namespace",
            )
        self.assertNotIn(
            'copy_digiorg_local_ca_to_namespace "gitea"',
            main_up,
            "the Gitea CA copy belongs in the gated-sync preamble, not main up",
        )

        gated_sync = _func_body(self.text, "sync_gated_apps_for_local_dev")
        copy_gitea_idx = gated_sync.index(
            'copy_digiorg_local_ca_to_namespace "gitea"'
        )
        gated_loop_idx = gated_sync.index("for app in $gated_apps {")
        self.assertLess(
            copy_gitea_idx,
            gated_loop_idx,
            "the Gitea namespace CA copy must run before the gated Application loop",
        )

        deploy_root_app = _func_body(self.text, "deploy_root_app")
        self.assertIn(
            "sync_gated_apps_for_local_dev",
            deploy_root_app,
            "deploy_root_app must invoke the gated sync that stages Gitea's CA",
        )
        self.assertLess(
            main_up.index("deploy_root_app"),
            main_up.index("configure_gitea"),
            "main up must finish deploy_root_app's gated sync before configuring Gitea",
        )

    def test_gitea_ca_copy_precedes_conditional_gitea_and_runner_restarts(self):
        def indented_block(source, header):
            lines = source.splitlines(keepends=True)
            for start_line, line in enumerate(lines):
                if line.strip() == header:
                    break
            else:
                self.fail(f"missing control-flow header: {header}")

            indent = len(lines[start_line]) - len(lines[start_line].lstrip())
            for end_line in range(start_line + 1, len(lines)):
                line = lines[end_line]
                if line.strip() == "}" and len(line) - len(line.lstrip()) == indent:
                    start = sum(len(item) for item in lines[:start_line])
                    end = sum(len(item) for item in lines[: end_line + 1])
                    return source[start:end], start, end
            self.fail(f"unterminated control-flow block: {header}")

        gated_sync = _func_body(self.text, "sync_gated_apps_for_local_dev")
        copy_gitea_idx = gated_sync.index(
            'let gitea_ca_changed = (copy_digiorg_local_ca_to_namespace "gitea")'
        )
        _, gated_loop_start, gated_loop_end = indented_block(
            gated_sync, "for app in $gated_apps {"
        )
        convergence_idx = gated_sync.index("wait_for_ingress_local_ca_convergence")
        changed_gate, changed_gate_start, _ = indented_block(
            gated_sync, "if $gitea_ca_changed {"
        )
        self.assertLess(copy_gitea_idx, gated_loop_start)
        self.assertLessEqual(
            gated_loop_end,
            convergence_idx,
            "ingress CA convergence must wait for the full gated Application loop",
        )
        self.assertLess(
            convergence_idx,
            changed_gate_start,
            "restart decisions must wait for ingress to publish the copied CA",
        )

        gitea_existed_gate, _, _ = indented_block(
            changed_gate, "if $gitea_existed_before_ca_copy {"
        )
        runner_token_gate, _, _ = indented_block(
            changed_gate, "if $runner_token_exists {"
        )
        gitea_restart_call = 'restart_oidc_deployment "gitea" "gitea" "120s"'
        runner_restart_call = (
            'restart_oidc_deployment_if_present "gitea" "gitea-actions-runner" "120s"'
        )
        self.assertEqual(gated_sync.count(gitea_restart_call), 1)
        self.assertEqual(gated_sync.count(runner_restart_call), 1)
        self.assertIn(gitea_restart_call, gitea_existed_gate)
        self.assertIn(runner_restart_call, runner_token_gate)
        self.assertIn(gitea_restart_call, changed_gate)
        self.assertIn(runner_restart_call, changed_gate)


class NoInsecureCurlTest(SetupTextFixture):
    """Every curl call this script makes against the digiorg.local ingress
    for a credential-bearing Gitea Admin API operation must verify the
    server certificate explicitly (--cacert), never skip verification
    (-k/--insecure)."""

    def test_no_curl_dash_k_against_digiorg_local(self):
        for line in self.text.splitlines():
            if "digiorg.local" in line and "curl" in line:
                self.assertNotRegex(
                    line, r"-[a-zA-Z]*k\b(?!eychain)",
                    f"insecure curl flag found: {line!r}",
                )

    def test_gitea_admin_api_calls_use_explicit_cacert(self):
        body = _func_body(self.text, "configure_app_config_repo")
        self.assertIn("--cacert", body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
