"""Issue #301 Harbor delegation contract and resume-safe permission upgrade.

The AppClaim project robot needs repository push/pull. Harbor v2.15.1 rejects
robot creation unless those exact permissions are a subset of the creator
robot's permissions. The bootstrap Request must therefore grant the system
robot only the two delegated repository actions and converge an existing robot
through PUT /robots/{id}, without touching its credential Secret.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[2]
REQUEST_PATH = ROOT / "crossplane/bootstrap/harbor-robot-request.yaml"


def norm_permissions(perms):
    return {
        (p["kind"], p["namespace"], a["resource"], a["action"])
        for p in perms
        for a in p["access"]
    }


def jq(expression: str, context: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["jq", "-c", expression],
        input=json.dumps(context),
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )


class HarborRobotDelegationUpgradeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = yaml.safe_load(REQUEST_PATH.read_text(encoding="utf-8"))
        cls.provider = cls.doc["spec"]["forProvider"]
        cls.desired = json.loads(cls.provider["payload"]["body"])
        cls.expected_permissions = {
            ("system", "/", "project", "create"),
            ("project", "*", "robot", "create"),
            ("project", "*", "robot", "read"),
            ("project", "*", "artifact", "read"),
            ("project", "*", "repository", "push"),
            ("project", "*", "repository", "pull"),
        }

    @classmethod
    def target_permissions(cls):
        return [
            {"kind": "system", "namespace": "/", "access": [
                {"resource": "project", "action": "create"},
            ]},
            {"kind": "project", "namespace": "*", "access": [
                {"resource": "robot", "action": "create"},
                {"resource": "robot", "action": "read"},
                {"resource": "artifact", "action": "read"},
                {"resource": "repository", "action": "push"},
                {"resource": "repository", "action": "pull"},
            ]},
        ]

    def mapping(self, action: str):
        matches = [m for m in self.provider["mappings"] if m.get("action") == action]
        self.assertEqual(len(matches), 1, f"expected exactly one {action} mapping")
        return matches[0]

    def robot(self, *, permissions=None, robot_id: object = 7, name="robot$crossplane-system"):
        return {
            "id": robot_id,
            "name": name,
            "description": "existing description",
            "duration": -1,
            "level": "system",
            "disable": False,
            "permissions": permissions if permissions is not None else self.target_permissions(),
        }

    def context(self, robots):
        return {
            "payload": self.provider["payload"],
            # provider-http v1.0.14 leaves top-level JSON arrays as strings.
            "response": {"statusCode": 200, "body": json.dumps(robots)},
        }

    def test_creator_permissions_are_exactly_required_plus_delegated_repository_actions(self):
        self.assertEqual(norm_permissions(self.desired["permissions"]), self.expected_permissions)
        self.assertEqual(self.desired["permissions"], self.target_permissions())
        self.assertNotIn(("project", "*", "repository", "delete"), self.expected_permissions)
        self.assertNotIn(("project", "*", "repository", "update"), self.expected_permissions)

    def test_initial_create_path_can_only_submit_exact_declared_payload(self):
        create = self.mapping("CREATE")
        uncertain = {
            "payload": self.provider["payload"],
            "response": {"statusCode": 500, "body": "upstream unavailable"},
        }
        url = jq(create["url"], uncertain)
        body = jq(create["body"], uncertain)
        self.assertEqual(url.returncode, 0, url.stderr)
        self.assertEqual(json.loads(url.stdout), "https://digiorg.local/api/v2.0/robots")
        self.assertEqual(body.returncode, 0, body.stderr)
        rendered = json.loads(json.loads(body.stdout))
        self.assertEqual(rendered, self.desired)
        self.assertEqual(rendered["permissions"], self.target_permissions())
        self.assertNotIn("secret", rendered)

    def test_expected_response_accepts_only_the_new_exact_permission_contract(self):
        logic = self.provider["expectedResponseCheck"]["logic"]
        exact = jq(logic, self.context([self.robot()]))
        self.assertEqual(exact.returncode, 0, exact.stderr)
        self.assertEqual(json.loads(exact.stdout), True)

        effect_drift = self.robot()
        effect_drift["permissions"][1]["access"][3]["effect"] = "allow"
        for drift in (
            self.robot(permissions=self.target_permissions()[:-1]),
            self.robot(permissions=self.target_permissions() + [{
                "kind": "project", "namespace": "*",
                "access": [{"resource": "repository", "action": "delete"}],
            }]),
            effect_drift,
        ):
            result = jq(logic, self.context([drift]))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), False)

    def test_update_mapping_converges_one_observed_robot_without_secret_rotation(self):
        update = self.mapping("UPDATE")
        self.assertEqual(update["method"], "PUT")
        old_permissions = [
            {"kind": "system", "namespace": "/", "access": [
                {"resource": "project", "action": "create"},
            ]},
            {"kind": "project", "namespace": "*", "access": [
                {"resource": "robot", "action": "create"},
                {"resource": "robot", "action": "read"},
                {"resource": "artifact", "action": "read"},
            ]},
        ]
        current = self.robot(permissions=old_permissions)
        context = self.context([current])

        url = jq(update["url"], context)
        body = jq(update["body"], context)
        self.assertEqual(url.returncode, 0, url.stderr)
        self.assertEqual(json.loads(url.stdout), "https://digiorg.local/api/v2.0/robots/7")
        self.assertEqual(body.returncode, 0, body.stderr)
        rendered = json.loads(body.stdout)
        self.assertEqual(rendered, {
            "name": current["name"],
            "description": current["description"],
            "duration": current["duration"],
            "level": current["level"],
            "disable": current["disable"],
            "permissions": self.target_permissions(),
        })
        self.assertNotIn("secret", rendered)

    def _provider_v114_render_source(self, update, current, cached):
        """Model GenerateValidRequestDetails' current-then-cache fallback."""
        for source, context in (("current", current), ("cache", cached)):
            url = jq(update["url"], context)
            body = jq(update["body"], context)
            if url.returncode or body.returncode:
                continue
            rendered_url = json.loads(url.stdout)
            rendered_body = json.loads(body.stdout)
            if rendered_url and "null" not in repr((rendered_url, rendered_body)):
                return source, rendered_url, rendered_body
        return None

    def test_noncanonical_current_response_uses_safe_sentinel_not_stale_cache(self):
        update = self.mapping("UPDATE")
        cached = self.context([self.robot()])
        unsafe_current = [
            self.context([]),
            self.context([self.robot(), self.robot(robot_id=8)]),
            self.context([self.robot(robot_id="not-a-number")]),
            self.context([self.robot(name="robot$wrong-name")]),
            {"payload": self.provider["payload"], "response": {"statusCode": 200, "body": "not-json"}},
            {"payload": self.provider["payload"], "response": {
                "statusCode": 500, "body": json.dumps([self.robot()])}},
        ]
        for current in unsafe_current:
            with self.subTest(context=current["response"]):
                rendered = self._provider_v114_render_source(update, current, cached)
                if rendered is None:
                    self.fail("provider rendering returned no safe current response")
                source, url, body = rendered
                self.assertEqual(source, "current", "must never fall back to stale cached identity")
                self.assertEqual(
                    url,
                    "https://digiorg.local/api/v2.0/robots/provider-http-refused-noncanonical-observe",
                )
                self.assertEqual(body, {})

    def test_every_credential_key_is_preserved_when_update_response_has_no_secret(self):
        configs = self.provider["secretInjectionConfigs"]
        self.assertEqual(len(configs), 1)
        mappings = configs[0]["keyMappings"]
        self.assertEqual({m["secretKey"] for m in mappings}, {"name", "secret", "basicAuth"})
        self.assertTrue(all(m["missingFieldStrategy"] == "preserve" for m in mappings))


if __name__ == "__main__":
    unittest.main(verbosity=2)
