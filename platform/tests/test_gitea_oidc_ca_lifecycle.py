#!/usr/bin/env python3
"""Declarative Gitea OIDC CA lifecycle contract (Issue #297)."""

from pathlib import Path
import re
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[2]
VALUES_PATH = ROOT / "platform/base/gitea/values.yaml"
SETUP_PATH = ROOT / "scripts/local-setup.nu"
VOLUME_NAME = "digiorg-local-ca"
CERT_FILENAME = "digiorg-local-ca.crt"


def _function(source, signature):
    """Return one top-level Nushell function without scanning unrelated code."""
    match = re.search(
        rf"(?ms)^def {re.escape(signature)} \{{\n.*?^\}}\s*$",
        source,
    )
    if match is None:
        raise AssertionError(f"could not find Nushell function: def {signature} {{")
    return match.group(0)


def _control_block(source, header_pattern):
    """Return an indentation-delimited control-flow block matching its header."""
    header = re.search(rf"(?m)^(?P<indent>[ \t]*){header_pattern}[ \t]*$", source)
    if header is None:
        raise AssertionError(f"could not find control-flow header: {header_pattern}")

    indent = len(header.group("indent"))
    lines = source[header.start() :].splitlines(keepends=True)
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() and len(line) - len(line.lstrip()) <= indent:
            return "".join(lines[:index])
    return "".join(lines)


class GiteaDeclarativeCaContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.values = yaml.safe_load(VALUES_PATH.read_text(encoding="utf-8"))

    def _named_entries(self, key, name):
        entries = self.values.get(key, [])
        self.assertIsInstance(entries, list, f"{key} must use the pinned chart's list contract")
        return [entry for entry in entries if entry.get("name") == name]

    def test_public_ca_secret_volume_selects_exactly_one_public_item(self):
        volumes = self._named_entries("extraVolumes", VOLUME_NAME)
        self.assertEqual(len(volumes), 1, f"expected one {VOLUME_NAME} extraVolumes entry")

        secret = volumes[0].get("secret")
        self.assertIsInstance(secret, dict)
        self.assertEqual(secret.get("secretName"), VOLUME_NAME)
        self.assertIsNot(secret.get("optional", False), True, "the Gitea CA Secret must be required")
        self.assertEqual(
            secret.get("items"),
            [{"key": "ca.crt", "path": CERT_FILENAME}],
            "the volume must project only the public CA certificate, never a private key",
        )

    def test_main_and_init_containers_mount_the_same_read_only_directory(self):
        mount_groups = []
        for key in ("extraContainerVolumeMounts", "extraInitVolumeMounts"):
            mounts = self._named_entries(key, VOLUME_NAME)
            self.assertEqual(len(mounts), 1, f"expected one {VOLUME_NAME} entry in {key}")
            mount = mounts[0]
            self.assertTrue(mount.get("readOnly"), f"{key} CA mount must be read-only")
            self.assertNotIn("subPath", mount, f"{key} must mount the projected directory")
            mount_groups.append(mount)

        self.assertEqual(mount_groups[0], mount_groups[1], "main and init CA mounts must match")
        mount_path = mount_groups[0].get("mountPath")
        self.assertIsInstance(mount_path, str)
        self.assertTrue(Path(mount_path).is_absolute())
        self.assertNotEqual(
            Path(mount_path),
            Path("/etc/ssl/certs"),
            "the custom CA volume must not replace the image's system certificate directory",
        )

    def test_ssl_cert_file_resolves_to_the_projected_public_certificate(self):
        mounts = self._named_entries("extraContainerVolumeMounts", VOLUME_NAME)
        self.assertEqual(len(mounts), 1)
        expected_cert_file = str(Path(mounts[0].get("mountPath", "")) / CERT_FILENAME)

        deployment = self.values.get("deployment", {})
        self.assertIsInstance(deployment, dict)
        env = deployment.get("env", [])
        self.assertIsInstance(env, list, "deployment.env must use the pinned chart's list contract")
        ssl_cert_file = [item for item in env if item.get("name") == "SSL_CERT_FILE"]
        self.assertEqual(len(ssl_cert_file), 1, "deployment.env must define SSL_CERT_FILE exactly once")
        self.assertEqual(ssl_cert_file[0], {"name": "SSL_CERT_FILE", "value": expected_cert_file})

    def test_values_do_not_disable_tls_verification(self):
        forbidden_names = {
            "git_ssl_no_verify",
            "gitea_tls_skip_verify",
            "insecure_skip_verify",
            "node_tls_reject_unauthorized",
            "oidc_insecure_skip_verify",
            "oidc_skip_tls_verify",
            "skip_tls_verify",
            "ssl_no_verify",
            "tls_skip_verify",
        }
        found = []

        def visit(value, path=()):
            if isinstance(value, dict):
                for key, child in value.items():
                    normalized = re.sub(r"[^a-z0-9]+", "_", str(key).lower()).strip("_")
                    if normalized in forbidden_names:
                        found.append(".".join((*path, str(key))))
                    visit(child, (*path, str(key)))
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    visit(child, (*path, str(index)))
            elif isinstance(value, str):
                normalized = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
                if normalized in forbidden_names:
                    found.append(".".join(path))

        visit(self.values)
        self.assertEqual(found, [], f"TLS verification bypasses are forbidden: {found}")


class GiteaSetupCaLifecycleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = SETUP_PATH.read_text(encoding="utf-8")
        cls.main_up = _function(cls.source, '"main up" []')
        cls.configure_gitea = _function(cls.source, "configure_gitea []")
        cls.gated_sync = _function(cls.source, "sync_gated_apps_for_local_dev []")
        cls.gated_apps = _function(cls.source, "gated_apps_for_local_dev []")

    def _convergence_helpers(self):
        helpers = []
        for match in re.finditer(
            r"(?ms)^def (?P<name>[a-zA-Z_][a-zA-Z0-9_]*)[^\n{]* \{\n.*?^\}\s*$",
            self.source,
        ):
            body = match.group(0)
            if "digiorg-local-ca-secret" in body and "digiorg-local-tls" in body:
                helpers.append((match.group("name"), body))
        return helpers

    def _gitea_copy_contract(self, require_snapshot=True):
        copy = re.search(
            r'let (?P<changed>[a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*'
            r'\(copy_digiorg_local_ca_to_namespace\s+"gitea"\)',
            self.gated_sync,
        )
        self.assertIsNotNone(copy, "the Gitea CA copy must retain its change result")
        if copy is None:
            return None

        before_copy = self.gated_sync[: copy.start()]
        matching_lookup = None
        for candidate in re.finditer(
            r'(?ms)let (?P<result>[a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*\(do(?: -i)?\s*\{'
            r'.*?kubectl\b(?P<command>.*?)\}\s*\|\s*complete\)',
            before_copy,
        ):
            command = candidate.group("command")
            if re.search(r'\bget\s+deployment\s+gitea\b', command) and re.search(
                r'(?:-n|--namespace)\s+gitea\b', command
            ):
                matching_lookup = candidate
        if require_snapshot:
            self.assertIsNotNone(
                matching_lookup,
                "observe whether deployment gitea/gitea exists before copying its CA",
            )
        if matching_lookup is None:
            return copy.group("changed"), None

        result = matching_lookup.group("result")
        command = matching_lookup.group("command")
        self.assertIn("--ignore-not-found", command)
        self.assertRegex(command, r'(?:-o|--output)\s+name\b')

        after_lookup = before_copy[matching_lookup.end() :]
        failure = re.search(
            rf'(?m)^\s*if \${re.escape(result)}\.exit_code\s*!=\s*0\s*\{{',
            after_lookup,
        )
        self.assertIsNotNone(
            failure, "deployment lookup errors must fail closed before the CA copy"
        )
        if failure is None:
            return None
        failure_block = _control_block(
            after_lookup, rf'if \${re.escape(result)}\.exit_code\s*!=\s*0\s*\{{'
        )
        self.assertIn("error make", failure_block)

        existed = re.search(
            rf'let (?P<exists>[a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*'
            rf'\([^\n]*\${re.escape(result)}\.stdout[^\n]*'
            r'(?:is-not-empty|not\s+\([^\n]*is-empty)[^\n]*\)',
            after_lookup,
        )
        self.assertIsNotNone(
            existed, "capture whether Gitea existed before the CA was copied"
        )
        if existed is None:
            return None
        self.assertLess(failure.start(), existed.start())
        return copy.group("changed"), existed.group("exists")

    def test_gitea_ca_secret_exists_before_the_gated_sync_loop_can_reach_gitea(self):
        apps_match = re.search(r"(?ms)^\s*\[(.*?)^\s*\]", self.gated_apps)
        self.assertIsNotNone(apps_match)
        gated_apps = re.findall(r'"([^"]+)"', apps_match.group(1))
        self.assertIn("gitea", gated_apps)

        replication = self.gated_sync.find('copy_digiorg_local_ca_to_namespace "gitea"')
        sync_loop = self.gated_sync.find("for app in $gated_apps {")
        self.assertGreaterEqual(replication, 0, "replicate the public CA Secret into namespace gitea")
        self.assertGreaterEqual(sync_loop, 0)
        self.assertLess(replication, sync_loop, "Gitea must receive its CA Secret before gated sync")

    def test_configure_gitea_does_not_mutate_the_running_container_trust_store(self):
        forbidden = {
            "kubectl cp": r"(?m)\bkubectl\b[^\n]*\bcp\b",
            "update-ca-certificates": r"\bupdate-ca-certificates\b",
            "mutable CA destination": re.escape(
                "/usr/local/share/ca-certificates/digiorg-local-ca.crt"
            ),
        }
        for description, pattern in forbidden.items():
            with self.subTest(description=description):
                match = re.search(pattern, self.configure_gitea)
                self.assertIsNone(match, f"configure_gitea still contains {description}")

    def test_gitea_existence_is_observed_fail_closed_before_ca_copy(self):
        self._gitea_copy_contract()

    def test_changed_ca_restarts_are_after_gated_sync_and_ingress_convergence(self):
        contract = self._gitea_copy_contract(require_snapshot=False)
        if contract is None:
            return
        changed_signal, existed_signal = contract

        loop_block = _control_block(self.gated_sync, r"for app in \$gated_apps \{")
        loop_start = self.gated_sync.index(loop_block)
        after_loop = self.gated_sync[loop_start + len(loop_block) :]
        changed = _control_block(
            after_loop, rf"if \${re.escape(changed_signal)}\s*\{{"
        )
        self.assertIsNotNone(
            existed_signal, "capture whether Gitea existed before the CA was copied"
        )
        if existed_signal is None:
            return

        helpers = self._convergence_helpers()
        self.assertEqual(
            len(helpers),
            1,
            "define one helper that waits for ingress to publish the current public CA",
        )
        if len(helpers) != 1:
            return
        helper_name, _ = helpers[0]
        convergence_call = re.search(
            rf"(?m)^\s*{re.escape(helper_name)}(?:\s|$)", after_loop
        )
        self.assertIsNotNone(convergence_call, "call the convergence helper after gated sync")
        changed_start = after_loop.index(changed)
        if convergence_call is not None:
            self.assertLess(convergence_call.start(), changed_start)

        gitea_gate = _control_block(
            changed, rf"if \${re.escape(existed_signal)}\s*\{{"
        )
        gitea_restart = re.compile(
            r'restart_oidc_deployment(?:_if_present)?\s+"gitea"\s+"gitea"\s+"120s"'
        )
        self.assertEqual(len(gitea_restart.findall(gitea_gate)), 1)
        self.assertEqual(
            len(gitea_restart.findall(changed)),
            1,
            "only a Gitea process observed before CA copy may be restarted",
        )

    def test_runner_restart_is_token_gated_and_token_lookup_failure_is_fatal(self):
        contract = self._gitea_copy_contract(require_snapshot=False)
        if contract is None:
            return
        changed_signal, _ = contract
        loop_block = _control_block(self.gated_sync, r"for app in \$gated_apps \{")
        loop_start = self.gated_sync.index(loop_block)
        after_loop = self.gated_sync[loop_start + len(loop_block) :]
        changed = _control_block(
            after_loop, rf"if \${re.escape(changed_signal)}\s*\{{"
        )

        lookup = changed.index("get secret gitea-actions-runner-token")
        failure_gate = changed.index("if $runner_token_lookup.exit_code != 0 {")
        exists = changed.index("let runner_token_exists =")
        runner_gate = changed.index("if $runner_token_exists {")
        self.assertLess(lookup, failure_gate)
        self.assertLess(failure_gate, exists)
        self.assertLess(exists, runner_gate)
        self.assertIn("--ignore-not-found", changed[lookup:failure_gate])

        lookup_failure = _control_block(
            changed, r"if \$runner_token_lookup\.exit_code != 0 \{"
        )
        self.assertIn(
            "error make",
            lookup_failure,
            "API, RBAC, and transport failures during token lookup must remain fatal",
        )
        self.assertRegex(
            changed[failure_gate:runner_gate],
            r"runner_token_lookup\.stdout.*(?:is-not-empty|not .*is-empty)",
        )

        runner_restart = re.compile(
            r'restart_oidc_deployment(?:_if_present)?\s+"gitea"\s+'
            r'"gitea-actions-runner"\s+"120s"'
        )
        token_present = _control_block(changed, r"if \$runner_token_exists \{")
        self.assertEqual(len(runner_restart.findall(token_present)), 1)
        self.assertEqual(
            len(runner_restart.findall(changed)),
            1,
            "a missing token on fresh bootstrap must have no runner-restart path",
        )

    def test_ingress_ca_convergence_helper_is_safe_and_bounded(self):
        candidates = self._convergence_helpers()
        self.assertEqual(
            len(candidates),
            1,
            "define one convergence helper comparing the cert-manager public CA with ingress TLS CA",
        )
        if len(candidates) != 1:
            return
        _, helper = candidates[0]

        self.assertRegex(
            helper,
            r"(?s)get secret digiorg-local-ca-secret\b.*?-n cert-manager\b.*?"
            r"jsonpath='\{\.data\.ca\\\.crt\}'",
        )
        self.assertRegex(
            helper,
            r"(?s)get secret digiorg-local-tls\b.*?-n ingress-nginx\b.*?"
            r"jsonpath='\{\.data\.ca\\\.crt\}'",
        )
        self.assertGreaterEqual(helper.count(".exit_code"), 2, "both reads must check command errors")
        public_read_end = helper.index("digiorg-local-tls")
        self.assertRegex(
            helper[:public_read_end],
            r"(?s)(?:stdout.*?str trim.*?is-empty.*?error make|"
            r"exit_code\s*!=\s*0\s+or\s+\([^\n]*stdout[^\n]*is-empty)",
        )

        bounded_for = re.search(r"for\s+\w+\s+in\s+1\.\.[0-9]+\s*\{", helper)
        bounded_loop = "loop {" in helper and re.search(
            r"(?:attempt|elapsed|deadline|timeout).*(?:>|>=)", helper
        )
        self.assertTrue(bounded_for or bounded_loop, "convergence polling must have a finite bound")
        equality = re.search(r"(?m)^\s*if\s+[^\n]*==[^\n]*\{\s*$", helper)
        self.assertIsNotNone(
            equality, "compare the public and ingress CA values exactly"
        )
        if equality is not None:
            equality_block = _control_block(helper, r"if\s+[^\n]*==[^\n]*\{")
            self.assertRegex(equality_block, r"(?m)^\s*return\s*$")
            outside_equality = helper.replace(equality_block, "", 1)
            self.assertNotRegex(
                outside_equality,
                r"(?m)^\s*return\b",
                "the convergence helper may return only when the CA values are equal",
            )
        self.assertIn("error make", helper, "timeout must fail closed")

        certificate_variables = set(
            re.findall(
                r"let\s+([a-zA-Z_][a-zA-Z0-9_]*(?:ca|cert)[a-zA-Z0-9_]*)\s*=",
                helper,
                re.IGNORECASE,
            )
        )
        for line in helper.splitlines():
            if re.search(r"\bprint\b", line):
                for variable in certificate_variables:
                    self.assertNotIn(
                        f"${variable}", line, "convergence must not print certificate content"
                    )

    def test_fresh_bootstrap_without_gitea_or_token_has_no_restart_path(self):
        contract = self._gitea_copy_contract(require_snapshot=False)
        if contract is None:
            return
        changed_signal, existed_signal = contract
        loop_block = _control_block(self.gated_sync, r"for app in \$gated_apps \{")
        loop_start = self.gated_sync.index(loop_block)
        after_loop = self.gated_sync[loop_start + len(loop_block) :]
        changed = _control_block(
            after_loop, rf"if \${re.escape(changed_signal)}\s*\{{"
        )
        self.assertIsNotNone(
            existed_signal, "capture whether Gitea existed before the CA was copied"
        )
        if existed_signal is None:
            return

        gitea_gate = _control_block(
            changed, rf"if \${re.escape(existed_signal)}\s*\{{"
        )
        self.assertNotIn("gitea-actions-runner", gitea_gate)
        runner_gate = _control_block(changed, r"if \$runner_token_exists\s*\{")
        self.assertNotIn('restart_oidc_deployment "gitea" "gitea"', runner_gate)

    def test_main_up_has_no_second_gitea_ca_copy_or_change_restart_block(self):
        self.assertNotIn(
            'copy_digiorg_local_ca_to_namespace "gitea"',
            self.main_up,
            "main up must leave the single Gitea CA copy to the gated sync preamble",
        )
        self.assertNotIn("gitea_ca_changed", self.main_up)
        self.assertNotIn("gitea-actions-runner-token", self.main_up)
        self.assertNotIn(
            'restart_oidc_deployment_if_present "gitea"',
            self.main_up,
            "main up must not retain a duplicate CA-change restart block",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
