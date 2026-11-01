"""Run and administer the FlowSME server (only on the server; customers never get this).

  python -m flowsme.server serve [--host 127.0.0.1] [--port 8765]
  python -m flowsme.server create-org "Acme Finance" --seats 5 --manager "Dana Lee"
  python -m flowsme.server add-manager "Acme Finance" "Sam Patel"
  python -m flowsme.server seats "Acme Finance" 10
  python -m flowsme.server list

The data folder is FLOWSME_DATA, or "server-data" in the current folder. A new seat key is written once, into a
seat file in --out (default "seat-files-to-send", outside the data folder): send it to the person over a private
channel, then delete it. The server keeps only the key's hash."""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

from .store import Store


def _seat_file(server: str, org: dict, member: dict, key: str, out: Path) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    slug = lambda text, fallback: re.sub(r"[^A-Za-z0-9]+", " ", text).strip()[:40] or fallback  # noqa: E731
    path = out / f"{slug(org['name'], org['id'])} - {slug(member['name'], 'person')} - {member['seat']}.flowseat"
    path.write_text(json.dumps({"kind": "flowsme-cloud-seat", "version": 1, "server": server, "key": key,
                                "name": member["name"], "team": org["name"], "role": member["role"]}, indent=1),
                    encoding="utf-8")
    return path


def main() -> None:
    p = argparse.ArgumentParser(prog="python -m flowsme.server", description="FlowSME server")
    p.add_argument("--data", default=os.environ.get("FLOWSME_DATA", "server-data"), help="data folder")
    p.add_argument("--server-url", default=os.environ.get("FLOWSME_SERVER_URL", "http://127.0.0.1:8765"),
                   help="the address customers' apps use, written into seat files")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="run the API")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--workers", type=int, default=2, help="analyses at the same time")
    c = sub.add_parser("create-org", help="a new customer organisation, with its manager's seat file")
    c.add_argument("name")
    c.add_argument("--seats", type=int, required=True, help="seats in the customer's plan")
    c.add_argument("--manager", required=True, help="the manager's name")
    c.add_argument("--currency", default="$")
    m = sub.add_parser("add-manager", help="another manager for an organisation")
    m.add_argument("org")
    m.add_argument("name")
    n = sub.add_parser("seats", help="change an organisation's number of seats")
    n.add_argument("org")
    n.add_argument("seats", type=int)
    sub.add_parser("list", help="organisations and their seats")
    p.add_argument("--out", default=os.environ.get("FLOWSME_SEAT_OUT", "seat-files-to-send"),
                   help="where new seat files go (keep it outside the data folder; delete them once sent)")
    args = p.parse_args()

    data = Path(args.data)
    if args.cmd == "serve":
        import uvicorn

        from .api import create_app

        print(f"FlowSME server {data.resolve()} on http://{args.host}:{args.port}")
        # Behind a TLS proxy, trust its X-Forwarded-For (FORWARDED_ALLOW_IPS) so each client is told apart.
        # Run one server process per data folder: the JSON files are locked within a process only.
        uvicorn.run(create_app(data, args.workers), host=args.host, port=args.port, log_level="info",
                    proxy_headers=True, forwarded_allow_ips=os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1"))
        return
    store = Store(data)
    out = Path(args.out)
    if args.cmd in ("create-org", "add-manager"):
        if out.resolve() == data.resolve() or data.resolve() in out.resolve().parents:
            raise SystemExit("Keep --out outside the data folder: seat files hold keys, the server keeps only hashes.")
        try:  # before anything is created, so a key is never made that can't be handed over
            out.mkdir(parents=True, exist_ok=True)
            probe = out / ".write-test"
            probe.write_text("", encoding="utf-8")
            probe.unlink()
        except OSError as exc:
            raise SystemExit(f"Can't write seat files to {out}: {exc}. Choose another folder with --out.") from None
    try:
        _admin(args, store, out)
    except (KeyError, ValueError) as exc:
        raise SystemExit(str(exc).strip("'\"")) from None


def _admin(args, store: Store, out: Path) -> None:
    sent = "Send it to them privately, then delete it: FlowSME can always make a new key."
    if args.cmd == "create-org":
        org = store.create_org(args.name, args.seats, args.currency)
        member, key = store.add_member(org, args.manager, org["default_position"], role="manager")
        print(f"Created {org['name']} ({org['id']}) with {org['seats']} seat{'' if org['seats'] == 1 else 's'}.")
        print(f"Manager seat file: {_seat_file(args.server_url, org, member, key, out)}\n{sent}")
    elif args.cmd == "add-manager":
        org = store.by_name(args.org)
        member, key = store.add_member(org, args.name, org["default_position"], role="manager")
        print(f"Manager seat file: {_seat_file(args.server_url, org, member, key, out)}\n{sent}")
    elif args.cmd == "seats":
        org = store.by_name(args.org)
        store.set_seats(org, args.seats)
        print(f"{org['name']} now has {args.seats} seats.")
    else:
        for org in store.orgs():
            print(f"{org['id']}  {org['name']}: {len(store.employees(org))} of {org['seats']} seats used, "
                  f"{len(store.records(org['id']))} recordings")


if __name__ == "__main__":
    main()
