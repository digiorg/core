# Platform manifest regression tests

Deterministic tests that parse the platform manifests and assert their
**behaviour** (not just the presence of a string), so subpath/proxy regressions
are caught in review rather than in the cluster.

## Requirements

- Python 3
- PyYAML (`pip install pyyaml`)

No cluster, pytest, or network access required.

## Scope of the HTML fixtures

The Parcel and Vite HTML fixtures in `test_opencost_ui_subpath.py` are
representative, hand-written snapshots of the two build layouts — they are
**not** dynamically extracted from the exact OpenCost container image in use.
They assert that the manifests correctly handle the *shapes* of root-absolute
references each layout emits (`/index.*`, `/favicon.*`, `/assets/*`); they do
not guarantee byte-for-byte parity with the pinned image's actual output.

## Running

```sh
python3 platform/tests/test_opencost_ui_subpath.py
```

## Coverage

| Test file | Manifests under test | What it proves |
| --- | --- | --- |
| `test_opencost_ui_subpath.py` | `platform/base/opencost/ui-proxy.yaml`, `platform/base/ingress/opencost-assets-ingress.yaml` | The `/opencost` subpath proxy rewrites root-absolute asset references for **both** the Parcel (`/index.*`, `/favicon.*`) and Vite (`/assets/*`) UI layouts in every serving location, keeps gzip disabled and the redirect rewrite intact, and the asset ingress routes both layouts with an RE2-compatible expression (no non-capturing groups) whose `rewrite-target: /$1` capture strips `/opencost/` correctly. See GitHub issue #272. |
