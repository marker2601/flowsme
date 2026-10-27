"""Run and administer the FlowSME server (only on the server; customers never get this).

  python -m flowsme.server serve [--host 127.0.0.1] [--port 8765]
  python -m flowsme.server create-org "Acme Finance" --seats 5 --manager "Dana Lee"
  python -m flowsme.server add-manager "Acme Finance" "Sam Patel"
  python -m flowsme.server seats "Acme Finance" 10
  python -m flowsme.server list

The data folder is FLOWSME_DATA, or "server-data" in the current folder. Seat keys are shown once and saved as
a seat file for the person; the server keeps only their hashes."""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

from .store import Store


def _seat_file(server: str, org: dict, member: dict, key: str, out: Path) -> Path:
    slug = re.sub(r"[^A-Za-z0-9]+", " ", member["name"]).strip() or "person"
    path = out / f"{slug} - FlowSME seat.flowseat"
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
    args = p.parse_args()

    data = Path(args.data)
    if args.cmd == "serve":
        import uvicorn

        from .api import create_app

        print(f"FlowSME server {data.resolve()} on http://{args.host}:{args.port}")
        uvicorn.run(create_app(data, args.workers), host=args.host, port=args.port, log_level="info")
        return
    store = Store(data)
    out = data / "seat-files"
    out.mkdir(parents=True, exist_ok=True)
    if args.cmd == "create-org":
        org = store.create_org(args.name, args.seats, args.currency)
        member, key = store.add_member(org, args.manager, org["default_position"], role="manager")
        print(f"Created {org['name']} ({org['id']}) with {org['seats']} seats.")
        print(f"Manager seat file: {_seat_file(args.server_url, org, member, key, out)}")
    elif args.cmd == "add-manager":
        org = store.by_name(args.org)
        member, key = store.add_member(org, args.name, org["default_position"], role="manager")
        print(f"Manager seat file: {_seat_file(args.server_url, org, member, key, out)}")
    elif args.cmd == "seats":
        org = store.by_name(args.org)
        if args.seats < len(store.employees(org)):
            raise SystemExit(f"{len(store.employees(org))} seats are in use: revoke some first.")
        org["seats"] = args.seats
        store.save_org(org)
        print(f"{org['name']} now has {args.seats} seats.")
    else:
        for org in store.orgs():
            print(f"{org['id']}  {org['name']}: {len(store.employees(org))} of {org['seats']} seats used, "
                  f"{len(store.records(org['id']))} recordings")


if __name__ == "__main__":
    main()
