#!/usr/bin/env python3
"""Issue #285 runtime blockers for the declarative Harbor bootstrap Request
(`crossplane/bootstrap/harbor-robot-request.yaml`).

Blocker A -- secret preservation: Harbor's `GET /robots/{id}` (and, after
this fix, `GET /robots` LIST) responses never carry the robot's `secret`
field (confirmed against goharbor/harbor v2.15.1's
`src/server/v2.0/handler/robot.go` -- only `CreateRobot`'s response
populates it). provider-http v1.0.14's `KeyInjection.MissingFieldStrategy`
defaults to `"delete"`, so every OBSERVE reconcile after the first would
wipe the already-injected `secret`/`basicAuth` keys from
`crossplane-harbor-credentials`. This locks `missingFieldStrategy:
preserve` on every keyMapping fed by a field the OBSERVE response omits.

Blocker B -- crash-safe identity: the previous OBSERVE mapping resolved the
robot purely via a numeric ID cached from a prior CREATE response
(`.response.body.id`). If this Request's `.status` is ever lost (deleted
and recreated after Harbor already accepted the CREATE POST), that cached
ID is gone. Unlike the per-app project-level robot (core-catalog's
pipeline Composition), Harbor's `GET /robots` LIST endpoint defaults to
system-level scope (`ProjectID=0`) whenever the `Level` query keyword is
omitted (confirmed via `ListRobot` in the same handler file) -- exactly
this bootstrap robot's own level, with no numeric project ID required.
This locks a LIST-based OBSERVE mapping (`GET /robots?q=Name=~...`) plus
CUSTOM `isRemovedCheck`/`expectedResponseCheck` that independently
rediscover the robot from its declared name/level/permissions alone,
never relying on any previously cached response.

Blocker C -- provider response boundary: provider-http v1.0.14 stores HTTP
response bodies as strings. Its
`internal/service/request/requestgen/request_generator.go`
`GenerateRequestContext` calls
`internal/json/util.go` `ConvertJSONStringsToMaps`, but `IsJSONString`
attempts to unmarshal into `map[string]interface{}` and therefore leaves a
top-level JSON array as a string. These tests must exercise that actual
string boundary, while retaining explicit decoded-array compatibility.

Run:
    python3 platform/tests/test_harbor_bootstrap_crash_safety.py
"""

import json
import os
import subprocess
import sys
import unittest

try:
    import yaml
except ImportError:  # pragma: no cover - environment guard
    sys.stderr.write("PyYAML is required: pip install pyyaml\n")
    raise

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
HARBOR_REQUEST = os.path.join(REPO_ROOT, "crossplane", "bootstrap", "harbor-robot-request.yaml")
PROVIDER_HTTP_PACKAGE = os.path.join(
    REPO_ROOT, "crossplane", "providers", "packages", "provider-http.yaml"
)


