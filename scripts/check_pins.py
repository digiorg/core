#!/usr/bin/env python3
"""Pin-policy checker for the DigiOrg Core Platform (Issue #275).

Rejects floating / non-reproducible references so a clean bootstrap installs the
same versions every time:

  * container ``image:`` refs must be pinned to an immutable ``@sha256:`` digest
    (Tier-2 "record a human tag plus an immutable digest"), unless the exact ref
    is listed in ``scripts/pin-policy-allowlist.yaml`` with a rationale (e.g.
    locally built, kind-loaded images with ``pullPolicy: Never``);
  * Argo CD Helm ``chart:`` sources must pin an exact SemVer — no ``latest``,
    branch, empty, range (``^`` ``~`` ``>=`` ``*``) or ``x`` wildcard;
  * git sources for repositories other than first-party ``digiorg/core`` must
    pin an immutable 40-hex commit (first-party ``main`` is the GitOps norm and
    is allowed).

Pure standard library + PyYAML; no cluster or network access. Run directly for
CI (exit non-zero on any violation) or import ``scan_repo`` / ``scan_text``::

    python3 scripts/check_pins.py            # scan the repo, print violations
    python3 scripts/check_pins.py --list     # also print every checked ref
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass

try:
    import yaml
except ImportError:  # pragma: no cover - environment guard
    sys.stderr.write("PyYAML is required: pip install pyyaml\n")
    raise

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ALLOWLIST_PATH = os.path.join(SCRIPT_DIR, "pin-policy-allowlist.yaml")

# Directories scanned for manifests (repo-relative).
SCAN_DIRS = ("apps", "platform/base")

# First-party repositories: GitOps self-references may track ``main``.
FIRST_PARTY_REPOS = ("https://github.com/digiorg/core.git", "https://github.com/digiorg/core")

DIGEST_RE = re.compile(r"@sha256:[0-9a-f]{64}$")
EXACT_SEMVER_RE = re.compile(r"^v?\d+\.\d+\.\d+([-.][0-9A-Za-z.-]+)?$")
COMMIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
RANGE_CHARS_RE = re.compile(r"[\^~*]|>=|<=|>|<|\bx\b|\.x")
# A scalar that looks like an OCI image reference (has a repo path + tag/digest)
# and is not a Helm template expression.
IMAGE_LIKE_RE = re.compile(r"^[a-z0-9]([a-z0-9._/-]*[a-z0-9])?(:[\w][\w.-]*)?(@sha256:[0-9a-f]{64})?$")


@dataclass(frozen=True)
class Violation:
    file: str
    line: int
    kind: str  # "image" | "chart" | "git"
    ref: str
    message: str


def load_allowlist(path: str = ALLOWLIST_PATH) -> set[str]:
    if not os.path.exists(path):
        return set()
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return {entry["ref"] for entry in (data.get("images") or []) if entry.get("ref")}


def _line_of(text: str, needle: str, start: int = 0) -> int:
    idx = text.find(needle, start)
    if idx < 0:
        return 1
    return text.count("\n", 0, idx) + 1


def _is_image_ref(value) -> bool:
    if not isinstance(value, str) or not value:
        return False
    if "{{" in value or "$(" in value or " " in value:
        return False
    # Must have a tag or digest, and at least one dot/slash/colon distinguishing
    # it from an arbitrary word.
    if ":" not in value and "@" not in value:
        return False
    return bool(IMAGE_LIKE_RE.match(value))


def _check_image(value: str, allow: set[str]) -> str | None:
    """Return a violation message for an image ref, or None if it is compliant."""
    if value in allow:
        return None
    if not DIGEST_RE.search(value):
        tag = value.split("@")[0].split(":")[-1] if (":" in value or "@" in value) else ""
        if tag in ("latest", "main", "master", "stable", "edge", "") and ":" in value:
            return f"floating tag ':{tag}' — pin an immutable @sha256 digest"
        return "image is not pinned to an immutable @sha256 digest"
    return None


def _check_chart_rev(rev) -> str | None:
    if rev is None or rev == "":
        return "chart targetRevision is missing/empty — pin an exact SemVer"
    rev = str(rev)
    if rev in ("latest", "main", "master", "stable"):
        return f"chart targetRevision '{rev}' is floating — pin an exact SemVer"
    if RANGE_CHARS_RE.search(rev):
        return f"chart targetRevision '{rev}' is a range/wildcard — pin an exact SemVer"
    if not EXACT_SEMVER_RE.match(rev):
        return f"chart targetRevision '{rev}' is not an exact SemVer"
    return None


def _normalize_repo(url: str) -> str:
    return url.rstrip("/").removesuffix(".git")


def _check_git_rev(repo_url: str, rev) -> str | None:
    rev = "" if rev is None else str(rev)
    first_party = {_normalize_repo(fp) for fp in FIRST_PARTY_REPOS}
    if _normalize_repo(repo_url) in first_party:
        return None  # first-party GitOps self-reference may track a branch
    if COMMIT_SHA_RE.match(rev):
        return None
    # Allow an exact tag pin for third-party repos too.
    if EXACT_SEMVER_RE.match(rev):
        return None
    return f"git source '{repo_url}' targetRevision '{rev}' is not an immutable 40-hex commit"


def _walk_images(node, out: list):
    """Yield every value under an 'image' key anywhere in the structure."""
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "image" and _is_image_ref(v):
                out.append(v)
            _walk_images(v, out)
    elif isinstance(node, list):
        for item in node:
            _walk_images(item, out)


def _walk_sources(node, out: list):
    """Yield Argo CD source mappings (dicts that have a repoURL)."""
    if isinstance(node, dict):
        if "repoURL" in node and isinstance(node.get("repoURL"), str):
            out.append(node)
        for v in node.values():
            _walk_sources(v, out)
    elif isinstance(node, list):
        for item in node:
            _walk_sources(item, out)


def scan_text(path: str, text: str, allow: set[str] | None = None) -> list[Violation]:
    """Scan a single manifest body. ``allow`` overrides the on-disk allowlist."""
    allow = load_allowlist() if allow is None else allow
    violations: list[Violation] = []
    try:
        docs = list(yaml.safe_load_all(text))
    except yaml.YAMLError as exc:  # surface unparseable YAML as a violation
        return [Violation(path, 1, "yaml", path, f"YAML parse error: {exc}")]

    for doc in docs:
        if doc is None:
            continue
        images: list = []
        _walk_images(doc, images)
        for ref in images:
            msg = _check_image(ref, allow)
            if msg:
                violations.append(Violation(path, _line_of(text, ref), "image", ref, msg))

        sources: list = []
        _walk_sources(doc, sources)
        for src in sources:
            repo = src["repoURL"]
            rev = src.get("targetRevision")
            if "chart" in src:  # Helm chart source
                msg = _check_chart_rev(rev)
                if msg:
                    violations.append(
                        Violation(path, _line_of(text, str(rev)) if rev else 1, "chart", f"{src['chart']}@{rev}", msg)
                    )
            else:  # git source
                msg = _check_git_rev(repo, rev)
                if msg:
                    violations.append(Violation(path, _line_of(text, repo), "git", f"{repo}@{rev}", msg))
    return violations


def scan_repo(root: str) -> list[Violation]:
    allow = load_allowlist()
    violations: list[Violation] = []
    for scan_dir in SCAN_DIRS:
        base = os.path.join(root, scan_dir)
        for dirpath, _dirs, files in os.walk(base):
            for name in sorted(files):
                if not name.endswith((".yaml", ".yml")):
                    continue
                full = os.path.join(dirpath, name)
                rel = os.path.relpath(full, root)
                with open(full, encoding="utf-8") as fh:
                    text = fh.read()
                violations.extend(
                    Violation(rel, v.line, v.kind, v.ref, v.message)
                    for v in scan_text(rel, text, allow=allow)
                )
    return violations


def main(argv: list[str]) -> int:
    root = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
    violations = scan_repo(root)
    if violations:
        print(f"✗ {len(violations)} pin-policy violation(s):\n")
        for v in violations:
            print(f"  {v.file}:{v.line}  [{v.kind}]  {v.ref}\n      {v.message}")
        print("\nPin an immutable digest/version, or add a documented exception to")
        print("  scripts/pin-policy-allowlist.yaml")
        return 1
    print("✓ source pin policy: direct image/chart/git references are pinned")
    print("  (rendered Helm defaults are checked separately by render_platform_charts.py)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
