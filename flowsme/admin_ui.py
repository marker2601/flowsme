"""The Admin panel: people and seats, positions and their hourly rates, the team inbox and the team's settings.

A manager's panel is behind the admin PIN. Someone using FlowSME on their own gets the positions and rates, and
can set up a team or join one from here."""
from __future__ import annotations

import json
import os
import re
import shutil
import tkinter as tk
import zipfile
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from . import org
from .session import import_recording


def open_admin(app, tab: str | None = None) -> None:
    o = org.Org(app.home)
    if o.team and not app.unlocked:
        pin = simpledialog.askstring("Admin panel", f"Admin PIN for {o.team['name']}:", show="•", parent=app.root)
        if pin is None:
            return
        if not o.check_pin(pin.strip()):
            messagebox.showerror("FlowSME", "That PIN isn't right.", parent=app.root)
            return
        app.unlocked = True
    Admin(app, o, tab)


class Admin:
    def __init__(self, app, o: org.Org, tab: str | None):
        self.app, self.o = app, o
        self.scale = app.root.winfo_fpixels("1i") / 96
        self.win = tk.Toplevel(app.root)
        self.win.title(f"Admin panel: {o.team['name']}" if o.team else "Positions, rates and team")
        self.win.transient(app.root)
        self.win.bind("<Escape>", lambda _: self.close())
        self.win.protocol("WM_DELETE_WINDOW", self.close)
        frame = ttk.Frame(self.win, padding=16)
        frame.pack(fill="both", expand=True)
        self.tabs = ttk.Notebook(frame)
        self.tabs.pack(fill="both", expand=True)
        pages = {}
        if o.team:
            pages["people"] = self._people()
            pages["inbox"] = self._inbox()
        pages["positions"] = self._positions()
        pages["team"] = self._team()
        if tab in pages:
            self.tabs.select(pages[tab])
        bar = ttk.Frame(frame)
        bar.pack(fill="x", pady=(12, 0))
        ttk.Button(bar, text="Close", command=self.close).pack(side="right")
        self.win.update_idletasks()
        root = app.root
        x = root.winfo_rootx() + (root.winfo_width() - self.win.winfo_reqwidth()) // 2
        y = root.winfo_rooty() + max(0, (root.winfo_height() - self.win.winfo_reqheight()) // 3)
        self.win.geometry(f"+{max(x, 0)}+{max(y, 0)}")

    def close(self) -> None:
        self.win.destroy()
        self.app.refresh(select=self.app._selected())

    def _tree(self, parent, cols, height=8, mode="browse") -> ttk.Treeview:
        tree = ttk.Treeview(parent, columns=[c for c, _, _ in cols], show="headings", height=height,
                            selectmode=mode)
        for col, label, width in cols:
            tree.heading(col, text=label, anchor="w")
            tree.column(col, width=int(width * self.scale), anchor="w")
        return tree

    def _save(self) -> None:
        self.o.save()

    def _form(self, title: str, rows: list[tuple[str, tk.Widget]], ok: str, action) -> tk.Toplevel:
        """A small modal form over the panel: one label and one field per row."""
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
            try:
                action()
            except ValueError as exc:
                messagebox.showerror("FlowSME", str(exc), parent=win)
                return
            win.destroy()
        ttk.Button(bar, text=ok, style="Accent.TButton", command=go).pack(side="left")
        ttk.Button(bar, text="Cancel", command=win.destroy).pack(side="left", padx=(8, 0))
        win.bind("<Return>", lambda _: go())
        win.bind("<Escape>", lambda _: win.destroy())
        win.update_idletasks()
        win.geometry(f"+{self.win.winfo_rootx() + 60}+{self.win.winfo_rooty() + 60}")
        win.grab_set()
        return win

    # ---- people and seats -------------------------------------------------------------------------

    def _people(self) -> ttk.Frame:
        page = ttk.Frame(self.tabs, padding=12)
        self.tabs.add(page, text="People and seats")
        self.seats_label = ttk.Label(page, style="Heading.TLabel")
        self.seats_label.pack(anchor="w")
        ttk.Label(page, style="Muted.TLabel", wraplength=int(620 * self.scale), justify="left", text=(
            "Give each person a seat, then send them their seat file (by email or Teams). They open FlowSME, "
            "choose 'My manager sent me a seat file' and pick it. Their recordings come back marked with the seat, "
            "and this panel only accepts recordings from active seats. A seat file names the position, never its "
            "rate.")).pack(anchor="w", pady=(2, 8))
        self.people = self._tree(page, (("name", "Name", 150), ("position", "Position", 150), ("seat", "Seat", 110),
                                        ("status", "Status", 80), ("issued", "Since", 90), ("email", "Email", 150)))
        self.people.pack(fill="both", expand=True)
        row = ttk.Frame(page)
        row.pack(fill="x", pady=(8, 0))
        for text, command in (("Add person...", self.add_person), ("Change position...", self.move_person),
                              ("Save seat file...", self.save_seat), ("Revoke seat", self.revoke),
                              ("Restore seat", self.restore)):
            ttk.Button(row, text=text, command=command).pack(side="left", padx=(0, 6))
        self.people.bind("<Double-1>", lambda _: self.save_seat())
        self._fill_people()
        return page

    def _fill_people(self) -> None:
        o = self.o
        self.people.delete(*self.people.get_children())
        for m in o.members:
            self.people.insert("", "end", iid=m["seat"], values=(
                m["name"], m["position"], m["seat"], m["status"], m["issued"][:10], m.get("email", "")))
        free = o.team["seats"] - len(o.active())
        self.seats_label.config(text=f"{len(o.active())} of {o.team['seats']} seats in use, {free} free")

    def _member(self) -> dict | None:
        chosen = self.people.selection()
        if not chosen:
            messagebox.showinfo("FlowSME", "Select a person first.", parent=self.win)
            return None
        return self.o.member(chosen[0])

    def add_person(self) -> None:
        name, email = tk.StringVar(), tk.StringVar()
        position = tk.StringVar(value=self.o.default_position)
        names = self.o.position_names()

        def add() -> None:
            member = self.o.add_member(name.get(), position.get(), email.get())
            self._save()
            self._fill_people()
            self.people.selection_set(member["seat"])
            self.win.after(100, lambda: self.save_seat(member))
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

        def move() -> None:
            if position.get() not in self.o.position_names():
                raise ValueError("Choose one of the positions.")
            member["position"] = position.get()
            self._save()
            self._fill_people()
        self._form(f"Position of {member['name']}", [
            ("Position", lambda p: ttk.Combobox(p, textvariable=position, values=self.o.position_names(),
                                                state="readonly", width=28))], "Save", move)

    def save_seat(self, member: dict | None = None) -> None:
        member = member or self._member()
        if not member:
            return
        if member["status"] != "active":
            messagebox.showinfo("FlowSME", "This seat is revoked. Restore it first.", parent=self.win)
            return
        from .app import _desktop

        slug = re.sub(r"[^A-Za-z0-9]+", " ", member["name"]).strip() or "person"
        file = filedialog.asksaveasfilename(parent=self.win, title=f"Save {member['name']}'s seat file",
                                            initialdir=str(_desktop()), initialfile=f"{slug} - FlowSME seat.flowseat",
                                            defaultextension=".flowseat",
                                            filetypes=[("FlowSME seat", "*.flowseat")])
        if not file:
            return
        Path(file).write_text(json.dumps(self.o.seat_file(member), indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"Seat file for {member['name']} saved: {file}. Send it to them; they choose 'Join my team'.")
        os.startfile(Path(file).parent)

    def revoke(self) -> None:
        member = self._member()
        if not member or member["status"] != "active":
            return
        if not messagebox.askyesno("FlowSME", f"Revoke {member['name']}'s seat? Their recordings will no longer be "
                                              "accepted, and the seat is free to give to someone else.",
                                   parent=self.win):
            return
        self.o.revoke(member["seat"])
        self._save()
        self._fill_people()

    def restore(self) -> None:
        member = self._member()
        if not member:
            return
        try:
            self.o.restore(member["seat"])
        except ValueError as exc:
            messagebox.showerror("FlowSME", str(exc), parent=self.win)
            return
        self._save()
        self._fill_people()

    # ---- inbox ------------------------------------------------------------------------------------

    def _inbox(self) -> ttk.Frame:
        page = ttk.Frame(self.tabs, padding=12)
        self.tabs.add(page, text="Inbox")
        self.inbox_label = ttk.Label(page, style="Muted.TLabel", wraplength=int(620 * self.scale), justify="left")
        self.inbox_label.pack(anchor="w", pady=(0, 8))
        self.inbox = self._tree(page, (("from", "From", 150), ("recording", "Recording", 230),
                                       ("received", "Received", 120), ("check", "Seat check", 120)), mode="extended")
        self.inbox.pack(fill="both", expand=True)
        row = ttk.Frame(page)
        row.pack(fill="x", pady=(8, 0))
        ttk.Button(row, text="Import selected", style="Accent.TButton", command=self.import_selected).pack(
            side="left")
        ttk.Button(row, text="Refresh", command=self._fill_inbox).pack(side="left", padx=6)
        ttk.Button(row, text="Open inbox folder", command=lambda: self.o.folder and os.startfile(
            self.o.folder / "inbox")).pack(side="left")
        self._fill_inbox()
        return page

    def _check_text(self, zip_path: Path) -> tuple[str, str]:
        status, member = self.o.check_seat(org.seat_in_zip(zip_path))
        who = member["name"] if member else "?"
        return who, {"ok": "active seat", "none": "no seat (yours?)", "revoked": "seat revoked",
                     "forged": "not a real seat", "other team": "another team", "unknown": "unknown seat"}[status]

    def _fill_inbox(self) -> None:
        self.inbox.delete(*self.inbox.get_children())
        folder = self.o.folder
        if not folder:
            self.inbox_label.config(text="No team folder yet. Choose one on the Team tab (a OneDrive, SharePoint or "
                                         "network folder you and your team can reach) and employees' 'Send to my "
                                         "manager' drops recordings here. Without one, they email the zip and you "
                                         "use More > Import a recording.")
            return
        if not folder.exists():
            self.inbox_label.config(text=f"Can't reach the team folder {folder}. Is it synced or connected?")
            return
        self.inbox_label.config(text=f"Recordings employees sent to {folder / 'inbox'}. Importing checks each one "
                                     "was made on an active seat of this team.")
        for zip_path in self.o.inbox():
            who, check = self._check_text(zip_path)
            when = datetime.fromtimestamp(zip_path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
            self.inbox.insert("", "end", iid=str(zip_path), values=(who, zip_path.stem, when, check))

    def import_selected(self) -> None:
        chosen = [Path(i) for i in self.inbox.selection()]
        if not chosen:
            messagebox.showinfo("FlowSME", "Select one or more recordings.", parent=self.win)
            return
        last, refused = None, []
        for zip_path in chosen:
            try:
                last = import_checked(self.o, zip_path, self.app.cfg.sessions_dir)
            except ValueError as exc:
                refused.append(f"{zip_path.stem}: {exc}")
                continue
            done = zip_path.parent / "imported"
            done.mkdir(exist_ok=True)
            shutil.move(str(zip_path), str(done / zip_path.name))
            print(f"Imported '{zip_path.stem}'.")
        self._fill_inbox()
        self.app.refresh(select=last)
        if refused:
            messagebox.showwarning("FlowSME", "Not imported:\n\n" + "\n".join(refused), parent=self.win)

    # ---- positions and rates ----------------------------------------------------------------------

    def _positions(self) -> ttk.Frame:
        page = ttk.Frame(self.tabs, padding=12)
        self.tabs.add(page, text="Positions and rates")
        ttk.Label(page, style="Muted.TLabel", wraplength=int(620 * self.scale), justify="left", text=(
            "What an hour of each position costs the business (salary plus overheads). Reports turn the hours "
            "saved into money with the rate of the person's position. "
            + ("Employees see their position's name, never a rate: their own reports show hours only, and the "
               "money appears when you make the report here." if self.o.team else
               "The rates below are examples: change them to your own."))).pack(anchor="w", pady=(0, 8))
        self.positions = self._tree(page, (("name", "Position", 260), ("rate", "Cost of an hour", 120),
                                           ("people", "People", 70), ("default", "", 140)))
        self.positions.pack(fill="both", expand=True)
        row = ttk.Frame(page)
        row.pack(fill="x", pady=(8, 0))
        for text, command in (("Add...", self.add_position), ("Edit...", self.edit_position),
                              ("Remove", self.remove_position), ("Use for my recordings", self.default_position)):
            ttk.Button(row, text=text, command=command).pack(side="left", padx=(0, 6))
        self.positions.bind("<Double-1>", lambda _: self.edit_position())
        money = ttk.Frame(page)
        money.pack(fill="x", pady=(10, 0))
        ttk.Label(money, text="Currency symbol").pack(side="left")
        self.currency = tk.StringVar(value=self.o.currency)
        box = ttk.Entry(money, textvariable=self.currency, width=6)
        box.pack(side="left", padx=6)
        box.bind("<FocusOut>", lambda _: self._set_currency())
        box.bind("<Return>", lambda _: self._set_currency())
        self._fill_positions()
        return page

    def _set_currency(self) -> None:
        value = self.currency.get().strip() or "$"
        if value != self.o.currency:
            self.o.data["currency"] = value
            self._save()
            self._fill_positions()

    def _fill_positions(self) -> None:
        o = self.o
        self.positions.delete(*self.positions.get_children())
        for p in o.positions:
            people = sum(1 for m in o.members if m["position"] == p["name"] and m["status"] == "active")
            self.positions.insert("", "end", iid=p["name"], values=(
                p["name"], f"{o.currency}{p['hourly_cost']:g}", people,
                "used for my recordings" if p["name"] == o.default_position else ""))

    def _position_form(self, title: str, old: dict | None) -> None:
        name = tk.StringVar(value=old["name"] if old else "")
        rate = tk.StringVar(value=f"{old['hourly_cost']:g}" if old else "")

        def save() -> None:
            try:
                value = float(rate.get())
            except ValueError:
                raise ValueError("Enter the cost of an hour as a number.") from None
            self.o.set_position(name.get(), value, old["name"] if old else None)
            self._save()
            self._fill_positions()
            if self.o.team:
                self._fill_people()
        self._form(title, [("Position", lambda p: ttk.Entry(p, textvariable=name, width=30)),
                           (f"Cost of an hour ({self.o.currency})",
                            lambda p: ttk.Spinbox(p, from_=0, to=100000, textvariable=rate, width=10))], "Save", save)

    def _position(self) -> dict | None:
        chosen = self.positions.selection()
        if not chosen:
            messagebox.showinfo("FlowSME", "Select a position first.", parent=self.win)
            return None
        return next(p for p in self.o.positions if p["name"] == chosen[0])

    def add_position(self) -> None:
        self._position_form("Add a position", None)

    def edit_position(self) -> None:
        p = self._position()
        if p:
            self._position_form(f"Edit {p['name']}", p)

    def remove_position(self) -> None:
        p = self._position()
        if not p:
            return
        try:
            self.o.remove_position(p["name"])
        except ValueError as exc:
            messagebox.showerror("FlowSME", str(exc), parent=self.win)
            return
        self._save()
        self._fill_positions()

    def default_position(self) -> None:
        p = self._position()
        if p:
            self.o.data["default_position"] = p["name"]
            self._save()
            self._fill_positions()

    # ---- the team ---------------------------------------------------------------------------------

    def _team(self) -> ttk.Frame:
        page = ttk.Frame(self.tabs, padding=12)
        self.tabs.add(page, text="Team")
        if self.o.team:
            self._team_settings(page)
        else:
            self._team_setup(page)
        return page

    def _folder_row(self, parent, row: int, folder: tk.StringVar) -> None:
        ttk.Label(parent, text="Team folder (optional)").grid(row=row, column=0, sticky="w", pady=3)
        place = ttk.Frame(parent)
        place.grid(row=row, column=1, sticky="w", padx=(8, 0))
        ttk.Entry(place, textvariable=folder, width=40).pack(side="left")
        ttk.Button(place, text="Choose...", command=lambda: folder.set(
            filedialog.askdirectory(parent=self.win, title="A folder you and your team can all reach") or
            folder.get())).pack(side="left", padx=6)
        ttk.Label(parent, style="Muted.TLabel", wraplength=int(560 * self.scale), justify="left", text=(
            "A OneDrive, SharePoint or network folder everyone can reach. Employees' 'Send to my manager' drops "
            "recordings in it, and their apps read which seats are active from it. It never holds rates.")).grid(
            row=row + 1, column=0, columnspan=2, sticky="w")

    def _team_setup(self, page: ttk.Frame) -> None:
        ttk.Label(page, text="Set up a team", style="Heading.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(page, style="Muted.TLabel", wraplength=int(560 * self.scale), justify="left", text=(
            "As the manager you hand out seats to the people who will record, set the positions and their rates, "
            "and make the reports with money in them. The PIN keeps this panel for you.")).grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(2, 10))
        name, seats, pin, again, folder = (tk.StringVar(), tk.StringVar(value="5"), tk.StringVar(), tk.StringVar(),
                                           tk.StringVar())
        for n, (label, widget) in enumerate((
                ("Team name", ttk.Entry(page, textvariable=name, width=30)),
                ("Seats in your plan", ttk.Spinbox(page, from_=1, to=10000, textvariable=seats, width=8)),
                ("Admin PIN (4+ digits)", ttk.Entry(page, textvariable=pin, show="•", width=12)),
                ("PIN again", ttk.Entry(page, textvariable=again, show="•", width=12))), 2):
            ttk.Label(page, text=label).grid(row=n, column=0, sticky="w", pady=3)
            widget.grid(row=n, column=1, sticky="w", padx=(8, 0), pady=3)
        self._folder_row(page, 6, folder)

        def create() -> None:
            if pin.get() != again.get():
                messagebox.showerror("FlowSME", "The two PINs differ.", parent=self.win)
                return
            try:
                self.o.create_team(name.get(), pin.get().strip(), int(float(seats.get() or 0)), folder.get())
            except ValueError as exc:
                messagebox.showerror("FlowSME", str(exc), parent=self.win)
                return
            self._save()
            self.app.unlocked = True
            print(f"Team '{self.o.team['name']}' set up. Add people in the Admin panel to give them seats.")
            self.win.destroy()
            open_admin(self.app, "people")

        bar = ttk.Frame(page)
        bar.grid(row=8, column=0, columnspan=2, sticky="w", pady=(14, 0))
        ttk.Button(bar, text="Create team", style="Accent.TButton", command=create).pack(side="left")
        ttk.Button(bar, text="Join a team instead...", command=lambda: (self.win.destroy(),
                                                                       self.app.join_team())).pack(side="left",
                                                                                                  padx=8)

    def _team_settings(self, page: ttk.Frame) -> None:
        team = self.o.team
        name, seats = tk.StringVar(value=team["name"]), tk.StringVar(value=str(team["seats"]))
        folder, reports = tk.StringVar(value=team.get("folder", "")), tk.BooleanVar(
            value=team.get("employees_make_reports", True))
        ttk.Label(page, text=f"Team {team['id']}", style="Heading.TLabel").grid(row=0, column=0, columnspan=2,
                                                                                 sticky="w", pady=(0, 8))
        for n, (label, widget) in enumerate((
                ("Team name", ttk.Entry(page, textvariable=name, width=30)),
                ("Seats in your plan", ttk.Spinbox(page, from_=1, to=10000, textvariable=seats, width=8))), 1):
            ttk.Label(page, text=label).grid(row=n, column=0, sticky="w", pady=3)
            widget.grid(row=n, column=1, sticky="w", padx=(8, 0), pady=3)
        self._folder_row(page, 3, folder)
        ttk.Checkbutton(page, variable=reports, text="Employees can make their own reports (hours only, with their "
                        "own AI key or the built-in rules)").grid(row=5, column=0, columnspan=2, sticky="w",
                                                                  pady=(10, 0))

        def save() -> None:
            try:
                wanted = int(float(seats.get()))
            except ValueError:
                wanted = 0
            if wanted < max(1, len(self.o.active())):
                messagebox.showerror("FlowSME", f"{len(self.o.active())} seats are in use: the plan needs at least "
                                                "that many. Revoke seats first.", parent=self.win)
                return
            if not name.get().strip():
                messagebox.showerror("FlowSME", "Give the team a name.", parent=self.win)
                return
            team.update(name=name.get().strip(), seats=wanted, folder=folder.get().strip(),
                        employees_make_reports=reports.get())
            self._save()
            published = self.o.folder and self.o.folder.exists()
            print("Team settings saved" + (f"; seats published to {self.o.folder}." if published else "."))
            self._fill_people()
            self._fill_inbox()

        def change_pin() -> None:
            new = simpledialog.askstring("Admin PIN", "New admin PIN (4+ digits):", show="•", parent=self.win)
            if new is None:
                return
            if len(new.strip()) < 4 or not new.strip().isdigit():
                messagebox.showerror("FlowSME", "The admin PIN needs at least 4 digits.", parent=self.win)
                return
            self.o.set_pin(new.strip())
            self._save()
            messagebox.showinfo("FlowSME", "Admin PIN changed.", parent=self.win)

        bar = ttk.Frame(page)
        bar.grid(row=6, column=0, columnspan=2, sticky="w", pady=(14, 0))
        ttk.Button(bar, text="Save", style="Accent.TButton", command=save).pack(side="left")
        ttk.Button(bar, text="Change admin PIN...", command=change_pin).pack(side="left", padx=8)
        ttk.Label(page, style="Muted.TLabel", wraplength=int(560 * self.scale), justify="left", text=(
            "Seat files already sent keep working after you change these. With a team folder, employees' apps "
            "pick up revoked seats and the reports setting from it.")).grid(row=7, column=0, columnspan=2,
                                                                           sticky="w", pady=(10, 0))


def import_checked(o: org.Org, zip_path: Path, sessions_dir: Path) -> Path:
    """Import a recording only if it was made on an active seat of this team (or on no seat at all)."""
    status, member = o.check_seat(org.seat_in_zip(zip_path))
    reasons = {"revoked": f"made on {member['name'] if member else 'a'}'s seat, which is revoked",
               "forged": "its seat isn't one this team issued", "other team": "it was made for another team",
               "unknown": "its seat isn't in this team's list"}
    if status in reasons:
        raise ValueError(reasons[status])
    try:
        return import_recording(zip_path, sessions_dir)
    except zipfile.BadZipFile:
        raise ValueError("not a zip file") from None
