#!/usr/bin/env python3
"""Issue 372 dedicated provisioning identity. Never change shared group policy."""
import base64
import json
import secrets
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request


class Client:
    def __init__(self, base, ca_file, password):
        parsed = urllib.parse.urlsplit(base)
        if parsed.scheme != "https" or parsed.path != "/sonarqube" or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise PolicyError("InvalidApiOrigin")
        self.base = base
        self.admin = "Basic " + base64.b64encode(("admin:" + password).encode()).decode()
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(), urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=str(ca_file))))

    def __call__(self, path, data=None, token=None):
        request = urllib.request.Request(self.base + path, data=None if data is None else urllib.parse.urlencode(data).encode(),
                                         headers={"Authorization": "Bearer " + token if token is not None else self.admin,
                                                  "Content-Type": "application/x-www-form-urlencoded"})
        try:
            with self.opener.open(request, timeout=15) as response:
                if response.status not in (200, 204):
                    raise PolicyError("UnexpectedApiStatus")
                raw = response.read(4 * 1024 * 1024)
                result = json.loads(raw) if raw else {}
                if not isinstance(result, dict):
                    raise PolicyError("MalformedApiResponse")
                return result
        except urllib.error.HTTPError as error:
            error.close()
            raise PolicyError("ApiRequestFailed") from None
        except Exception:
            raise PolicyError("ApiRequestFailed") from None


class PolicyError(Exception):
    pass


def complete(api, path, field, **kwargs):
    result = api(path, **kwargs)
    rows = result.get(field)
    if not isinstance(rows, list) or result.get("paging", {}).get("total") != len(rows):
        raise PolicyError("IncompletePolicyInventory")
    return rows


def verify_shared_policy(api):
    groups = complete(api, "/api/permissions/groups?ps=100", "groups")
    effective = {g["name"]: set(g.get("permissions", [])) for g in groups}
    if not effective.get("sonar-users", set()).issubset({"provisioning", "scan"}) or effective.get("Anyone", set()):
        raise PolicyError("UnexpectedGlobalGroupRights")
    policy = api("/api/permissions/search_templates")
    templates = policy.get("permissionTemplates", [])
    defaults = policy.get("defaultTemplates", [])
    if (len(templates) != 1 or templates[0].get("name") != "Default template"
            or templates[0].get("projectKeyPattern")
            or defaults != [{"qualifier": "TRK", "templateId": templates[0].get("id")}]
            or any(p.get("withProjectCreator") or p.get("usersCount", 0) != 0 for p in templates[0].get("permissions", []))):
        raise PolicyError("PermissionTemplateMismatch")
    group_rows = complete(api, "/api/permissions/template_groups?templateName=Default%20template&ps=100", "groups")
    expected = {"sonar-users": {"user", "codeviewer", "issueadmin", "securityhotspotadmin"}, "sonar-administrators": {"admin"}, "Anyone": set()}
    if {g["name"]: set(g.get("permissions", [])) for g in group_rows} != expected:
        raise PolicyError("UnexpectedProjectTemplateRights")
    users = complete(api, "/api/permissions/template_users?templateName=Default%20template&ps=100", "users")
    if any(u.get("permissions") for u in users):
        raise PolicyError("UnexpectedTemplateUserRights")


def prepare(api, existing_token):
    verify_shared_policy(api)
    users = complete(api, "/api/users/search?q=digiorg-provisioner&ps=100", "users")
    exact = [u for u in users if u.get("login") == "digiorg-provisioner"]
    if not exact:
        api("/api/users/create", {"login": "digiorg-provisioner", "name": "DigiOrg provisioner", "local": "true", "password": secrets.token_urlsafe(48)})
        exact = [u for u in complete(api, "/api/users/search?q=digiorg-provisioner&ps=100", "users") if u.get("login") == "digiorg-provisioner"]
    if len(exact) != 1 or exact[0].get("local") is not True or exact[0].get("active") is not True:
        raise PolicyError("ProvisionerAccountMismatch")
    membership = complete(api, "/api/users/groups?login=digiorg-provisioner&selected=selected&ps=100", "groups")
    if {g.get("name") for g in membership if g.get("selected") is True} != {"sonar-users"}:
        raise PolicyError("UnexpectedProvisionerGroups")
    rights = complete(api, "/api/permissions/users?q=digiorg-provisioner&ps=100", "users")
    exact_rights = [u for u in rights if u.get("login") == "digiorg-provisioner"]
    if len(exact_rights) != 1 or not set(exact_rights[0].get("permissions", [])).issubset({"provisioning"}):
        raise PolicyError("UnexpectedProvisionerGlobalRights")
    if "provisioning" not in exact_rights[0].get("permissions", []):
        api("/api/permissions/add_user", {"login": "digiorg-provisioner", "permission": "provisioning"})
        current = complete(api, "/api/permissions/users?q=digiorg-provisioner&ps=100", "users")
        if not any(u.get("login") == "digiorg-provisioner" and set(u.get("permissions", [])) == {"provisioning"} for u in current):
            raise PolicyError("ProvisioningGrantNotObserved")
    token = existing_token or api("/api/user_tokens/generate", {"login": "digiorg-provisioner", "name": "crossplane-onboarding-v1", "type": "USER_TOKEN"})["token"]
    if api("/api/authentication/validate", token=token).get("valid") is not True:
        raise PolicyError("ProvisionerAuthenticationFailed")
    if api("/api/users/current", token=token).get("login") != "digiorg-provisioner":
        raise PolicyError("ProvisionerIdentityMismatch")
    return token


if __name__ == "__main__":
    # Only called by local-setup.nu with captured stdout. Inputs and the returned
    # token travel through pipes, never argv/files/logs. No automatic rotation.
    try:
        config = json.load(sys.stdin)
        client = Client("https://digiorg.local/sonarqube", config["ca_file"], config["password"])
        if client("/api/system/status").get("status") != "UP":
            raise PolicyError("ApiNotUp")
        print(prepare(client, config["existing_token"]))
    except Exception:
        print("SonarQube provisioner prerequisite failed; inspect policy/account/transport without exposing credentials", file=sys.stderr)
        raise SystemExit(1)
