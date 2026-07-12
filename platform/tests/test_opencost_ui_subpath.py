#!/usr/bin/env python3
# =============================================================================
# Regression tests for the OpenCost /opencost subpath UI proxy and asset ingress
#
# These tests parse the real platform manifests and assert their *behaviour*
# rather than grepping for a single arbitrary string:
#
#   * ui-proxy.yaml    — the nginx sub_filter rules are extracted per location
#                        block and replayed against sample Parcel and Vite HTML
#                        to prove root-absolute references get rewritten under
#                        /opencost/ for BOTH UI layouts.
#   * opencost-assets-ingress.yaml — the path regex is compiled and matched
#                        against real Parcel (/index.*, /favicon.*) and Vite
#                        (/assets/*) asset URLs, and the rewrite-target capture
#                        is replayed to prove the /opencost/ prefix is stripped
#                        correctly. The expression is also asserted to be
#                        RE2-compatible (no non-capturing (?:...) groups).
#
# Context: GitHub issue #272 (Option B — robust dual Parcel + Vite support).
#
# NOTE: PARCEL_HTML and VITE_HTML below are representative, hand-written
# snapshots of the two build layouts' index.html — they are NOT dynamically
# extracted from the exact OpenCost container image in use. They exercise the
# reference *shapes* the manifests must handle (root-absolute /index.*,
# /favicon.*, /assets/* references); they do not guarantee byte-for-byte parity
# with whatever the pinned image happens to emit.
#
# Runs with the standard library + PyYAML only (no pytest required):
#     python3 platform/tests/test_opencost_ui_subpath.py
# =============================================================================
import os
import re
import unittest

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
UI_PROXY = os.path.join(REPO_ROOT, "platform", "base", "opencost", "ui-proxy.yaml")
ASSETS_INGRESS = os.path.join(
    REPO_ROOT, "platform", "base", "ingress", "opencost-assets-ingress.yaml"
)

# Representative index.html snapshots for the two UI build layouts the proxy
# must support simultaneously (see module NOTE — these are illustrative, not
# extracted from the exact container image).
# Parcel v2 emits root-level chunks (/index.*.js|css, /favicon.*).
PARCEL_HTML = (
    '<!doctype html><html><head>'
    '<link href="/favicon.a1b2c3.png" rel="icon">'
    '<link href="/index.3406705b.css" rel="stylesheet">'
    '</head><body>'
    '<script src="/index.6dd063c6.js"></script>'
    '<script src="/index.runtime.216fe643.js"></script>'
    '</body></html>'
)
# Vite emits hashed assets under /assets/*.
VITE_HTML = (
    '<!doctype html><html><head>'
    '<link rel="stylesheet" href="/assets/index-8f7e0a12.css">'
    '</head><body>'
    '<script type="module" src="/assets/index-4b3c2d1e.js"></script>'
    '</body></html>'
)


def load_yaml_docs(path):
    with open(path, "r", encoding="utf-8") as fh:
        return [d for d in yaml.safe_load_all(fh) if d is not None]


def extract_nginx_conf(path):
    """Return the nginx.conf string from the opencost-ui-proxy ConfigMap."""
    for doc in load_yaml_docs(path):
        if doc.get("kind") == "ConfigMap" and "nginx.conf" in doc.get("data", {}):
            return doc["data"]["nginx.conf"]
    raise AssertionError("nginx.conf ConfigMap not found in %s" % path)


def extract_location_blocks(nginx_conf):
    """Parse `location ... { ... }` blocks into {header: body} by brace matching.

    Keys are the raw location header text (e.g. '= /opencost', '/opencost/').
    """
    blocks = {}
    for m in re.finditer(r"location\s+([^\{]+?)\s*\{", nginx_conf):
        header = m.group(1).strip()
        i = m.end() - 1  # position of the opening brace
        depth = 0
        for j in range(i, len(nginx_conf)):
            c = nginx_conf[j]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    blocks[header] = nginx_conf[i + 1 : j]
                    break
    return blocks


def parse_sub_filters(block_body):
    """Return list of (from, to) literal sub_filter pairs in a location block."""
    pairs = []
    for m in re.finditer(
        r"sub_filter\s+(['\"])(.*?)\1\s+(['\"])(.*?)\3\s*;", block_body
    ):
        pairs.append((m.group(2), m.group(4)))
    return pairs


def apply_sub_filters(html, pairs):
    """Replay nginx sub_filter literal replacements against an HTML string.

    nginx applies every configured filter to the response buffer; replacement
    output is not re-scanned, so a single ordered pass of str.replace is a
    faithful simulation for non-overlapping rules.
    """
    for src, dst in pairs:
        html = html.replace(src, dst)
    return html


