#!/usr/bin/env python3
# =============================================================================
# Regression tests for the OpenCost /opencost subpath UI — Issue #272 Option B
#
# Architecture under test (Option B — build a subpath-aware OpenCost UI image):
#
#   * The stock OpenCost UI v1.113.0 used BrowserRouter WITHOUT a basename, so
#     at the public path /opencost/ the router matched none of its exact routes
#     and rendered an EMPTY <div id="app"> — a white page. No amount of HTML
#     sub_filter or asset-path rewriting can fix that, because the router lives
#     in the browser and sees window.location.pathname === "/opencost/".
#
#   * Option B replaces it with a custom image built from upstream v1.120.4
#     (which has finalized VITE_BASENAME support) using the upstream-supported
#     build arg `vite_basename=/opencost`. That single build arg configures, at
#     BUILD time:
#         - Vite `base`            → /opencost/   (assets emit /opencost/assets/*)
#         - React Router `basename`→ /opencost    (routes match under /opencost)
#         - nginx `UI_PATH`        → /opencost    (container serves at /opencost)
#     so the container serves the whole SPA natively under /opencost/ and the
#     HTML rewrite proxy + separate asset ingress become unnecessary.
#
# These tests parse/render the REAL platform configuration and the REAL pinned
# upstream nginx serving template — they do not grep a single arbitrary string:
#
#   BuildContractTest          ui-image/build.sh pins the upstream tag + immutable
#                              commit, passes vite_basename=/opencost, embeds no
#                              credentials, and defines an image name/tag.
#   NginxServingRenderTest     the vendored v1.120.4 default.nginx.conf.template is
#                              rendered with the UI_PATH/BASE_URL taken from the
#                              Helm values, then a longest-prefix nginx location
#                              matcher proves: /opencost/ serves the SPA, deep
#                              links fall back to index.html, and /opencost/model/
#                              is the API proxy (API base = /opencost/model).
#   HelmWiringTest             values.yaml selects the custom image (NOT the stock
#                              root-only 1.113.0) and aligns BASE_URL + UI_PATH.
#   AuthRouteTest              oauth2-proxy forwards straight to opencost:9090
#                              (native basename, no prefix strip) with OIDC intact,
#                              and the portal ingress routes /opencost through the
#                              auth proxy WITHOUT a rewrite-target.
#   NoObsoleteWorkaroundsTest  the root-only-era workarounds are gone: no ui-proxy,
#                              no unauthenticated asset-bypass ingress, no
#                              BASE_URL_OVERRIDE hack, no sub_filter.
#   RouterBasenameDomTest      a faithful react-router basename simulation proves
#                              the OLD (no-basename) router renders an EMPTY #app
#                              at /opencost/ while the configured basename renders
#                              content — the DOM-level empty-root regression guard.
#
# Runs with the standard library + PyYAML only (no pytest, cluster or network):
#     python3 platform/tests/test_opencost_ui_subpath.py
# =============================================================================
import os
import re
import unittest

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OPENCOST_DIR = os.path.join(REPO_ROOT, "platform", "base", "opencost")
INGRESS_DIR = os.path.join(REPO_ROOT, "platform", "base", "ingress")

VALUES = os.path.join(OPENCOST_DIR, "values.yaml")
OAUTH2 = os.path.join(OPENCOST_DIR, "oauth2-proxy.yaml")
OPENCOST_KUSTOMIZATION = os.path.join(OPENCOST_DIR, "kustomization.yaml")
UI_IMAGE_DIR = os.path.join(OPENCOST_DIR, "ui-image")
BUILD_SH = os.path.join(UI_IMAGE_DIR, "build.sh")
NGINX_TEMPLATE = os.path.join(UI_IMAGE_DIR, "reference", "default.nginx.conf.template")

PORTAL_INGRESS = os.path.join(INGRESS_DIR, "opencost-portal-ingress.yaml")
INGRESS_KUSTOMIZATION = os.path.join(INGRESS_DIR, "kustomization.yaml")

# The custom UI image must be built from a release with finalized VITE_BASENAME
# support. v1.120.4 is the minimum documented by the issue; a newer pinned tag
# is acceptable, but never the root-only v1.113.0.
MIN_UI_MAJOR_MINOR_PATCH = (1, 120, 4)
STOCK_ROOT_ONLY_TAG = "1.113.0"

