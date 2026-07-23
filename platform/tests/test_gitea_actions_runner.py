#!/usr/bin/env python3
"""Gitea Actions runner (Issue #285 blocker #4): no act_runner existed at
all, so no Gitea Actions workflow the platform generates (the CI/CD pipeline
in core-catalog's pipeline.yaml, Issue #285 blockers #5/#6/#7) could ever
actually execute.

Locks:
  * a pinned, functional act_runner Deployment (official gitea/act_runner
    dind-rootless image -- a single container bundling act_runner + a
    rootless dockerd, so CI jobs can build/push images without a separate
    DinD sidecar or `privileged: true`), registered at instance scope;
  * the registration token travels stdin/readback into a dedicated Secret,
    consumed by the runner via a mounted file (GITEA_RUNNER_REGISTRATION_TOKEN_FILE),
    never a literal in argv/env/manifest;
  * resume-stability: the runner's own `.runner` registration state persists
    on a PersistentVolumeClaim (not emptyDir), and this platform explicitly
    re-verifies the runner is online every run, not just once;
  * Argo inventory/wave placement consistent with the rest of the platform;
  * both the act_runner and DinD-capable image, plus the CI job-runtime
    image the generated workflow's `ubuntu-latest` label resolves to, are
    pinned by immutable registry digest -- confirmed 2026-07-22 via
    registry-1.docker.io's `docker-content-digest` response header;
  * the exact Gitea Admin API this platform's actually-pinned Gitea version
    (chart 12.6.0 / appVersion 1.26.1 -- apps/platform/gitea.yaml) exposes:
    `POST /api/v1/admin/actions/runners/registration-token` (confirmed
    against go-gitea/gitea's templates/swagger/v1_json.tmpl at tag v1.26.1;
    the older v1.23 `GET /api/v1/admin/runners/registration-token` no
    longer exists on this chart's pinned appVersion).

Run:
    python3 platform/tests/test_gitea_actions_runner.py
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
APP = os.path.join(REPO_ROOT, "apps", "platform", "gitea-actions-runner.yaml")
RUNNER_DIR = os.path.join(REPO_ROOT, "platform", "base", "gitea-actions-runner")

ACT_RUNNER_IMAGE_DIGEST = (
    "gitea/act_runner:0.6.1-dind-rootless"
    "@sha256:6b8f7c4297c0a5c4c181e4737665d4af69288cdc380e2887105a05a2b78930df"
)
CI_JOB_IMAGE_DIGEST = (
    "catthehacker/ubuntu:act-latest"
    "@sha256:3220992391c1182a0cfe4c64453511772c54f4c39e960d26a5e327960675982e"
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


class RunnerArgoApplicationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(APP, encoding="utf-8") as fh:
            cls.app = yaml.safe_load(fh)

    def test_application_exists_and_targets_the_runner_manifests(self):
        self.assertEqual(self.app["kind"], "Application")
        self.assertEqual(self.app["spec"]["source"]["path"], "platform/base/gitea-actions-runner")

    def test_destination_is_the_gitea_namespace(self):
        self.assertEqual(self.app["spec"]["destination"]["namespace"], "gitea")

    def test_automated_sync_is_enabled(self):
        self.assertIn("automated", self.app["spec"]["syncPolicy"])

    def test_syncs_after_gitea_itself(self):
        gitea_wave = int(
            yaml.safe_load(_read(os.path.join(REPO_ROOT, "apps", "platform", "gitea.yaml")))
            ["metadata"]["annotations"]["argocd.argoproj.io/sync-wave"]
        )
        runner_wave = int(self.app["metadata"]["annotations"]["argocd.argoproj.io/sync-wave"])
        self.assertGreater(runner_wave, gitea_wave)


class RunnerManifestsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.docs = []
        for name in os.listdir(RUNNER_DIR):
            if name.endswith(".yaml") and name != "kustomization.yaml":
                cls.docs.extend(_yaml_docs(os.path.join(RUNNER_DIR, name)))
        cls.by_kind = {}
        for d in cls.docs:
            cls.by_kind.setdefault(d["kind"], []).append(d)

    def test_kustomization_lists_every_manifest(self):
        kustom = yaml.safe_load(_read(os.path.join(RUNNER_DIR, "kustomization.yaml")))
        listed = set(kustom["resources"])
        on_disk = {
            f for f in os.listdir(RUNNER_DIR) if f.endswith(".yaml") and f != "kustomization.yaml"
        }
        self.assertEqual(listed, on_disk)

    def test_deployment_exists_with_single_replica(self):
        self.assertEqual(len(self.by_kind.get("Deployment", [])), 1)
        deploy = self.by_kind["Deployment"][0]
        self.assertEqual(deploy["spec"]["replicas"], 1)

    def _container(self):
        deploy = self.by_kind["Deployment"][0]
        return deploy["spec"]["template"]["spec"]["containers"][0]

    def test_act_runner_image_is_pinned_by_digest(self):
        c = self._container()
        self.assertEqual(c["image"], ACT_RUNNER_IMAGE_DIGEST)

    def test_image_is_not_a_floating_tag(self):
        c = self._container()
        self.assertIn("@sha256:", c["image"])

    def test_ci_job_runtime_image_label_is_pinned_by_digest(self):
        c = self._container()
        env = {e["name"]: e for e in c["env"]}
        self.assertIn("GITEA_RUNNER_LABELS", env)
        self.assertIn(CI_JOB_IMAGE_DIGEST, env["GITEA_RUNNER_LABELS"]["value"])

    def test_instance_url_is_the_trusted_ingress_over_https(self):
        # Issue #285 TLS hardening: the runner's own registration/task-fetch
        # calls to Gitea now go through the trusted digiorg.local ingress
        # (CA: cert-manager/digiorg-local-ca-secret), not the raw in-cluster
        # Service address over plain HTTP.
        c = self._container()
        env = {e["name"]: e for e in c["env"]}
        self.assertEqual(env["GITEA_INSTANCE_URL"]["value"], "https://digiorg.local/gitea")

    def test_runner_trusts_the_ca_via_ssl_cert_file(self):
        c = self._container()
        env = {e["name"]: e for e in c["env"]}
        self.assertIn("SSL_CERT_FILE", env)
        self.assertTrue(env["SSL_CERT_FILE"]["value"].startswith("/"))

    def test_ca_is_mounted_for_the_runner_and_for_the_embedded_dockerd_registry_trust(self):
        # Two mounts of the same CA secret volume: one at the generic path
        # SSL_CERT_FILE points to (the runner's own HTTPS calls), and one at
        # Docker's registry-specific trust convention
        # (/etc/docker/certs.d/<registry-host>/ca.crt) so the embedded
        # rootless dockerd trusts the digiorg.local registry without
        # insecureSkipVerify.
        c = self._container()
        env = {e["name"]: e for e in c["env"]}
        mount_paths = {vm["mountPath"] for vm in c["volumeMounts"]}
        self.assertIn(env["SSL_CERT_FILE"]["value"].rsplit("/", 1)[0], mount_paths)
        self.assertIn("/etc/docker/certs.d/digiorg.local", mount_paths)

    def test_config_file_env_points_at_a_mounted_act_runner_config(self):
        # act_runner v0.6.1's entrypoint (scripts/run.sh) only passes
        # --config when CONFIG_FILE is set; without it there is no way to
        # set container.options (used below to mount the CA into every
        # ephemeral job container).
        c = self._container()
        env = {e["name"]: e for e in c["env"]}
        self.assertIn("CONFIG_FILE", env)
        config_path = env["CONFIG_FILE"]["value"]
        mount_paths = {vm["mountPath"]: vm for vm in c["volumeMounts"]}
        self.assertTrue(any(config_path.startswith(p) for p in mount_paths))

    def test_act_runner_config_disables_insecure_and_trusts_ca_in_job_containers(self):
        configmaps = self.by_kind.get("ConfigMap", [])
        config_docs = [
            cm for cm in configmaps if "config.yaml" in (cm.get("data") or {})
        ]
        self.assertEqual(len(config_docs), 1, "expected exactly one act_runner config ConfigMap")
        config_yaml = yaml.safe_load(config_docs[0]["data"]["config.yaml"])
        self.assertIs(config_yaml["runner"]["insecure"], False)
        container_options = config_yaml["container"]["options"]
        self.assertIn("--volume", container_options)
        self.assertNotIn("insecureSkipVerify", container_options)

    def test_registration_token_is_mounted_as_a_file_not_a_raw_env_value(self):
        c = self._container()
        env_names = {e["name"] for e in c["env"]}
        # Never the direct-value env var form -- only the *_FILE indirection,
        # which the official act_runner entrypoint reads from a mounted Secret.
        self.assertNotIn("GITEA_RUNNER_REGISTRATION_TOKEN", env_names)
        self.assertIn("GITEA_RUNNER_REGISTRATION_TOKEN_FILE", env_names)
        volume_mounts = c["volumeMounts"]
        token_mount = next(
            vm for vm in volume_mounts if vm["mountPath"] in
            {"/run/secrets/gitea-actions-runner"}
        )
        self.assertTrue(token_mount.get("readOnly"))

    def test_registration_token_volume_sources_the_dedicated_secret(self):
        deploy = self.by_kind["Deployment"][0]
        volumes = deploy["spec"]["template"]["spec"]["volumes"]
        token_volume = next(v for v in volumes if "secret" in v)
        self.assertEqual(token_volume["secret"]["secretName"], "gitea-actions-runner-token")

    def test_data_directory_is_a_persistent_volume_not_emptydir(self):
        # act_runner's own `.runner` registration state lives under /data;
        # only a PVC survives pod restarts, which is what makes registration
        # resume-stable (Issue #285 blocker #4: "resume-stable registration
        # token").
        deploy = self.by_kind["Deployment"][0]
        volumes = deploy["spec"]["template"]["spec"]["volumes"]
        data_volume = next(v for v in volumes if v["name"] == "data")
        self.assertIn("persistentVolumeClaim", data_volume)
        self.assertNotIn("emptyDir", data_volume)
        self.assertEqual(len(self.by_kind.get("PersistentVolumeClaim", [])), 1)

    def test_runner_container_is_privileged(self):
        # gitea/act_runner:0.6.1-dind-rootless still needs `privileged: true`
        # in Kubernetes: the rootless dockerd inside it sets up user/mount
        # namespaces (newuidmap/newgidmap, unshare/clone CLONE_NEWUSER) that
        # every non-privileged securityContext combination (capabilities,
        # seccompProfile, allowPrivilegeEscalation alone) fails to permit in
        # this cluster's default PSA/seccomp posture. Confirmed against the
        # upstream gitea/runner official Kubernetes example
        # (examples/kubernetes/rootless-docker.yaml @ gitea.com/gitea/runner
        # main), whose runner container securityContext is exactly
        # `privileged: true` and nothing else.
        c = self._container()
        sc = c["securityContext"]
        self.assertIs(sc.get("privileged"), True)

    def test_no_contradictory_hardening_alongside_privileged(self):
        # `capabilities.drop`, `seccompProfile`, `allowPrivilegeEscalation`
        # and `runAsNonRoot` are meaningless-to-misleading on a
        # `privileged: true` container (a privileged container is granted
        # every capability and an unconfined seccomp profile by the
        # container runtime regardless of what's declared here -- see
        # kubernetes/kubernetes securitycontext validation and containerd's
        # WithPrivileged spec opts). Declaring them anyway falsely implies
        # this container is still confined the way the pre-fix version
        # claimed. The upstream gitea/runner official example sets none of
        # them.
        c = self._container()
        sc = c["securityContext"]
        self.assertNotIn("capabilities", sc)
        self.assertNotIn("seccompProfile", sc)
        self.assertNotIn("allowPrivilegeEscalation", sc)
        self.assertNotIn("runAsNonRoot", sc)

    def test_trust_boundary_is_documented_on_the_deployment(self):
        # Issue #285 blocker #4: a privileged pod is a distinct trust
        # boundary from every per-app namespace this same platform creates
        # for AppClaims (namespaceObj in core-catalog's pipeline.yaml) --
        # those are never privileged. The manifest must say so in-repo, next
        # to the grant, not only in a PR description.
        text = _read(os.path.join(RUNNER_DIR, "deployment.yaml"))
        lowered = text.lower()
        self.assertIn("privileged", lowered)
        self.assertIn("trust boundary", lowered)
        self.assertIn("gitea", lowered)

    def test_no_dangling_kyverno_policy_exception_is_introduced(self):
        # No cluster-wide privileged/PSA-restricting Kyverno ClusterPolicy
        # currently exists in this platform (policies/kyverno/), so a
        # PolicyException resource here would reference a nonexistent
        # policy -- dead configuration. If such a policy is ever added, its
        # exception must be scoped to exactly this Deployment's pods in the
        # `gitea` namespace, never broader; that is enforced by review, not
        # by a speculative resource today.
        policy_dir = os.path.join(REPO_ROOT, "policies", "kyverno")
        exception_names = set()
        for root, _dirs, files in os.walk(policy_dir):
            for name in files:
                if not name.endswith(".yaml"):
                    continue
                for doc in _yaml_docs(os.path.join(root, name)):
                    if doc.get("kind") == "ClusterPolicy":
                        exception_names.add(doc["metadata"]["name"])
        self.assertEqual(self.by_kind.get("PolicyException", []), [])
        for doc in self.docs:
            self.assertNotEqual(doc.get("kind"), "PolicyException")


class RegistrationTokenNeverInRegisterArgvTest(unittest.TestCase):
    """act_runner v0.6.1's own stock entrypoint (gitea.com/gitea/runner
    scripts/run.sh @ that tag) reads GITEA_RUNNER_REGISTRATION_TOKEN_FILE into
    an env var and then passes it as `act_runner register --token <value>` --
    a literal argv value, visible via `ps`/`/proc/<pid>/cmdline` to anything
    with exec access to the container for the life of that subprocess. The
    fix replaces that stock entrypoint (mounted over
    /usr/local/bin/run.sh, which the image's s6-supervised act_runner
    service execs by name) with a custom script that feeds the token to
    act_runner's *interactive* register path on stdin instead -- verified
    against internal/app/cmd/register.go @ v0.6.1: omitting --no-interactive
    and --token drops into registerInteractive, which only prompts (reads
    stdin) for a stage whose value is still empty; pre-filling
    --instance/--name/--labels as flags means the token is the only stdin
    prompt reached.
    """

    @classmethod
    def setUpClass(cls):
        cls.configmap = yaml.safe_load(_read(os.path.join(RUNNER_DIR, "configmap.yaml")))
        cls.deploy_docs = [
            d for d in _yaml_docs(os.path.join(RUNNER_DIR, "deployment.yaml"))
            if d["kind"] == "Deployment"
        ]
        cls.container = cls.deploy_docs[0]["spec"]["template"]["spec"]["containers"][0]

    def _entrypoint_script(self):
        return self.configmap["data"]["run.sh"]

    def test_configmap_carries_a_custom_entrypoint_script(self):
        self.assertIn("run.sh", self.configmap["data"])

    def test_entrypoint_never_passes_token_flag_to_register(self):
        script = self._entrypoint_script()
        self.assertIn("act_runner register", script)
        for line in script.splitlines():
            if "act_runner register" in line or "--token" in line:
                self.assertNotIn("--token", line, f"--token flag found in: {line!r}")

    def test_entrypoint_never_sets_the_raw_token_env_var(self):
        script = self._entrypoint_script()
        self.assertNotIn("GITEA_RUNNER_REGISTRATION_TOKEN=", script)
        self.assertNotIn("GITEA_RUNNER_REGISTRATION_TOKEN}", script)

    def test_entrypoint_never_runs_register_no_interactive(self):
        # --no-interactive forces the token-as-flag path (registerNoInteractive
        # requires --token); the fix depends on the interactive path instead.
        script = self._entrypoint_script()
        self.assertNotIn("--no-interactive", script)

    def test_entrypoint_pipes_the_token_file_into_register_stdin(self):
        script = self._entrypoint_script()
        found = False
        for line in script.splitlines():
            if "act_runner register" in line and "|" in line.split("act_runner register")[0]:
                self.assertIn("GITEA_RUNNER_REGISTRATION_TOKEN_FILE", line)
                found = True
        self.assertTrue(found, "expected a `... | act_runner register ...` pipeline reading the token file")

    def test_entrypoint_preserves_idempotent_registration_state_check(self):
        script = self._entrypoint_script()
        self.assertIn(".runner", script)
        self.assertRegex(script, r"\[\[\s*!\s*-s\s*\"?\$RUNNER_STATE_FILE\"?\s*\]\]")

    def test_entrypoint_still_execs_the_daemon(self):
        script = self._entrypoint_script()
        self.assertIn("exec act_runner daemon", script)

    def test_run_sh_is_mounted_over_the_images_stock_entrypoint(self):
        mounts = [
            vm for vm in self.container["volumeMounts"]
            if vm["mountPath"] == "/usr/local/bin/run.sh"
        ]
        self.assertEqual(len(mounts), 1, "expected exactly one volumeMount overriding run.sh")
        mount = mounts[0]
        self.assertEqual(mount.get("subPath"), "run.sh")
        self.assertTrue(mount.get("readOnly"))

    def test_mounted_run_sh_is_executable(self):
        deploy = self.deploy_docs[0]
        volumes = deploy["spec"]["template"]["spec"]["volumes"]
        config_volume = next(v for v in volumes if v["name"] == "act-runner-config")
        items = config_volume["configMap"]["items"]
        run_sh_item = next(i for i in items if i["key"] == "run.sh")
        self.assertEqual(run_sh_item.get("mode"), 0o755)


class ConfigureGiteaActionsRunnerSetupTest(SetupTextFixture):
    def test_functions_are_defined_exactly_once(self):
        for name in ("configure_gitea_actions_runner", "wait_for_gitea_actions_runner_online"):
            self.assertEqual(self.text.count(f"def {name} ["), 1, f"{name} must be defined once")

    def test_configure_gitea_calls_it_after_backstage_publisher(self):
        body = _func_body(self.text, "configure_gitea")
        self.assertIn("configure_gitea_actions_runner", body)
        self.assertLess(
            body.index("configure_backstage_gitea_publisher"),
            body.index("configure_gitea_actions_runner"),
        )

    def test_uses_the_gitea_1_26_admin_api_not_the_stale_v1_23_path(self):
        body = _func_body(self.text, "configure_gitea_actions_runner")
        self.assertIn("/api/v1/admin/actions/runners/registration-token", body)
        self.assertNotIn("/api/v1/admin/runners/registration-token", body)

    def test_registration_token_never_appears_in_argv_only_stdin(self):
        body = _func_body(self.text, "configure_gitea_actions_runner")
        import re

        for match in re.finditer(r"curl[^\n']*", body):
            self.assertNotIn('-H "Authorization', match.group(0))

    def test_token_generation_is_resume_preserved(self):
        body = _func_body(self.text, "configure_gitea_actions_runner")
        self.assertIn("secret_exists", body)

    def test_online_verification_runs_unconditionally_every_run(self):
        # Unlike token generation (resume-preserved), coming online must be
        # re-verified even when the Secret already existed -- a stale/expired
        # token or a crash-looping runner must still fail this run closed.
        body = _func_body(self.text, "configure_gitea_actions_runner")
        call_index = body.index("wait_for_gitea_actions_runner_online")
        # The call must not be nested inside the `if not $secret_exists {`
        # branch -- i.e. it must appear after that branch's closing brace at
        # the function's own indentation level, not gated by it.
        branch_start = body.index("if not $secret_exists {")
        branch_body_start = body.index("{", branch_start) + 1
        depth = 1
        i = branch_body_start
        while depth > 0 and i < len(body):
            if body[i] == "{":
                depth += 1
            elif body[i] == "}":
                depth -= 1
            i += 1
        branch_end = i
        self.assertGreater(
            call_index,
            branch_end,
            "online verification must run after the token-generation branch closes, unconditionally",
        )

    def test_wait_for_online_fails_closed_on_timeout(self):
        body = _func_body(self.text, "wait_for_gitea_actions_runner_online")
        self.assertIn("error make", body)
        self.assertIn("did not come online", body)

    def test_wait_for_online_accepts_only_the_explicit_online_status(self):
        # Gitea's Admin API only ever emits "status": "online" or "offline"
        # for a runner (verified against services/convert/convert.go's
        # ToActionRunner @ go-gitea/gitea v1.26.1: apiStatus is hardcoded to
        # "offline" unless runner.IsOnline(), never any other string). Any
        # other value -- including this script's own "unknown" fallback for
        # a missing/malformed `status` field -- must NOT be treated as
        # ready; only the literal "online" may satisfy readiness.
        body = _func_body(self.text, "wait_for_gitea_actions_runner_online")
        self.assertIn('$status == "online"', body)
        self.assertNotIn('$status != "offline"', body)

    def test_wait_for_online_does_not_return_early_on_unknown_status(self):
        body = _func_body(self.text, "wait_for_gitea_actions_runner_online")
        # The success path (`return` before the loop's final error make) must
        # be reachable only via the explicit "online" acceptance check, not
        # via a bare `if $status != "offline"` that a missing/garbled field
        # (defaulted to "unknown") would also satisfy.
        return_index = body.index("return")
        preceding = body[:return_index]
        self.assertIn('$status == "online"', preceding)


class CaRotationRunnerRestartOrderingTest(SetupTextFixture):
    """Issue #285 core review: on a resumed `up` run where the digiorg.local
    CA content changed, the previous ordering called configure_gitea (which
    calls configure_gitea_actions_runner -> wait_for_gitea_actions_runner_online)
    BEFORE restarting the runner Deployment. An already-running runner pod's
    cached TLS trust does not pick up a rotated CA without a restart, so
    wait_for_gitea_actions_runner_online would poll a runner that can never
    reconnect to Gitea's now-differently-trusted endpoint -- a deadlock that
    only resolves after wait_for_gitea_actions_runner_online times out and
    the (too-late) restart finally runs. The fix restarts the runner
    immediately once a CA change is detected, before configure_gitea runs."""

    def test_gitea_ca_changed_restart_happens_before_configure_gitea(self):
        body = _func_body(self.text, '"main up"')
        restart_call = 'restart_oidc_deployment_if_present "gitea" "gitea-actions-runner"'
        self.assertIn(restart_call, body)
        self.assertIn("configure_gitea", body)
        restart_idx = body.index(restart_call)
        # Find configure_gitea called as its own statement (not the longer
        # configure_gitea_actions_runner identifier, which only appears
        # inside configure_gitea's own definition elsewhere in the file).
        configure_gitea_idx = body.index("\n    configure_gitea\n")
        self.assertLess(
            restart_idx,
            configure_gitea_idx,
            "the gitea-actions-runner restart on CA change must run before configure_gitea, "
            "otherwise wait_for_gitea_actions_runner_online deadlocks against the stale CA",
        )

    def test_restart_is_still_gated_on_the_ca_actually_changing(self):
        body = _func_body(self.text, '"main up"')
        restart_call = 'restart_oidc_deployment_if_present "gitea" "gitea-actions-runner"'
        gate_idx = body.index("if $gitea_ca_changed {")
        restart_idx = body.index(restart_call)
        self.assertLess(
            gate_idx, restart_idx,
            "the restart must remain conditional on gitea_ca_changed, not unconditional",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
