"""The manager's Admin panel in the customer app: seats, positions and rates, all kept on the FlowSME server."""
from __future__ import annotations

import json
import os
import re
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .account import SEAT_KIND
from .cloud import CloudError


def open_admin(app) -> None:
    try:
        data = app.cloud.admin()
    except CloudError as exc:
        messagebox.showerror("FlowSME", str(exc), parent=app.root)
        return
    Admin(app, data)


class Admin:
    def __init__(self, app, data: dict):
        self.app, self.data = app, data
        self.scale = app.root.winfo_fpixels("1i") / 96
        self.win = tk.Toplevel(app.root)
        self.win.title(f"Admin panel: {data['team']}")
        self.win.transient(app.root)
        self.win.bind("<Escape>", lambda _: self.close())
        self.win.protocol("WM_DELETE_WINDOW", self.close)
        frame = ttk.Frame(self.win, padding=16)
        frame.pack(fill="both", expand=True)
        tabs = ttk.Notebook(frame)
        tabs.pack(fill="both", expand=True)
        self._people(tabs)
        self._positions(tabs)
        bar = ttk.Frame(frame)
        bar.pack(fill="x", pady=(12, 0))
        ttk.Label(bar, style="Muted.TLabel", text="Kept on the FlowSME server: employees never see rates, and a "
                  "revoked seat stops working at once.").pack(side="left")
        ttk.Button(bar, text="Close", command=self.close).pack(side="right")
        self._fill()
        self.win.update_idletasks()
        root = app.root
        self.win.geometry(f"+{root.winfo_rootx() + 40}+{root.winfo_rooty() + 60}")

    def close(self) -> None:
        self.win.destroy()
        self.app._bg(self.app.cloud.me, self.app._signed_in)

    def _call(self, fn, *args):
        try:
            return fn(*args)
        except CloudError as exc:
            messagebox.showerror("FlowSME", str(exc), parent=self.win)
            return None

    def _tree(self, parent, cols) -> ttk.Treeview:
        tree = ttk.Treeview(parent, columns=[c for c, _, _ in cols], show="headings", height=8, selectmode="browse")
        for col, label, width in cols:
            tree.heading(col, text=label, anchor="w")
            tree.column(col, width=int(width * self.scale), anchor="w")
        return tree

    # ---- people and seats -------------------------------------------------------------------------

    def _people(self, tabs: ttk.Notebook) -> None:
        page = ttk.Frame(tabs, padding=12)
        tabs.add(page, text="People and seats")
        self.seats = ttk.Label(page, style="Heading.TLabel")
        self.seats.pack(anchor="w")
        ttk.Label(page, style="Muted.TLabel", wraplength=int(620 * self.scale), justify="left", text=(
            "Add a person and FlowSME saves their seat file, with their key in it: send it to them (it is shown "
            "only once, so keep it safe). They open FlowSME and pick it. A new key stops the old one working."
        )).pack(anchor="w", pady=(2, 8))
        self.people = self._tree(page, (("name", "Name", 150), ("position", "Position", 160), ("role", "Role", 80),
                                        ("seat", "Seat", 110), ("status", "Status", 70), ("issued", "Since", 90)))
        self.people.pack(fill="both", expand=True)
        row = ttk.Frame(page)
        row.pack(fill="x", pady=(8, 0))
        for text, command in (("Add person...", self.add_person), ("Change position...", self.move_person),
                              ("New key...", self.new_key), ("Revoke seat", lambda: self.status("revoke")),
                              ("Restore seat", lambda: self.status("restore"))):
            ttk.Button(row, text=text, command=command).pack(side="left", padx=(0, 6))

    def _member(self) -> dict | None:
        chosen = self.people.selection()
        if not chosen:
            messagebox.showinfo("FlowSME", "Select a person first.", parent=self.win)
            return None
        return next(m for m in self.data["members"] if m["seat"] == chosen[0])

    def _form(self, title: str, rows, ok: str, action) -> None:
        win = tk.Toplevel(self.win)
        win.title(title)
        win.resizable(False, False)
        win.transient(self.win)
        f = ttk.Frame(win, padding=16)
        f.pack()
        for n, (label, make) in enumerate(rows):
            ttk.Label(f, text=label).grid(row=n, column=0, sticky="w", pady=3)
            make(f).grid(row=n, column=1, sticky="w", padx=(8, 0), pady=3)
        bar = ttk.Frame(f)
        bar.grid(row=len(rows), column=0, columnspan=2, sticky="e", pady=(12, 0))

        def go() -> None:
            if action() is not False:
                win.destroy()
        ttk.Button(bar, text=ok, style="Accent.TButton", command=go).pack(side="left")
        ttk.Button(bar, text="Cancel", command=win.destroy).pack(side="left", padx=(8, 0))
        win.bind("<Escape>", lambda _: win.destroy())
        win.geometry(f"+{self.win.winfo_rootx() + 60}+{self.win.winfo_rooty() + 60}")
        win.grab_set()

    def _save_seat(self, member: dict, key: str) -> None:
        from .app import _desktop

        slug = re.sub(r"[^A-Za-z0-9]+", " ", member["name"]).strip() or "person"
        file = filedialog.asksaveasfilename(parent=self.win, title=f"Save {member['name']}'s seat file",
                                            initialdir=str(_desktop()), initialfile=f"{slug} - FlowSME seat.flowseat",
                                            defaultextension=".flowseat", filetypes=[("FlowSME seat", "*.flowseat")])
        if not file:
            messagebox.showwarning("FlowSME", f"The key wasn't saved. Use New key... to make another for "
                                              f"{member['name']}.", parent=self.win)
            return
        Path(file).write_text(json.dumps({"kind": SEAT_KIND, "version": 1, "server": self.app.cloud.server,
                                          "key": key, "name": member["name"], "team": self.data["team"],
                                          "role": member.get("role", "employee")}, indent=1), encoding="utf-8")
        print(f"Seat file for {member['name']} saved: {file}. Send it to them; anyone with it can sign in as them.")
        os.startfile(Path(file).parent)

    def add_person(self) -> None:
        name, email = tk.StringVar(), tk.StringVar()
        names = [p["name"] for p in self.data["positions"]]
        position = tk.StringVar(value=self.data["default_position"])

        def add():
            result = self._call(self.app.cloud.add_member, name.get(), position.get(), email.get())
            if not result:
                return False
            self.data = result["admin"]
            self._fill()
            member = next(m for m in self.data["members"] if m["seat"] == result["seat"])
            self.win.after(100, lambda: self._save_seat(member, result["key"]))
        self._form("Add a person", [
            ("Name", lambda p: ttk.Entry(p, textvariable=name, width=30)),
            ("Email (optional)", lambda p: ttk.Entry(p, textvariable=email, width=30)),
            ("Position", lambda p: ttk.Combobox(p, textvariable=position, values=names, state="readonly",
                                                width=28))], "Add and save seat file", add)

    def move_person(self) -> None:
        member = self._member()
        if not member:
            return
        position = tk.StringVar(value=member["position"])

        def move():
            result = self._call(self.app.cloud.change_member, member["seat"], "position", position.get())
            if not result:
                return False
            self.data = result["admin"]
            self._fill()
        self._form(f"Position of {member['name']}", [
            ("Position", lambda p: ttk.Combobox(p, textvariable=position, state="readonly", width=28,
                                                values=[x["name"] for x in self.data["positions"]]))], "Save", move)

    def new_key(self) -> None:
        member = self._member()
        if not member or not messagebox.askyesno("FlowSME", f"Make a new key for {member['name']}? Their current "
                                                            "seat file stops working.", parent=self.win):
            return
        result = self._call(self.app.cloud.change_member, member["seat"], "new_key")
        if result:
            self.data = result["admin"]
            self._save_seat(member, result["key"])

    def status(self, action: str) -> None:
        member = self._member()
        if not member:
            return
        if action == "revoke" and not messagebox.askyesno(
                "FlowSME", f"Revoke {member['name']}'s seat? Their app stops working at once, and the seat is free "
                           "for someone else.", parent=self.win):
            return
        result = self._call(self.app.cloud.change_member, member["seat"], action)
        if result:
            self.data = result["admin"]
            self._fill()

    # ---- positions and rates ----------------------------------------------------------------------

    def _positions(self, tabs: ttk.Notebook) -> None:
        page = ttk.Frame(tabs, padding=12)
        tabs.add(page, text="Positions and rates")
        ttk.Label(page, style="Muted.TLabel", wraplength=int(620 * self.scale), justify="left", text=(
            "What an hour of each position costs the business (salary plus overheads). Your reports turn hours "
            "saved into money with the rate of the person's position; employees see their position's name only."
        )).pack(anchor="w", pady=(0, 8))
        self.rates = self._tree(page, (("name", "Position", 260), ("rate", "Cost of an hour", 120),
                                       ("default", "", 160)))
        self.rates.pack(fill="both", expand=True)
        row = ttk.Frame(page)
        row.pack(fill="x", pady=(8, 0))
        for text, command in (("Add...", lambda: self.edit_position(None)), ("Edit...", self.edit_selected),
                              ("Remove", self.remove_position), ("Default for new people", self.make_default)):
            ttk.Button(row, text=text, command=command).pack(side="left", padx=(0, 6))
        money = ttk.Frame(page)
        money.pack(fill="x", pady=(10, 0))
        ttk.Label(money, text="Currency symbol").pack(side="left")
        self.currency = tk.StringVar()
        box = ttk.Entry(money, textvariable=self.currency, width=6)
        box.pack(side="left", padx=6)
        ttk.Button(money, text="Save currency", command=lambda: self._put(self.data["positions"])).pack(side="left")

    def _put(self, positions: list[dict], default: str | None = None) -> bool:
        result = self._call(self.app.cloud.positions, positions, self.currency.get().strip() or "$",
                            default or self.data["default_position"])
        if result:
            self.data = result
            self._fill()
        return bool(result)

    def edit_position(self, old: dict | None) -> None:
        name = tk.StringVar(value=old["name"] if old else "")
        rate = tk.StringVar(value=f"{old['hourly_cost']:g}" if old else "")

        def save():
            try:
                value = float(rate.get())
            except ValueError:
                messagebox.showerror("FlowSME", "Enter the cost of an hour as a number.", parent=self.win)
                return False
            positions = [dict(p) for p in self.data["positions"]]
            if old:
                entry = next(p for p in positions if p["name"] == old["name"])
                entry.update(name=name.get().strip(), hourly_cost=value, was=old["name"])  # people keep it
            else:
                positions.append({"name": name.get().strip(), "hourly_cost": value})
            default = self.data["default_position"]
            if old and default == old["name"]:
                default = name.get().strip()
            return self._put(positions, default) or False
        self._form("Edit position" if old else "Add a position", [
            ("Position", lambda p: ttk.Entry(p, textvariable=name, width=30)),
            (f"Cost of an hour ({self.data['currency']})",
             lambda p: ttk.Spinbox(p, from_=0, to=100000, textvariable=rate, width=10))], "Save", save)

    def _position(self) -> dict | None:
        chosen = self.rates.selection()
        if not chosen:
            messagebox.showinfo("FlowSME", "Select a position first.", parent=self.win)
            return None
        return next(p for p in self.data["positions"] if p["name"] == chosen[0])

    def edit_selected(self) -> None:
        p = self._position()
        if p:
            self.edit_position(p)

    def remove_position(self) -> None:
        p = self._position()
        if p:
            self._put([x for x in self.data["positions"] if x["name"] != p["name"]])

    def make_default(self) -> None:
        p = self._position()
        if p:
            self._put(self.data["positions"], p["name"])

    def _fill(self) -> None:
        d = self.data
        self.seats.config(text=f"{d['used']} of {d['seats']} seats in use, {d['seats'] - d['used']} free "
                               "(FlowSME sets the number of seats in your plan)")
        self.people.delete(*self.people.get_children())
        for m in d["members"]:
            self.people.insert("", "end", iid=m["seat"], values=(m["name"], m["position"], m["role"], m["seat"],
                                                                m["status"], m["issued"][:10]))
        self.rates.delete(*self.rates.get_children())
        for p in d["positions"]:
            self.rates.insert("", "end", iid=p["name"], values=(
                p["name"], f"{d['currency']}{p['hourly_cost']:g}",
                "default for new people" if p["name"] == d["default_position"] else ""))
        self.currency.set(d["currency"])