# The subpath the whole platform mounts OpenCost under.
SUBPATH = "/opencost"
EXPECTED_BASE_URL = "/opencost/model"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def read_text(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def load_yaml_docs(path):
    with open(path, "r", encoding="utf-8") as fh:
        return [d for d in yaml.safe_load_all(fh) if d is not None]


def load_single(path):
    docs = load_yaml_docs(path)
    assert len(docs) == 1, "%s must hold exactly one document" % path
    return docs[0]


def parse_version_tuple(tag):
    """Extract (major, minor, patch) from a tag like v1.120.4 or 1.120.4-foo."""
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", tag)
    if not m:
        return None
    return tuple(int(g) for g in m.groups())


def find_ui_extra_env(values):
    ui = values["opencost"]["ui"]
    env = ui.get("extraEnv") or []
    return {e["name"]: e["value"] for e in env if "name" in e and "value" in e}


# --------------------------------------------------------------------------- #
# nginx serving template render + location matching
# --------------------------------------------------------------------------- #
# The exact allowlist the upstream docker-entrypoint.sh passes to envsubst, so
# nginx runtime variables ($uri, $remote_addr, ...) survive rendering untouched.
ENVSUBST_VARS = (
    "API_PORT", "API_SERVER", "UI_PORT", "NGINX_ROOT", "UI_PATH", "BASE_URL",
    "PROXY_CONNECT_TIMEOUT", "PROXY_SEND_TIMEOUT", "PROXY_READ_TIMEOUT",
)


def render_template(template, env):
    """Faithful reimplementation of `envsubst '$VAR ...'` for the allowlist."""
    out = template
    for var in ENVSUBST_VARS:
        val = env[var]
        out = out.replace("${%s}" % var, val).replace("$%s" % var, val)
    return out


def parse_locations(nginx_conf):
    """Return list of (modifier, match, body) for every `location` block.

    modifier is '=' for exact matches, '' for prefix, '@' for named blocks.
    Bodies are extracted by brace matching so nested blocks are handled.
    """
    locations = []
    for m in re.finditer(r"location\s+(=\s+)?([^\s{]+)\s*\{", nginx_conf):
        modifier = "=" if m.group(1) else ("@" if m.group(2).startswith("@") else "")
        match = m.group(2)
        i = m.end() - 1
        depth = 0
        for j in range(i, len(nginx_conf)):
            if nginx_conf[j] == "{":
                depth += 1
            elif nginx_conf[j] == "}":
                depth -= 1
                if depth == 0:
                    locations.append((modifier, match, nginx_conf[i + 1 : j]))
                    break
    return locations


def match_location(locations, path):
    """Resolve `path` to a serving block using nginx's matching precedence:
    exact `=` wins, otherwise the longest matching prefix. Named (@) blocks are
    internal-only and never match an external request path.
    """
    for modifier, match, body in locations:
        if modifier == "=" and match == path:
            return (match, body)
    best = None
    for modifier, match, body in locations:
        if modifier == "" and path.startswith(match):
            if best is None or len(match) > len(best[0]):
                best = (match, body)
    return best


# --------------------------------------------------------------------------- #
# react-router basename simulation (the DOM empty-root guard)
# --------------------------------------------------------------------------- #
# Representative route table. The exact set differs between UI versions, but the
# mechanism under test is basename stripping: react-router removes the basename
# from location.pathname before matching, and renders nothing if the pathname
# does not start with the basename OR the remainder matches no route.
KNOWN_ROUTES = ("/", "/allocation", "/cloud", "/external-costs")


def router_render(pathname, basename, routes=KNOWN_ROUTES):
    """Return the matched route path, or None if <Switch> would render nothing.

    Mirrors react-router-dom BrowserRouter basename handling: if a non-empty
    basename is set, the pathname must start with it and is stripped before
    route matching; otherwise no route matches and #app stays empty.
    """
    if basename:
        if not pathname.startswith(basename):
            return None
        remainder = pathname[len(basename):] or "/"
    else:
        remainder = pathname
    # Normalise a trailing slash so "/opencost/" -> "/" matches the index route.
    if remainder != "/" and remainder.endswith("/"):
        remainder = remainder.rstrip("/")
    return remainder if remainder in routes else None


# =========================================================================== #
# tests
# =========================================================================== #
class BuildContractTest(unittest.TestCase):
    """The custom image build must be pinned, reproducible and credential-free."""

    @classmethod
    def setUpClass(cls):
        cls.script = read_text(BUILD_SH)

    def _var(self, name):
        m = re.search(r'^\s*%s="?([^"\n]+)"?\s*$' % re.escape(name), self.script, re.M)
        self.assertIsNotNone(m, "build.sh must define %s" % name)
        return m.group(1).strip()

    def test_pins_release_at_least_v1_120_4(self):
        tag = self._var("UPSTREAM_TAG")
        ver = parse_version_tuple(tag)
        self.assertIsNotNone(ver, "UPSTREAM_TAG must contain a semver: %r" % tag)
        self.assertGreaterEqual(
            ver, MIN_UI_MAJOR_MINOR_PATCH,
            "UPSTREAM_TAG %s is older than the minimum basename-aware release "
            "v%d.%d.%d" % ((tag,) + MIN_UI_MAJOR_MINOR_PATCH),
        )
        self.assertNotIn(STOCK_ROOT_ONLY_TAG, tag, "must not pin root-only v1.113.0")

    def test_pins_immutable_commit(self):
        sha = self._var("UPSTREAM_COMMIT")
        self.assertRegex(
            sha, r"^[0-9a-f]{40}$",
            "UPSTREAM_COMMIT must be a full 40-hex immutable commit for provenance",
        )

    def test_passes_vite_basename_build_arg(self):
        self.assertRegex(
            self.script,
            r"--build-arg\s+vite_basename=/opencost\b",
            "build must pass the upstream-supported build arg vite_basename=/opencost",
        )

    def test_passes_vite_api_base_build_arg(self):
        self.assertRegex(
            self.script,
            r"--build-arg\s+vite_base_api_url=/opencost/model\b",
            "the v1.120.4 standard UI compiles its JS API base from "
            "vite_base_api_url; it must stay under the authenticated subpath",
        )

    def test_local_and_harbor_image_coordinates_are_distinct_and_aligned(self):
        image = self._var("IMAGE")
        tag = self._var("TAG")
        registry = self._var("REGISTRY")
        registry_repository = self._var("REGISTRY_REPOSITORY")
        self.assertEqual(image, "digiorg/opencost-ui")
        self.assertIn("digiorg.local/library", registry)
        self.assertEqual(registry_repository, "opencost-ui")
        self.assertIn(
            '"${REGISTRY}/${REGISTRY_REPOSITORY}:${TAG}"', self.script,
            "Harbor push target must be digiorg.local/library/opencost-ui:<tag> "
            "without duplicating the local digiorg namespace",
        )
        self.assertNotIn(
            '"${REGISTRY}/${IMAGE}:${TAG}"', self.script,
            "Harbor target must not become library/digiorg/opencost-ui",
        )

    def test_passes_provenance_build_args(self):
        # version + commit are baked into the image footer/labels for provenance.
        for arg in ("version", "commit"):
            self.assertRegex(
                self.script, r"--build-arg\s+%s=" % arg,
                "build should pass provenance build arg %s=" % arg,
            )

    def test_defines_image_name_and_tag(self):
        self.assertTrue(self._var("IMAGE"))
        self.assertTrue(self._var("TAG"))

    def test_no_embedded_credentials(self):
        # No hard-coded secrets/passwords/tokens in the build script.
        for pat in (r"password\s*=", r"passwd\s*=", r"token\s*=",
                    r"-p\s+\S+", r"DOCKER_PASSWORD="):
            self.assertNotRegex(
                self.script, re.compile(pat, re.I),
                "build.sh must not embed credentials (matched %r)" % pat,
            )


class NginxServingRenderTest(unittest.TestCase):
    """Render the REAL pinned serving template with the REAL Helm UI_PATH/BASE_URL
    and prove the native subpath serving contract via nginx location matching."""

    @classmethod
    def setUpClass(cls):
        cls.template = read_text(NGINX_TEMPLATE)
        values = load_single(VALUES)
        cls.env_from_values = find_ui_extra_env(values)

    def _render(self):
        # UI_PATH and BASE_URL are driven by the actual Helm values, so a values
        # regression (e.g. reverting to BASE_URL_OVERRIDE) breaks this render.
        ui_path = self.env_from_values.get("UI_PATH")
        base_url = self.env_from_values.get("BASE_URL")
        self.assertEqual(ui_path, SUBPATH,
                         "opencost.ui.extraEnv UI_PATH must be %s" % SUBPATH)
        self.assertEqual(base_url, EXPECTED_BASE_URL,
                         "opencost.ui.extraEnv BASE_URL must be %s" % EXPECTED_BASE_URL)
        env = {
            "API_PORT": "9003", "API_SERVER": "0.0.0.0", "UI_PORT": "9090",
            "NGINX_ROOT": "/var/www", "UI_PATH": ui_path, "BASE_URL": base_url,
            "PROXY_CONNECT_TIMEOUT": "60s", "PROXY_SEND_TIMEOUT": "60s",
            "PROXY_READ_TIMEOUT": "60s",
        }
        return parse_locations(render_template(self.template, env))

    def test_spa_served_natively_under_subpath(self):
        locs = self._render()
        served, body = match_location(locs, SUBPATH + "/")
        self.assertEqual(served, SUBPATH,
                         "/opencost/ must be served by the `location /opencost` SPA block")
        self.assertIn("try_files", body, "SPA block must use try_files for fallback")
        self.assertIn("@ui_spa_fallback", body, "SPA block must fall back to the SPA handler")

    def test_deep_link_falls_back_to_index_html(self):
        locs = self._render()
        # A deep link with no matching file must resolve to the SPA block, whose
        # named fallback rewrites to <subpath>index.html (client-side routing).
        served, _ = match_location(locs, SUBPATH + "/cloud")
        self.assertEqual(served, SUBPATH,
                         "deep link /opencost/cloud must hit the SPA block, not a 404")
        fallback = [b for mod, mt, b in locs if mt == "@ui_spa_fallback"]
        self.assertTrue(fallback, "@ui_spa_fallback block must exist")
        self.assertRegex(
            fallback[0], r"rewrite\s+\^\s+%sindex\.html" % re.escape(SUBPATH),
            "SPA fallback must rewrite to %sindex.html" % SUBPATH,
        )

    def test_api_proxy_at_subpath_model(self):
        locs = self._render()
        served, body = match_location(locs, EXPECTED_BASE_URL + "/allocation/compute")
        self.assertEqual(
            served, EXPECTED_BASE_URL + "/",
            "API calls to %s/... must be routed by the `location %s/` proxy block, "
            "not swallowed by the SPA" % (EXPECTED_BASE_URL, EXPECTED_BASE_URL),
        )
        self.assertIn("proxy_pass", body, "API location must proxy_pass to the model")
        self.assertIn("model", body, "API location must target the cost-model upstream")

    def test_api_and_spa_do_not_collide(self):
        # Longest-prefix: the API prefix must win over the SPA prefix for /model.
        locs = self._render()
        spa, _ = match_location(locs, SUBPATH + "/")
        api, _ = match_location(locs, EXPECTED_BASE_URL + "/x")
        self.assertNotEqual(spa, api,
                            "SPA and API must resolve to different location blocks")


class HelmWiringTest(unittest.TestCase):
    """values.yaml must select the custom basename-aware image and align env."""

    @classmethod
    def setUpClass(cls):
        cls.values = load_single(VALUES)
        cls.ui = cls.values["opencost"]["ui"]
        cls.image = cls.ui.get("image", {})
        cls.env = find_ui_extra_env(cls.values)

    def _image_ref(self):
        if self.image.get("fullImageName"):
            return self.image["fullImageName"]
        return "%s/%s:%s" % (
            self.image.get("registry", ""),
            self.image.get("repository", ""),
            self.image.get("tag", ""),
        )

    def test_ui_enabled(self):
        self.assertTrue(self.ui.get("enabled"))

    def test_selects_non_stock_basename_aware_image(self):
        ref = self._image_ref()
        self.assertNotIn(
            STOCK_ROOT_ONLY_TAG, ref,
            "must not select the root-only stock UI %s: %r" % (STOCK_ROOT_ONLY_TAG, ref),
        )
        ver = parse_version_tuple(ref)
        self.assertIsNotNone(ver, "image ref must carry a semver tag: %r" % ref)
        self.assertGreaterEqual(
            ver, MIN_UI_MAJOR_MINOR_PATCH,
            "UI image %r is older than the basename-aware minimum" % ref,
        )

    def test_image_tag_matches_build_script(self):
        build = read_text(BUILD_SH)
        m_img = re.search(r'^\s*IMAGE="?([^"\n]+)"?\s*$', build, re.M)
        m_tag = re.search(r'^\s*TAG="?([^"\n]+)"?\s*$', build, re.M)
        self.assertTrue(m_img and m_tag)
        built = "%s:%s" % (m_img.group(1).strip(), m_tag.group(1).strip())
        self.assertIn(
            m_tag.group(1).strip(), self._image_ref(),
            "Helm image tag must match the tag produced by build.sh (%s)" % built,
        )

    def test_pull_policy_present(self):
        # kind-loaded local image: must not force a registry pull.
        self.assertIn(self.image.get("pullPolicy"), ("IfNotPresent", "Never"))

    def test_nginx_base_url_aligned_not_legacy_override(self):
        self.assertEqual(
            self.env.get("BASE_URL"), EXPECTED_BASE_URL,
            "runtime BASE_URL must render the nginx model proxy at %s; the JS "
            "API base is separately compiled by vite_base_api_url" % EXPECTED_BASE_URL,
        )
        self.assertNotIn(
            "BASE_URL_OVERRIDE", self.env,
            "the standard v1.120.4 build uses vite_base_api_url at build time "
            "and BASE_URL for nginx; do not use the legacy override",
        )

    def test_ui_path_aligned(self):
        self.assertEqual(self.env.get("UI_PATH"), SUBPATH)


class AuthRouteTest(unittest.TestCase):
    """Native basename means the auth proxy talks straight to opencost:9090."""

    @classmethod
    def setUpClass(cls):
        cls.oauth_docs = load_yaml_docs(OAUTH2)
        cls.deploy = next(
            d for d in cls.oauth_docs
            if d.get("kind") == "Deployment"
            and d["metadata"]["name"] == "opencost-oauth2-proxy"
        )
        cls.args = cls.deploy["spec"]["template"]["spec"]["containers"][0]["args"]

    def _arg(self, key):
        for a in self.args:
            if a.startswith(key + "="):
                return a.split("=", 1)[1]
        return None

    def test_upstream_is_opencost_ui_directly(self):
        upstream = self._arg("--upstream")
        self.assertIsNotNone(upstream)
        self.assertIn(
            "opencost.cost-monitoring.svc.cluster.local:9090", upstream,
            "with native basename the auth proxy must forward straight to the "
            "opencost UI (9090), not the obsolete sub_filter ui-proxy (9091)",
        )
        self.assertNotIn("9091", upstream, "the ui-proxy (9091) must be gone")

    def test_oidc_protection_intact(self):
        self.assertEqual(self._arg("--provider"), "oidc")
        self.assertEqual(self._arg("--client-id"), "opencost")
        self.assertTrue(
            (self._arg("--redirect-url") or "").startswith("https://digiorg.local/opencost/"),
            "OAuth callback must stay scoped under /opencost",
        )
        self.assertEqual(self._arg("--proxy-prefix"), "/opencost/oauth2")

    def test_portal_ingress_routes_through_auth_without_rewrite(self):
        ing = load_single(PORTAL_INGRESS)
        ann = ing["metadata"].get("annotations", {})
        self.assertNotIn(
            "nginx.ingress.kubernetes.io/rewrite-target", ann,
            "portal ingress must NOT rewrite the path — the native /opencost prefix "
            "has to reach the UI intact",
        )
        paths = ing["spec"]["rules"][0]["http"]["paths"]
        backends = {p["backend"]["service"]["name"] for p in paths}
        self.assertEqual(
            backends, {"opencost-oauth2-proxy"},
            "every /opencost path must go through the auth proxy",
        )
        # The path must cover both the bare subpath and everything beneath it.
        for p in paths:
            self.assertRegex(p["path"], r"/opencost")


class NoObsoleteWorkaroundsTest(unittest.TestCase):
    """The root-only-era workarounds must be fully removed, with no auth bypass."""

    def test_ui_proxy_manifest_removed(self):
        self.assertFalse(
            os.path.exists(os.path.join(OPENCOST_DIR, "ui-proxy.yaml")),
            "ui-proxy.yaml (HTML sub_filter rewrite proxy) must be deleted",
        )

    def test_ui_proxy_not_referenced_in_kustomization(self):
        # Assert the parsed resources list (comments may reference the removal).
        resources = load_single(OPENCOST_KUSTOMIZATION).get("resources", [])
        self.assertTrue(
            all("ui-proxy" not in r for r in resources),
            "ui-proxy must not be a kustomization resource: %r" % resources,
        )

    def test_asset_bypass_ingress_removed(self):
        self.assertFalse(
            os.path.exists(os.path.join(INGRESS_DIR, "opencost-assets-ingress.yaml")),
            "opencost-assets-ingress.yaml (unauthenticated asset bypass) must be deleted",
        )
        self.assertNotIn("opencost-assets-ingress", read_text(INGRESS_KUSTOMIZATION))

    def test_no_ingress_bypasses_auth_to_opencost(self):
        # No Ingress may route an /opencost* path straight to the opencost service
        # (that would expose assets — and potentially the cost API — unauthenticated).
        for fn in os.listdir(INGRESS_DIR):
            if not fn.endswith((".yaml", ".yml")):
                continue
            for doc in load_yaml_docs(os.path.join(INGRESS_DIR, fn)):
                if doc.get("kind") != "Ingress":
                    continue
                for rule in doc.get("spec", {}).get("rules", []):
                    for p in rule.get("http", {}).get("paths", []):
                        path = p.get("path", "")
                        svc = p["backend"]["service"]["name"]
                        if "opencost" in path and svc == "opencost":
                            self.fail(
                                "%s routes %r straight to the opencost service, "
                                "bypassing the auth proxy" % (fn, path)
                            )

    def test_no_sub_filter_rewrite_remnants(self):
        # sub_filter directives / BASE_URL_OVERRIDE env belong to the removed
        # root-only pipeline. Scan NON-comment lines only, so a comment may
        # explain the removal while any reintroduced directive/env still fails.
        for fn in os.listdir(OPENCOST_DIR):
            path = os.path.join(OPENCOST_DIR, fn)
            if not os.path.isfile(path) or not fn.endswith((".yaml", ".yml")):
                continue
            for raw in read_text(path).splitlines():
                line = raw.strip()
                if line.startswith("#"):
                    continue
                self.assertNotIn("sub_filter", line,
                                 "%s still has a live sub_filter directive: %r" % (fn, raw))
                self.assertNotIn("BASE_URL_OVERRIDE", line,
                                 "%s still uses the legacy BASE_URL_OVERRIDE env: %r" % (fn, raw))


class RouterBasenameDomTest(unittest.TestCase):
    """DOM-level guard: prove the empty-#app root cause is fixed by the basename.

    The build arg vite_basename drives BOTH the Vite base and the react-router
    basename, so this test reads that arg from build.sh and uses it as the
    router basename — the DOM assertion is driven by real config, not a literal.
    """

    @classmethod
    def setUpClass(cls):
        build = read_text(BUILD_SH)
        m = re.search(r"--build-arg\s+vite_basename=(\S+)", build)
        cls.basename = m.group(1).rstrip("/") if m else None

    def test_root_only_router_renders_empty_app_at_subpath(self):
        # This is the confirmed root cause: no basename => nothing matches
        # /opencost/ => <div id="app"> stays empty (white page).
        self.assertIsNone(
            router_render("/opencost/", basename=None),
            "sanity: a root-only router must render nothing at /opencost/",
        )

    def test_configured_basename_renders_content_at_subpath(self):
        self.assertIsNotNone(self.basename, "vite_basename build arg not found in build.sh")
        self.assertEqual(self.basename, SUBPATH)
        matched = router_render("/opencost/", basename=self.basename)
        self.assertIsNotNone(
            matched,
            "with basename %s the index route must match at /opencost/ so #app is "
            "NOT empty" % self.basename,
        )

    def test_configured_basename_matches_deep_links(self):
        for pathname, expected in (
            ("/opencost/cloud", "/cloud"),
            ("/opencost/allocation", "/allocation"),
        ):
            self.assertEqual(
                router_render(pathname, basename=self.basename), expected,
                "deep link %s must match its client-side route under the basename"
                % pathname,
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
