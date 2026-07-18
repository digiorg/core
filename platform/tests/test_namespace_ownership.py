#!/usr/bin/env python3
"""Every Namespace must be owned by exactly one Argo CD Application (Issue #279).

``platform/base/namespaces`` is a dedicated wave -1 Application
(``apps/platform/namespaces.yaml``) that pre-creates every platform namespace so
concurrent wave-2+ Applications never race on ``CreateNamespace=true``. If a
component's own Kustomize base *also* ships a ``Namespace`` resource for a name
already declared there, Argo CD's resource-tracking annotation flips between the
two owning Applications on every reconciliation, leaving at least one
permanently ``OutOfSync`` — confirmed for NATS/``messaging`` in issue #279.

This is a *general* regression contract: it walks every ``platform/base/*``
Kustomization and fails if any of them declares a ``Namespace`` object whose
name is also pre-created by ``platform/base/namespaces``, regardless of which
component introduces it.

Pure python3 + PyYAML, no cluster access::

    python3 platform/tests/test_namespace_ownership.py
"""

import os
import sys
import unittest

try:
    import yaml
except ImportError:  # pragma: no cover
    sys.stderr.write("PyYAML is required: pip install pyyaml\n")
    raise

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
BASE_DIR = os.path.join(REPO_ROOT, "platform", "base")
CENTRAL_NAMESPACES_FILE = os.path.join(BASE_DIR, "namespaces", "namespaces.yaml")


def _docs(path):
    with open(path, encoding="utf-8") as fh:
        return [d for d in yaml.safe_load_all(fh) if d]


def _central_namespace_names():
    names = set()
    for doc in _docs(CENTRAL_NAMESPACES_FILE):
        if doc.get("kind") == "Namespace":
            names.add(doc["metadata"]["name"])
    return names


def _kustomization_namespace_resources(kustomization_path, visited=None):
    """Recursively find Namespace resources reachable from a Kustomization."""
    visited = set() if visited is None else visited
    real_path = os.path.realpath(kustomization_path)
    if real_path in visited:
        return []
    visited.add(real_path)

    comp_dir = os.path.dirname(kustomization_path)
    with open(kustomization_path, encoding="utf-8") as fh:
        kustomization = yaml.safe_load(fh) or {}
    names = []
    for resource in kustomization.get("resources", []):
        resource_path = os.path.normpath(os.path.join(comp_dir, resource))
        if os.path.isdir(resource_path):
            nested = next(
                (
                    os.path.join(resource_path, candidate)
                    for candidate in ("kustomization.yaml", "kustomization.yml", "Kustomization")
                    if os.path.isfile(os.path.join(resource_path, candidate))
                ),
                None,
            )
            if nested:
                names.extend(_kustomization_namespace_resources(nested, visited))
            continue
        if not os.path.isfile(resource_path) or not resource_path.endswith((".yaml", ".yml")):
            continue
        if os.path.basename(resource_path).lower().startswith("kustomization"):
            names.extend(_kustomization_namespace_resources(resource_path, visited))
            continue
        for doc in _docs(resource_path):
            if doc and doc.get("kind") == "Namespace":
                names.append((doc["metadata"]["name"], os.path.relpath(resource_path, REPO_ROOT)))
    return names


def _all_component_kustomizations():
    paths = []
    central_dir = os.path.realpath(os.path.join(BASE_DIR, "namespaces"))
    for root, dirs, files in os.walk(BASE_DIR):
        dirs[:] = [d for d in dirs if os.path.realpath(os.path.join(root, d)) != central_dir]
        for candidate in ("kustomization.yaml", "kustomization.yml", "Kustomization"):
            if candidate in files and os.path.realpath(root) != central_dir:
                paths.append(os.path.join(root, candidate))
    return sorted(paths)


class NamespaceOwnershipTest(unittest.TestCase):
    def test_central_namespaces_file_is_readable(self):
        self.assertTrue(os.path.isfile(CENTRAL_NAMESPACES_FILE))
        self.assertGreater(len(_central_namespace_names()), 0)

    def test_no_component_duplicates_a_centrally_owned_namespace(self):
        central = _central_namespace_names()
        violations = []
        for kustomization_path in _all_component_kustomizations():
            for name, rel_path in _kustomization_namespace_resources(kustomization_path):
                if name in central:
                    violations.append(f"{rel_path} duplicates centrally-owned Namespace '{name}'")
        self.assertEqual(
            violations, [],
            "Components must not ship a Namespace already owned by "
            "platform/base/namespaces (causes permanent OutOfSync ping-pong):\n  "
            + "\n  ".join(violations),
        )

    def test_nats_messaging_namespace_is_centrally_owned_only(self):
        # Explicit regression for the diagnosed issue #279 case.
        nats_kustomization = os.path.join(BASE_DIR, "nats", "kustomization.yaml")
        names = [n for n, _ in _kustomization_namespace_resources(nats_kustomization)]
        self.assertNotIn("messaging", names,
                          "nats must not manage the 'messaging' Namespace owned by the "
                          "central namespaces Application")


if __name__ == "__main__":
    unittest.main(verbosity=2)
