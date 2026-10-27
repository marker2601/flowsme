"""Create a generated workflow in the tool a business runs workflows in, through that tool's API.

This is the half of deployment that runs on the customer's computer (their n8n or Node-RED is often only
reachable there). It only sends a workflow it is given: the workflows themselves are generated on the FlowSME
server (deploy.py), so none of that logic ships with the client app."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Callable


@dataclass
class Setting:
    env: str  # kept as this user environment variable, like the AI key, so the command line finds it too
    label: str
    secret: bool = False
    default: str = ""
    optional: bool = False
    hint: str = ""
    keep: bool = True  # False for short-lived tokens: used for this build only, never saved


@dataclass
class Connector:
    id: str
    name: str
    help: str
    tested: bool  # created in a live instance by FlowSME's tests; the others follow the vendor's API docs
    settings: list[Setting]
    create: Callable[[dict, dict], str]
    check: Callable[[dict], str]


def _nodered_headers(v: dict) -> dict:
    headers = {"Node-RED-API-Version": "v2"}
    if v.get("NODERED_TOKEN"):
        headers["Authorization"] = f"Bearer {v['NODERED_TOKEN']}"
    return headers


def nodered_create(doc: dict, v: dict) -> str:
    base = v["NODERED_URL"].rstrip("/")
    body = {k: doc[k] for k in ("label", "info", "env", "disabled", "nodes", "configs")}
    result = _call("Node-RED", "POST", f"{base}/flow", _nodered_headers(v), body)
    return f"Created in Node-RED (tab '{doc['label']}', disabled) with id {result.get('id', '?')}: {base}"


def nodered_check(v: dict) -> str:
    _call("Node-RED", "GET", f"{v['NODERED_URL'].rstrip('/')}/flows", _nodered_headers(v))
    return "Node-RED answered: the address and token work."


def n8n_create(doc: dict, v: dict) -> str:
    base = v["N8N_URL"].rstrip("/")
    body = {k: doc[k] for k in ("name", "nodes", "connections", "settings")}  # n8n refuses any other field
    result = _call("n8n", "POST", f"{base}/api/v1/workflows", {"X-N8N-API-KEY": v["N8N_API_KEY"]}, body)
    return f"Created in n8n (inactive) with id {result.get('id', '?')}: {base}/workflow/{result.get('id', '')}"


def n8n_check(v: dict) -> str:
    _call("n8n", "GET", f"{v['N8N_URL'].rstrip('/')}/api/v1/workflows?limit=1", {"X-N8N-API-KEY": v["N8N_API_KEY"]})
    return "n8n answered: the address and API key work."


def _windmill(v: dict) -> tuple[str, dict]:
    base = f"{v['WINDMILL_URL'].rstrip('/')}/api/w/{urllib.parse.quote(v['WINDMILL_WORKSPACE'])}"
    return base, {"Authorization": f"Bearer {v['WINDMILL_TOKEN']}"}


def windmill_create(doc: dict, v: dict) -> str:
    base, headers = _windmill(v)
    user = _call("Windmill", "GET", f"{base}/users/whoami", headers).get("username") or "admin"
    body = dict(doc, path=f"u/{user}/flowsme_{doc['path'].rsplit('flowsme_', 1)[-1]}")
    _call("Windmill", "POST", f"{base}/flows/create", headers, body)
    _call("Windmill", "POST", f"{base}/schedules/create", headers, {  # every 15 minutes, off until switched on
        "path": f"{body['path']}_schedule", "schedule": "0 */15 * * * *", "timezone": "UTC",
        "script_path": body["path"], "is_flow": True, "args": {}, "enabled": False})
    return f"Created in Windmill as {body['path']}, with a 15-minute schedule switched off."


def windmill_check(v: dict) -> str:
    base, headers = _windmill(v)
    user = _call("Windmill", "GET", f"{base}/users/whoami", headers)
    return f"Windmill answered: signed in as {user.get('username', '?')}."


def _make(v: dict) -> tuple[str, dict]:
    zone = v["MAKE_ZONE"].strip().lower().removeprefix("https://").split(".")[0] or "eu1"
    return f"https://{zone}.make.com/api/v2", {"Authorization": f"Token {v['MAKE_API_TOKEN']}"}


def make_create(doc: dict, v: dict) -> str:
    base, headers = _make(v)
    team = v.get("MAKE_TEAM_ID", "").strip()
    if not team:  # the first team of the first organization the token can see
        orgs = _call("Make", "GET", f"{base}/organizations", headers).get("organizations") or []
        if not orgs:
            raise SystemExit("Make: this token sees no organization. Enter the team id.")
        teams = _call("Make", "GET", f"{base}/teams?organizationId={orgs[0]['id']}", headers).get("teams") or []
        if not teams:
            raise SystemExit("Make: no team found. Enter the team id.")
        team = str(teams[0]["id"])
    if not team.isdigit():
        raise SystemExit("Make: the team id is a number (Make > Team > the number in the address).")
    body = {"blueprint": json.dumps(doc["blueprint"], ensure_ascii=False), "teamId": int(team),
            "scheduling": json.dumps(doc["scheduling"])}
    scenario = _call("Make", "POST", f"{base}/scenarios?confirmed=true", headers, body).get("scenario") or {}
    return f"Created in Make (inactive) as scenario {scenario.get('id', '?')} in team {team}."


def make_check(v: dict) -> str:
    base, headers = _make(v)
    me = _call("Make", "GET", f"{base}/users/me", headers).get("authUser") or {}
    return f"Make answered: signed in as {me.get('name') or me.get('email') or 'you'}."


def _az_token(resource: str) -> str:
    """A token from the Azure CLI's sign-in (az login), when no token was pasted."""
    az = shutil.which("az") or shutil.which("az.cmd")
    if not az:
        raise SystemExit("Paste an access token, or install the Azure CLI and run 'az login' once.")
    try:
        out = subprocess.run([az, "account", "get-access-token", "--resource", resource, "--query", "accessToken",
                              "-o", "tsv"], capture_output=True, text=True, timeout=60,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SystemExit(f"The Azure CLI didn't answer: {exc}") from None
    if out.returncode != 0 or not out.stdout.strip():
        raise SystemExit(f"The Azure CLI has no token: run 'az login' first. {out.stderr.strip()[:200]}")
    return out.stdout.strip()


def _dataverse(v: dict) -> tuple[str, dict]:
    env = v["POWERAUTOMATE_ENV_URL"].strip().rstrip("/")
    if not env.startswith("https://"):
        env = "https://" + env
    token = v.get("POWERAUTOMATE_TOKEN", "").strip() or _az_token(env)
    return f"{env}/api/data/v9.2", {"Authorization": f"Bearer {token}", "OData-MaxVersion": "4.0",
                                    "OData-Version": "4.0", "Prefer": "return=representation"}


def power_automate_create(doc: dict, v: dict) -> str:
    base, headers = _dataverse(v)
    body = {"category": 5, "type": 1, "primaryentity": "none", "name": doc["name"], "description": doc["description"],
            "clientdata": json.dumps(doc["clientdata"], ensure_ascii=False)}
    flow = _call("Power Automate", "POST", f"{base}/workflows", headers, body)
    return (f"Created in Power Automate (off) with id {flow.get('workflowid', '?')}. Open it under Solutions > "
            "Default Solution > Cloud flows, pick the connections, then turn it on.")


def power_automate_check(v: dict) -> str:
    base, headers = _dataverse(v)
    who = _call("Power Automate", "GET", f"{base}/WhoAmI", headers)
    return f"Power Automate (Dataverse) answered: user {who.get('UserId', '?')}."


def _arm(v: dict) -> tuple[str, dict]:
    sub, group = v["AZURE_SUBSCRIPTION_ID"].strip(), v["AZURE_RESOURCE_GROUP"].strip()
    token = v.get("AZURE_TOKEN", "").strip() or _az_token("https://management.azure.com/")
    return (f"https://management.azure.com/subscriptions/{sub}/resourceGroups/{urllib.parse.quote(group)}",
            {"Authorization": f"Bearer {token}"})


def logic_app_create(doc: dict, v: dict) -> str:
    base, headers = _arm(v)
    name = re.sub(r"[^A-Za-z0-9-]+", "-", f"flowsme-{v.get('_slug', 'automation')}").strip("-")[:80]
    body = {k: val for k, val in doc.items() if not k.startswith("_")}
    body["location"] = v["AZURE_LOCATION"].strip() or "westeurope"
    result = _call("Azure", "PUT", f"{base}/providers/Microsoft.Logic/workflows/{name}?api-version=2019-05-01",
                   headers, body)
    return f"Created in Azure Logic Apps (disabled) as {result.get('id', name)}."


def logic_app_check(v: dict) -> str:
    base, headers = _arm(v)
    group = _call("Azure", "GET", f"{base}?api-version=2021-04-01", headers)
    return f"Azure answered: resource group {group.get('name', '?')} in {group.get('location', '?')}."


def _call(service: str, method: str, url: str, headers: dict | None = None, body=None, timeout: int = 30):
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=data, method=method, headers={
        "Accept": "application/json", **({"Content-Type": "application/json"} if data else {}), **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        hint = " Check the key or token." if exc.code in (401, 403) else ""
        raise SystemExit(f"{service} refused it ({exc.code}).{hint} {detail}".strip()) from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise SystemExit(f"Can't reach {service} at {url.split('?')[0]}: {getattr(exc, 'reason', exc)}") from None
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        return {"text": raw}
    return data if isinstance(data, dict) else {"result": data}


CONNECTORS: dict[str, Connector] = {c.id: c for c in (
    Connector("n8n", "n8n",
              "In n8n: Settings > n8n API > Create an API key. Works with n8n Cloud and self-hosted n8n.", True,
              [Setting("N8N_URL", "n8n address", default="http://localhost:5678"),
               Setting("N8N_API_KEY", "n8n API key", secret=True)], n8n_create, n8n_check),
    Connector("nodered", "Node-RED",
              "Free and open source. The address of your Node-RED editor; a token only if adminAuth is on.", True,
              [Setting("NODERED_URL", "Node-RED address", default="http://localhost:1880"),
               Setting("NODERED_TOKEN", "Access token", secret=True, optional=True, hint="only if adminAuth is on")],
              nodered_create, nodered_check),
    Connector("windmill", "Windmill",
              "Windmill Cloud (free tier) or self-hosted. Account settings > Tokens > New token.", False,
              [Setting("WINDMILL_URL", "Windmill address", default="https://app.windmill.dev"),
               Setting("WINDMILL_WORKSPACE", "Workspace id"),
               Setting("WINDMILL_TOKEN", "Token", secret=True)], windmill_create, windmill_check),
    Connector("make", "Make",
              "Make Core plan or higher. Profile > API access > Add token (scopes: scenarios:write, teams:read, "
              "organizations:read). The zone is the start of your Make address, e.g. eu1.", False,
              [Setting("MAKE_ZONE", "Make zone", default="eu1", hint="eu1, eu2, us1 or us2"),
               Setting("MAKE_API_TOKEN", "API token", secret=True),
               Setting("MAKE_TEAM_ID", "Team id", optional=True, hint="empty = your first team")],
              make_create, make_check),
    Connector("powerautomate", "Power Automate",
              "Your environment's Dataverse address (Power Platform admin center > Environments > Environment URL). "
              "Sign in once with the Azure CLI ('az login'), or paste an access token.", False,
              [Setting("POWERAUTOMATE_ENV_URL", "Environment URL", hint="https://yourorg.crm.dynamics.com"),
               Setting("POWERAUTOMATE_TOKEN", "Access token", secret=True, optional=True,
                       hint="empty = use az login", keep=False)],
              power_automate_create, power_automate_check),
    Connector("logicapps", "Azure Logic Apps",
              "An Azure subscription and resource group. Sign in once with the Azure CLI ('az login'), or paste an "
              "access token.", False,
              [Setting("AZURE_SUBSCRIPTION_ID", "Subscription id"),
               Setting("AZURE_RESOURCE_GROUP", "Resource group"),
               Setting("AZURE_LOCATION", "Region", default="westeurope"),
               Setting("AZURE_TOKEN", "Access token", secret=True, optional=True, hint="empty = use az login",
                       keep=False)],
              logic_app_create, logic_app_check),
)}


def create(connector_id: str, doc: dict, settings: dict, slug: str = "automation") -> str:
    """Send a generated workflow (deploy.py's render for this tool) to the tool; returns what was created."""
    return CONNECTORS[connector_id].create(doc, dict(settings, _slug=slug))