def _load():
    with open(HARBOR_REQUEST, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _run_jq(logic, doc):
    proc = subprocess.run(
        ["jq", "-c", logic], input=json.dumps(doc), capture_output=True, text=True, timeout=10
    )
    if proc.returncode != 0:
        raise AssertionError("jq failed for logic=%r: %s" % (logic, proc.stderr))
    return json.loads(proc.stdout)


class SecretPreservationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = _load()
        cls.injections = cls.doc["spec"]["forProvider"]["secretInjectionConfigs"]

    def test_every_key_mapping_preserves_on_missing_field(self):
        self.assertEqual(len(self.injections), 1)
        for mapping in self.injections[0]["keyMappings"]:
            self.assertEqual(
                mapping.get("missingFieldStrategy"),
                "preserve",
                "%s must preserve (not delete) on a response that omits it" % mapping["secretKey"],
            )

    def test_name_secret_basicauth_keys_still_present(self):
        keys = {m["secretKey"] for m in self.injections[0]["keyMappings"]}
        self.assertEqual(keys, {"name", "secret", "basicAuth"})


class ListBasedObserveTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = _load()
        cls.forProvider = cls.doc["spec"]["forProvider"]
        cls.mappings = cls.forProvider["mappings"]

    def test_observe_mapping_is_a_list_query_not_id_based(self):
        observe = next(m for m in self.mappings if m.get("action") == "OBSERVE")
        self.assertEqual(observe["method"], "GET")
        self.assertNotIn(".response.body.id", observe["url"])
        self.assertIn("/robots", observe["url"])
        self.assertIn("crossplane-system", observe["url"])

    def test_expected_response_check_is_custom(self):
        self.assertEqual(self.forProvider["expectedResponseCheck"]["type"], "CUSTOM")

    def test_is_removed_check_is_custom(self):
        self.assertEqual(self.forProvider["isRemovedCheck"]["type"], "CUSTOM")


class ProviderResponseBoundaryContractTest(unittest.TestCase):
    """Lock the provider source/dependency contract behind the string fixtures.

    provider-http v1.0.14 source:
    * `internal/service/request/requestgen/request_generator.go`
      `GenerateRequestContext`
    * `internal/json/util.go` `ConvertJSONStringsToMaps` and `IsJSONString`

    The tag's `go.mod` pins `github.com/itchyny/gojq v0.12.17`. That exact
    dependency registers `"fromjson": argFunc0(funcFromJSON)` in `func.go`,
    whose `funcFromJSON` decodes arbitrary JSON values, and its
    `cli/test.yaml` "fromjson function" case explicitly decodes `["foo"]`.
    Thus `try ... fromjson ... catch` is supported by the provider's
    embedded jq engine and is the compatibility boundary tested below.
    """

    def test_provider_http_remains_pinned_to_verified_boundary(self):
        with open(PROVIDER_HTTP_PACKAGE, encoding="utf-8") as fh:
            package = yaml.safe_load(fh)
        self.assertEqual(
            package["spec"]["package"],
            "xpkg.upbound.io/crossplane-contrib/provider-http:v1.0.14",
        )

    def test_both_custom_checks_normalize_string_bodies_with_fail_closed_fromjson(self):
        for check_name in ("isRemovedCheck", "expectedResponseCheck"):
            with self.subTest(check=check_name):
                logic = _load()["spec"]["forProvider"][check_name]["logic"]
                self.assertIn("fromjson", logic)
                self.assertIn("try", logic)
                self.assertIn("catch", logic)


class CrashSafeIdentityLogicTest(unittest.TestCase):
    """Exercise the actual CUSTOM jq logic against synthetic LIST responses --
    the concrete, provider-verified meaning of "rediscovers the robot from
    its declared identity alone"."""

    @classmethod
    def setUpClass(cls):
        cls.forProvider = _load()["spec"]["forProvider"]

    def _expected_logic(self):
        return self.forProvider["expectedResponseCheck"]["logic"]

    def _removed_logic(self):
        return self.forProvider["isRemovedCheck"]["logic"]

    def _matching_robot(self):
        return {
            "name": "robot$crossplane-system",
            "level": "system",
            "permissions": [
                {"kind": "system", "namespace": "/", "access": [{"resource": "project", "action": "create"}]},
                {
                    "kind": "project",
                    "namespace": "*",
                    "access": [
                        {"resource": "robot", "action": "create"},
                        {"resource": "robot", "action": "read"},
                        {"resource": "artifact", "action": "read"},
                    ],
                },
            ],
        }

    def test_provider_string_matching_array_is_synced_and_not_removed(self):
        doc = {
            "response": {
                "statusCode": 200,
                "body": json.dumps([self._matching_robot()]),
            }
        }
        self.assertTrue(_run_jq(self._expected_logic(), doc))
        self.assertFalse(_run_jq(self._removed_logic(), doc))

    def test_provider_string_empty_array_is_removed_and_not_synced(self):
        doc = {"response": {"statusCode": 200, "body": "[]"}}
        self.assertFalse(_run_jq(self._expected_logic(), doc))
        self.assertTrue(_run_jq(self._removed_logic(), doc))

    def test_predecoded_matching_array_remains_backward_compatible(self):
        doc = {"response": {"statusCode": 200, "body": [self._matching_robot()]}}
        self.assertTrue(_run_jq(self._expected_logic(), doc))
        self.assertFalse(_run_jq(self._removed_logic(), doc))

    def test_predecoded_empty_array_remains_backward_compatible(self):
        doc = {"response": {"statusCode": 200, "body": []}}
        self.assertFalse(_run_jq(self._expected_logic(), doc))
        self.assertTrue(_run_jq(self._removed_logic(), doc))

    def test_a_non_200_is_uncertain_not_removed_and_not_synced(self):
        # A non-200/error response proves nothing about whether the robot
        # actually exists in Harbor -- it must never be treated as a
        # confirmed absence (that would re-trigger CREATE against a name
        # Harbor's own uniqueness constraint may already hold). Only a
        # genuine HTTP 200 list that omits the robot counts as "removed".
        doc = {"response": {"statusCode": 500, "body": "error"}}
        self.assertFalse(_run_jq(self._expected_logic(), doc))
        self.assertFalse(_run_jq(self._removed_logic(), doc))

    def test_a_401_is_uncertain_not_removed_and_not_synced(self):
        doc = {"response": {"statusCode": 401, "body": "unauthorized"}}
        self.assertFalse(_run_jq(self._expected_logic(), doc))
        self.assertFalse(_run_jq(self._removed_logic(), doc))

    def test_a_malformed_200_body_is_uncertain_not_removed_and_not_synced(self):
        # Harbor is contractually a JSON array on 200, but a malformed/non-
        # array body must not be trusted as a confirmed absence either.
        doc = {"response": {"statusCode": 200, "body": {"unexpected": "object"}}}
        self.assertFalse(_run_jq(self._expected_logic(), doc))
        self.assertFalse(_run_jq(self._removed_logic(), doc))

    def test_a_malformed_json_string_fails_closed(self):
        doc = {"response": {"statusCode": 200, "body": "[}"}}
        self.assertFalse(_run_jq(self._expected_logic(), doc))
        self.assertFalse(_run_jq(self._removed_logic(), doc))

    def test_a_json_object_string_fails_closed(self):
        doc = {"response": {"statusCode": 200, "body": '{"unexpected":"object"}'}}
        self.assertFalse(_run_jq(self._expected_logic(), doc))
        self.assertFalse(_run_jq(self._removed_logic(), doc))

    def test_json_null_string_fails_closed(self):
        doc = {"response": {"statusCode": 200, "body": "null"}}
        self.assertFalse(_run_jq(self._expected_logic(), doc))
        self.assertFalse(_run_jq(self._removed_logic(), doc))

    def test_json_scalar_strings_fail_closed(self):
        for body in ('"unexpected"', "true", "42"):
            with self.subTest(body=body):
                doc = {"response": {"statusCode": 200, "body": body}}
                self.assertFalse(_run_jq(self._expected_logic(), doc))
                self.assertFalse(_run_jq(self._removed_logic(), doc))

    def test_predecoded_null_fails_closed(self):
        doc = {"response": {"statusCode": 200, "body": None}}
        self.assertFalse(_run_jq(self._expected_logic(), doc))
        self.assertFalse(_run_jq(self._removed_logic(), doc))

    def test_wrong_permissions_is_not_synced_but_not_removed(self):
        # The name already exists in Harbor (matched by name/level) -- must
        # not be reported as removed, or a mismatched/drifted identity would
        # re-trigger CREATE against a name Harbor's own uniqueness
        # constraint already holds.
        robot = self._matching_robot()
        robot["permissions"][1]["access"] = [{"resource": "robot", "action": "delete"}]
        doc = {"response": {"statusCode": 200, "body": json.dumps([robot])}}
        self.assertFalse(_run_jq(self._expected_logic(), doc))
        self.assertFalse(_run_jq(self._removed_logic(), doc))

    def test_extra_permission_action_is_not_synced_but_not_removed(self):
        # Issue #285 review finding: expectedResponseCheck must enforce exact
        # least-privilege permissions -- an extra action alongside the
        # required ones (e.g. Harbor RBAC drifting to also grant
        # robot:delete) must never be silently trusted as synced. The name
        # still matches, so this must not be reported as removed either.
        robot = self._matching_robot()
        robot["permissions"][1]["access"].append({"resource": "robot", "action": "delete"})
        doc = {"response": {"statusCode": 200, "body": json.dumps([robot])}}
        self.assertFalse(_run_jq(self._expected_logic(), doc))
        self.assertFalse(_run_jq(self._removed_logic(), doc))

    def test_object_valued_permissions_containing_canonical_entries_is_not_synced(self):
        # jq's `.[]` (and the guarded `.[]?`) iterates an OBJECT's VALUES just
        # like an array's elements. A `permissions` field that is a JSON
        # *object* (not an array) whose values happen to be the two
        # canonical permission entries must never be accepted as the
        # canonical array shape -- Harbor's own contract is `permissions`:
        # array of objects. This is PR#287's finding: malformed,
        # object-valued `permissions` must fail closed, not synced.
        robot = self._matching_robot()
        perms = robot["permissions"]
        robot["permissions"] = {"first": perms[0], "second": perms[1]}
        doc = {"response": {"statusCode": 200, "body": json.dumps([robot])}}
        self.assertFalse(_run_jq(self._expected_logic(), doc))
        self.assertFalse(_run_jq(self._removed_logic(), doc))

    def test_object_valued_access_containing_canonical_entries_is_not_synced(self):
        # Same shape confusion one level deeper: a permission entry's
        # `access` field must be an array of objects, never a JSON object
        # whose values happen to be the canonical access entries.
        robot = self._matching_robot()
        access = robot["permissions"][1]["access"]
        robot["permissions"][1]["access"] = {str(i): a for i, a in enumerate(access)}
        doc = {"response": {"statusCode": 200, "body": json.dumps([robot])}}
        self.assertFalse(_run_jq(self._expected_logic(), doc))
        self.assertFalse(_run_jq(self._removed_logic(), doc))

    def test_a_differently_named_robot_in_the_list_does_not_match(self):
        robot = self._matching_robot()
        robot["name"] = "robot$some-other-robot"
        doc = {"response": {"statusCode": 200, "body": json.dumps([robot])}}
        self.assertFalse(_run_jq(self._expected_logic(), doc))
        self.assertFalse(
            _run_jq(self._removed_logic(), doc),
            "Issue #285 slice M: under the exact server-side Name query, "
            "isRemovedCheck no longer scans item identity at all -- ANY "
            "nonempty response is fail-closed 'not removed', even one whose "
            "sole item doesn't match this robot. (This exact shape can't "
            "happen against the real exact query -- Harbor would never "
            "return an unrelated name for it -- but the check must not "
            "trust it either way if it somehow did.)",
        )


class ExactServerSideQueryTest(unittest.TestCase):
    """Issue #285 review finding (round 4, final blocker): fuzzy
    `q=Name=~crossplane-system` matches by SUBSTRING against every robot's
    stored name Harbor-wide -- the `page_size=100` + truncation-guard
    machinery in `ExactIdentityAndPaginationTest` (now superseded, see
    below) existed purely to bound the damage of that fuzziness, never to
    fix its root cause.

    Verified against pinned goharbor/harbor v2.15.1 source fetched into
    /tmp/harbor-v2151:
    * `builder.go` `parsePattern`: a bare value (no leading `~`) takes the
      `default:` branch -- `escapeValue`, an EXACT DB predicate. `~value` is
      the fuzzy/substring branch (`parseFuzzyMatchValue`).
    * `controller.go` `populate()`: `config.RobotPrefix(ctx)` is prepended
      to the robot's `Name` only on the OUTGOING `Robot` struct returned to
      the caller -- the DB row's stored `model.Robot.Name` (what `ListRobot`
      -> `BuildQuery` -> the exact predicate above actually filters) never
      carries it. `Create()` only prefixes `name` with `r.ProjectName` for
      `LEVELPROJECT`; a system-level robot's stored name is the bare
      declared name, `"crossplane-system"`.
    * `robot.go` `ListRobot`: whenever the `Level` query keyword is absent
      (as here), it defaults `Level=system, ProjectID=0` itself -- so this
      OBSERVE was already implicitly system-scoped even under the old fuzzy
      query.

    Given Harbor's `unique_robot UNIQUE(name, project_id)` constraint, an
    exact query for `Name=crossplane-system` at the implicit `ProjectID=0`
    can therefore only ever return the one canonical row or `[]` --
    Harbor-wide decoys and the `page_size` boundary both become
    structurally irrelevant to this OBSERVE, and the checks collapse to a
    plain array-length test with no per-item scan or pagination guard
    needed. GitHub issue 20679 (a prior exact-match failure) was improper
    URL-encoding of `+` in project-level robot names -- irrelevant here:
    `crossplane-system` contains no character `url.QueryUnescape` treats
    specially.
    """

    @classmethod
    def setUpClass(cls):
        cls.doc = _load()
        cls.forProvider = cls.doc["spec"]["forProvider"]

    def _observe_url(self):
        return next(
            m for m in self.forProvider["mappings"] if m.get("action") == "OBSERVE"
        )["url"]

    def _removed_logic(self):
        return self.forProvider["isRemovedCheck"]["logic"]

    def _expected_logic(self):
        return self.forProvider["expectedResponseCheck"]["logic"]

    def _matching_robot(self, name="robot$crossplane-system"):
        return {
            "name": name,
            "level": "system",
            "permissions": [
                {"kind": "system", "namespace": "/", "access": [{"resource": "project", "action": "create"}]},
                {
                    "kind": "project",
                    "namespace": "*",
                    "access": [
                        {"resource": "robot", "action": "create"},
                        {"resource": "robot", "action": "read"},
                        {"resource": "artifact", "action": "read"},
                    ],
                },
            ],
        }

    def test_observe_uses_exact_match_not_fuzzy(self):
        url = self._observe_url()
        self.assertIn("q=Name=crossplane-system", url)
        self.assertNotIn("Name=~crossplane-system", url)

    def test_a_hypothetical_hundred_global_decoys_still_converge_to_removed(self):
        """The server-side exact filter means Harbor never puts decoys in
        this response body no matter how many robots exist system-wide --
        so the only response shape a genuine absence can produce is an
        empty array, and that alone must be enough to report removed."""
        doc = {"response": {"statusCode": 200, "body": []}}
        self.assertTrue(_run_jq(self._removed_logic(), doc))
        self.assertFalse(_run_jq(self._expected_logic(), doc))

    def test_canonical_prefixed_response_converges_synced(self):
        doc = {"response": {"statusCode": 200, "body": [self._matching_robot()]}}
        self.assertTrue(_run_jq(self._expected_logic(), doc))
        self.assertFalse(_run_jq(self._removed_logic(), doc))

    def test_an_unprefixed_response_fails_closed(self):
        doc = {
            "response": {
                "statusCode": 200,
                "body": json.dumps([self._matching_robot("crossplane-system")]),
            }
        }
        self.assertFalse(_run_jq(self._expected_logic(), doc))
        self.assertFalse(_run_jq(self._removed_logic(), doc))

    def test_an_impossible_multi_item_response_fails_closed(self):
        """Harbor's own uniqueness constraint makes >1 item impossible for a
        genuine exact-name query at ProjectID=0 -- but the checks must not
        trust the shape as either state if it somehow occurred."""
        body = [self._matching_robot(), self._matching_robot("crossplane-system")]
        doc = {"response": {"statusCode": 200, "body": json.dumps(body)}}
        self.assertFalse(_run_jq(self._expected_logic(), doc))
        self.assertFalse(_run_jq(self._removed_logic(), doc))

    def test_a_hundred_item_response_fails_closed_not_removed(self):
        """Same impossible-shape guarantee at a larger, decoy-shaped size --
        this is exactly the "100 global decoys" case, but expressed as the
        (contract-violating) response body itself rather than as unrelated
        system-wide rows the exact filter would already have excluded."""
        body = [self._matching_robot(f"robot$unrelated-{i}") for i in range(100)]
        doc = {"response": {"statusCode": 200, "body": body}}
        self.assertFalse(_run_jq(self._expected_logic(), doc))
        self.assertFalse(_run_jq(self._removed_logic(), doc))

    def test_a_single_non_matching_item_fails_closed(self):
        doc = {
            "response": {
                "statusCode": 200,
                "body": json.dumps([self._matching_robot("robot$unrelated")]),
            }
        }
        self.assertFalse(_run_jq(self._expected_logic(), doc))
        self.assertFalse(_run_jq(self._removed_logic(), doc))

    def test_a_malformed_body_fails_closed(self):
        doc = {"response": {"statusCode": 200, "body": {"unexpected": "object"}}}
        self.assertFalse(_run_jq(self._expected_logic(), doc))
        self.assertFalse(_run_jq(self._removed_logic(), doc))

    def test_a_401_fails_closed(self):
        doc = {"response": {"statusCode": 401, "body": "unauthorized"}}
        self.assertFalse(_run_jq(self._expected_logic(), doc))
        self.assertFalse(_run_jq(self._removed_logic(), doc))

    def test_a_500_fails_closed(self):
        doc = {"response": {"statusCode": 500, "body": "error"}}
        self.assertFalse(_run_jq(self._expected_logic(), doc))
        self.assertFalse(_run_jq(self._removed_logic(), doc))

    def test_is_removed_check_no_longer_depends_on_a_page_size_threshold(self):
        """The truncation guard existed only to bound fuzzy-match pagination
        risk; an exact query has nothing left to bound."""
        logic = self._removed_logic()
        self.assertNotIn("page_size", logic)
        self.assertNotIn("100", logic)

    def test_observe_url_carries_no_leftover_page_size_param(self):
        self.assertNotIn("page_size", self._observe_url())


if __name__ == "__main__":
    unittest.main(verbosity=2)
