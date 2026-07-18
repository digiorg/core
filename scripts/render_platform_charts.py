#!/usr/bin/env python3
"""Render every Argo CD Helm source and reject floating rendered images.

This complements check_pins.py: chart defaults are invisible in source manifests.
Exact chart packages may still emit exact tag-only transitive images; those are
reported so digest migration remains visible, while floating tags fail CI.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
IMAGE_RE = re.compile(r"^\s*image:\s*[\"']?([^\"'\s]+)", re.MULTILINE)
FLOATING_RE = re.compile(r"(?i)(?::|^)(latest|main|master|head)$")


def sources(app: dict) -> list[dict]:
    spec = app.get("spec", {})
    return spec.get("sources") or ([spec["source"]] if "source" in spec else [])


def values_files(app: dict, source: dict, tmp: Path) -> list[Path]:
    helm = source.get("helm", {})
    result: list[Path] = []
    inline = helm.get("values")
    if inline:
        path = tmp / f"{app['metadata']['name']}-inline-values.yaml"
        path.write_text(inline, encoding="utf-8")
        result.append(path)
    for value_file in helm.get("valueFiles", []):
        if value_file.startswith("$values/"):
            result.append(ROOT / value_file.removeprefix("$values/"))
        elif not value_file.startswith("$"):
            result.append(ROOT / value_file)
    return result


def image_is_floating(ref: str) -> bool:
    base = ref.split("@", 1)[0]
    last = base.rsplit("/", 1)[-1]
    if ":" not in last:
        return True
    return bool(FLOATING_RE.search(last.rsplit(":", 1)[1]))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="/tmp/platform-helm-renders")
    args = parser.parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    rendered = 0
    floating: list[str] = []
    tag_only: set[str] = set()

    with tempfile.TemporaryDirectory(prefix="platform-chart-values-") as td:
        tmp = Path(td)
        for app_path in sorted((ROOT / "apps/platform").glob("*.yaml")):
            app = yaml.safe_load(app_path.read_text(encoding="utf-8"))
            for source in sources(app):
                if "chart" not in source:
                    continue
                name = app["metadata"]["name"]
                out = output_dir / f"{name}.yaml"
                cmd = [
                    "helm", "template", name, source["chart"],
                    "--repo", source["repoURL"],
                    "--version", str(source["targetRevision"]),
                    "--namespace", app["spec"]["destination"]["namespace"],
                    "--include-crds",
                ]
                for path in values_files(app, source, tmp):
                    if not path.is_file():
                        raise FileNotFoundError(f"{app_path}: values file not found: {path}")
                    cmd.extend(["-f", str(path)])
                proc = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True)
                if proc.returncode:
                    sys.stderr.write(proc.stderr)
                    return proc.returncode
                out.write_text(proc.stdout, encoding="utf-8")
                rendered += 1
                for ref in IMAGE_RE.findall(proc.stdout):
                    # CRD OpenAPI schemas contain `image: description:` prose;
                    # only OCI-like scalar references participate in the policy.
                    if ref.endswith(":"):
                        continue
                    if image_is_floating(ref):
                        floating.append(f"{name}: {ref}")
                    elif "@sha256:" not in ref:
                        tag_only.add(f"{name}: {ref}")

    if tag_only:
        print("Rendered exact tag-only images (remaining digest work):")
        for item in sorted(tag_only):
            print(f"  WARN {item}")
    if floating:
        print("Floating rendered images:", file=sys.stderr)
        for item in floating:
            print(f"  ERROR {item}", file=sys.stderr)
        return 1
    print(f"HELM_RENDER_PASS={rendered}; no floating rendered image tags")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