class UiProxySubFilterTest(unittest.TestCase):
    """The nginx sub_filter rules must rewrite BOTH Parcel and Vite assets."""

    @classmethod
    def setUpClass(cls):
        cls.conf = extract_nginx_conf(UI_PROXY)
        cls.blocks = extract_location_blocks(cls.conf)

    # Both the bare-path exact match and the trailing-slash prefix serve HTML.
    SERVING_LOCATIONS = ("= /opencost", "/opencost/")

    def test_both_serving_locations_present(self):
        for loc in self.SERVING_LOCATIONS:
            self.assertIn(
                loc, self.blocks, "expected a `location %s` block in nginx.conf" % loc
            )

    def test_parcel_layout_rewritten_in_all_locations(self):
        for loc in self.SERVING_LOCATIONS:
            pairs = parse_sub_filters(self.blocks[loc])
            out = apply_sub_filters(PARCEL_HTML, pairs)
            for ref in (
                '/opencost/index.6dd063c6.js',
                '/opencost/index.runtime.216fe643.js',
                '/opencost/index.3406705b.css',
                '/opencost/favicon.a1b2c3.png',
            ):
                self.assertIn(
                    ref, out, "Parcel ref %s not rewritten in `location %s`" % (ref, loc)
                )
            # No root-absolute Parcel references may survive the rewrite.
            for stale in ('src="/index.', 'href="/index.', 'href="/favicon.'):
                self.assertNotIn(
                    stale,
                    out,
                    "stale root-absolute %r remains in `location %s`" % (stale, loc),
                )

    def test_vite_layout_rewritten_in_all_locations(self):
        for loc in self.SERVING_LOCATIONS:
            pairs = parse_sub_filters(self.blocks[loc])
            out = apply_sub_filters(VITE_HTML, pairs)
            for ref in (
                '/opencost/assets/index-4b3c2d1e.js',
                '/opencost/assets/index-8f7e0a12.css',
            ):
                self.assertIn(
                    ref, out, "Vite ref %s not rewritten in `location %s`" % (ref, loc)
                )
            for stale in ('src="/assets/', 'href="/assets/'):
                self.assertNotIn(
                    stale,
                    out,
                    "stale root-absolute %r remains in `location %s`" % (stale, loc),
                )

    def test_gzip_disabled_for_sub_filter(self):
        # sub_filter only operates on uncompressed HTML, so upstream gzip must
        # stay disabled via Accept-Encoding "" in both serving locations.
        for loc in self.SERVING_LOCATIONS:
            self.assertRegex(
                self.blocks[loc],
                r'proxy_set_header\s+Accept-Encoding\s+"";',
                "Accept-Encoding gzip guard missing in `location %s`" % loc,
            )

    def test_redirect_rewrite_preserved(self):
        # The upstream->public Location rewrite must survive (no port leak).
        for loc in self.SERVING_LOCATIONS:
            self.assertIn(
                "https://digiorg.local/opencost/",
                self.blocks[loc],
                "proxy_redirect target missing in `location %s`" % loc,
            )


class AssetsIngressRegexTest(unittest.TestCase):
    """The asset ingress must route Parcel AND Vite assets, RE2-compatibly."""

    @classmethod
    def setUpClass(cls):
        docs = load_yaml_docs(ASSETS_INGRESS)
        ing = next(d for d in docs if d.get("kind") == "Ingress")
        cls.ingress = ing
        ann = ing["metadata"]["annotations"]
        cls.rewrite_target = ann["nginx.ingress.kubernetes.io/rewrite-target"]
        paths = ing["spec"]["rules"][0]["http"]["paths"]
        # The single asset path rule.
        cls.path = paths[0]["path"]
        # nginx/ingress-nginx anchors the location regex at the start.
        cls.regex = re.compile("^" + cls.path)

    def test_no_non_capturing_groups(self):
        # RE2 as used by ingress-nginx path validation: reject (?:...).
        self.assertNotIn(
            "(?:",
            self.path,
            "non-capturing group (?:...) is not RE2/ingress-safe: %r" % self.path,
        )

    def test_rewrite_target_capture_numbering(self):
        # Every $N referenced by the rewrite-target must exist as a capture group.
        refs = [int(n) for n in re.findall(r"\$(\d+)", self.rewrite_target)]
        self.assertTrue(refs, "rewrite-target must reference a capture group")
        self.assertLessEqual(
            max(refs),
            self.regex.groups,
            "rewrite-target %r references more groups than the path regex defines"
            % self.rewrite_target,
        )

    def _rewritten(self, url):
        """Return the upstream path after applying rewrite-target, or None."""
        m = self.regex.match(url)
        if not m:
            return None
        out = self.rewrite_target
        for i in range(1, self.regex.groups + 1):
            out = out.replace("$%d" % i, m.group(i) or "")
        return out

    def test_parcel_assets_routed_and_stripped(self):
        cases = {
            "/opencost/index.6dd063c6.js": "/index.6dd063c6.js",
            "/opencost/index.runtime.216fe643.js": "/index.runtime.216fe643.js",
            "/opencost/index.3406705b.css": "/index.3406705b.css",
            "/opencost/favicon.ico": "/favicon.ico",
        }
        for url, expected in cases.items():
            self.assertEqual(
                self._rewritten(url), expected, "Parcel asset %s mis-routed" % url
            )

    def test_vite_assets_routed_and_stripped(self):
        cases = {
            "/opencost/assets/index-4b3c2d1e.js": "/assets/index-4b3c2d1e.js",
            "/opencost/assets/index-8f7e0a12.css": "/assets/index-8f7e0a12.css",
        }
        for url, expected in cases.items():
            self.assertEqual(
                self._rewritten(url), expected, "Vite asset %s mis-routed" % url
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
