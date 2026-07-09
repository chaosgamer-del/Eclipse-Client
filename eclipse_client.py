import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
import minecraft_launcher_lib
import subprocess
import os
import sys
import json
import uuid
import threading
import shutil
import queue
import webbrowser
import re
import io
import requests
from pathlib import Path
from PIL import Image, ImageTk
from minecraft_launcher_lib.utils import get_available_versions, get_installed_versions
from minecraft_launcher_lib.command import get_minecraft_command
from minecraft_launcher_lib.install import install_minecraft_version
from minecraft_launcher_lib import fabric, forge, quilt

# ── Theme (solar eclipse) ────────────────────────────────────────────────────
C = {
    "bg": "#08060f",
    "bg2": "#100c1c",
    "bg3": "#181225",
    "panel": "#14101f",
    "border": "#2a2040",
    "corona": "#ffb347",
    "corona_dim": "#c4782a",
    "moon": "#e8e6f0",
    "moon_dim": "#9a96a8",
    "accent": "#ff7a3d",
    "success": "#5dffb0",
    "warn": "#ffcc66",
    "error": "#ff6b7a",
    "input_bg": "#1c1628",
    "list_bg": "#120e1c",
    "list_sel": "#3d2a18",
    "card": "#161022",
    "card_hover": "#1e1630",
}

ELY_AUTH = "https://authserver.ely.by/auth/authenticate"
ELY_REFRESH = "https://authserver.ely.by/auth/refresh"
ELY_VALIDATE = "https://authserver.ely.by/auth/validate"
MODRINTH_API = "https://api.modrinth.com/v2"
AUTHLIB_RELEASES = "https://api.github.com/repos/yushijinhun/authlib-injector/releases/latest"
USER_AGENT = "EclipseClient/1.2 (https://github.com/eclipse-client)"
MODS_PER_PAGE = 18
ICON_SIZE = 72
LOADER_MARKERS = ("fabric", "forge", "quilt", "neoforge", "liteloader", "rift")
LOADER_OFFER_LIMIT = 45  # max recent releases to offer loader installs for


def format_uuid(raw: str) -> str:
    raw = (raw or "").replace("-", "").lower()
    if len(raw) != 32:
        return str(uuid.uuid4())
    return f"{raw[:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:]}"


def safe_filename(name: str) -> str:
    return "".join(c for c in name if c.isalnum() or c in "._- ").strip() or "mod.jar"


def safe_pack_name(name: str) -> str:
    cleaned = re.sub(r"[^\w\- ]+", "", name).strip()
    return cleaned or f"Pack-{uuid.uuid4().hex[:6]}"


def is_modded_version_id(vid: str) -> bool:
    low = (vid or "").lower()
    return any(m in low for m in LOADER_MARKERS)


def loader_label_from_id(vid: str) -> str:
    low = (vid or "").lower()
    for m in ("neoforge", "fabric", "forge", "quilt", "liteloader"):
        if m in low:
            return m.capitalize() if m != "neoforge" else "NeoForge"
    return "Modded"


class EclipseClient:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Eclipse Client")
        self.root.geometry("1120x820")
        self.root.minsize(960, 720)
        self.root.configure(bg=C["bg"])
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        if getattr(sys, "frozen", False):
            self.app_dir = Path(sys.executable).resolve().parent
        else:
            self.app_dir = Path(__file__).resolve().parent

        self.minecraft_dir = self.app_dir / ".minecraft"
        self.minecraft_dir.mkdir(parents=True, exist_ok=True)
        (self.minecraft_dir / "mods").mkdir(exist_ok=True)
        self.instances_dir = self.minecraft_dir / "instances"
        self.instances_dir.mkdir(exist_ok=True)
        self.icon_cache_dir = self.minecraft_dir / "eclipse_cache" / "icons"
        self.icon_cache_dir.mkdir(parents=True, exist_ok=True)

        self.config_path = self.app_dir / "eclipse_config.json"
        self.authlib_path = self.app_dir / "authlib-injector.jar"

        self.current_version = None  # version id or mp:<id>
        self.selected_entry = None  # internal dict for list selection
        self.options = None
        self.auth_mode = None
        self.session = {}
        self.status_queue = queue.Queue()
        self._mod_install_lock = threading.Lock()

        # Modrinth search state
        self.mod_results = []
        self.mod_page = 0
        self.mod_total = 0
        self.mod_query = ""
        self._photo_refs = []  # keep PhotoImage alive
        self._placeholder_icon = None

        self._setup_styles()
        self._load_config()
        self._make_placeholder_icon()
        self.create_ui()
        self.root.after(80, self.process_queue)
        self.root.after(200, self.refresh_versions)
        self.root.after(300, self._try_restore_session)
        self.root.after(400, self.refresh_installed_mods)
        self.root.after(500, self._refresh_modpack_combo)
        self.root.after(600, lambda: self.search_mods(reset_page=True))

        try:
            icon = self.app_dir / "icon.ico"
            if icon.exists():
                self.root.iconbitmap(str(icon))
        except Exception:
            pass

    # ── Styles ───────────────────────────────────────────────────────────────
    def _setup_styles(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass

        style.configure(".", background=C["bg"], foreground=C["moon"], fieldbackground=C["input_bg"])
        style.configure("TFrame", background=C["bg"])
        style.configure("TLabel", background=C["bg"], foreground=C["moon"])
        style.configure(
            "TNotebook",
            background=C["bg"],
            borderwidth=0,
            tabmargins=(8, 6, 8, 0),
        )
        style.configure(
            "TNotebook.Tab",
            background=C["bg3"],
            foreground=C["moon_dim"],
            padding=(18, 8),
            font=("Segoe UI", 10, "bold"),
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", C["panel"]), ("active", C["bg2"])],
            foreground=[("selected", C["corona"]), ("active", C["moon"])],
        )
        style.configure(
            "TProgressbar",
            troughcolor=C["bg3"],
            background=C["corona"],
            bordercolor=C["border"],
            lightcolor=C["corona"],
            darkcolor=C["corona_dim"],
            thickness=10,
        )
        style.configure(
            "Vertical.TScrollbar",
            background=C["bg3"],
            troughcolor=C["bg"],
            bordercolor=C["border"],
            arrowcolor=C["corona"],
        )
        style.configure(
            "Horizontal.TScrollbar",
            background=C["bg3"],
            troughcolor=C["bg"],
            bordercolor=C["border"],
            arrowcolor=C["corona"],
        )
        style.configure(
            "TCombobox",
            fieldbackground=C["input_bg"],
            background=C["bg3"],
            foreground=C["moon"],
            arrowcolor=C["corona"],
        )
        style.map("TCombobox", fieldbackground=[("readonly", C["input_bg"])], foreground=[("readonly", C["moon"])])

    def _btn(self, parent, text, command, kind="default", **kw):
        colors = {
            "default": (C["bg3"], C["moon"], C["border"]),
            "primary": (C["corona_dim"], "#1a0a00", C["corona"]),
            "play": ("#1a4a2a", C["success"], "#2a7a45"),
            "danger": ("#4a1520", C["error"], "#7a2030"),
            "ely": ("#2a1840", "#d4b8ff", "#5a3080"),
            "mod": ("#1a3040", "#8fd4ff", "#2a5070"),
        }
        bg, fg, active = colors.get(kind, colors["default"])
        opts = {
            "text": text,
            "command": command,
            "bg": bg,
            "fg": fg,
            "activebackground": active,
            "activeforeground": fg,
            "relief": "flat",
            "bd": 0,
            "padx": 12,
            "pady": 6,
            "font": ("Segoe UI", 9, "bold"),
            "cursor": "hand2",
            "highlightthickness": 1,
            "highlightbackground": C["border"],
            "highlightcolor": C["corona"],
        }
        opts.update(kw)
        return tk.Button(parent, **opts)

    def _entry(self, parent, textvariable=None, show=None, width=None):
        return tk.Entry(
            parent,
            textvariable=textvariable,
            show=show,
            width=width,
            bg=C["input_bg"],
            fg=C["moon"],
            insertbackground=C["corona"],
            relief="flat",
            font=("Segoe UI", 10),
            highlightthickness=1,
            highlightbackground=C["border"],
            highlightcolor=C["corona"],
        )

    def _listbox(self, parent, height=12):
        return tk.Listbox(
            parent,
            font=("Consolas", 10),
            bg=C["list_bg"],
            fg=C["moon"],
            selectbackground=C["list_sel"],
            selectforeground=C["corona"],
            activestyle="none",
            relief="flat",
            highlightthickness=1,
            highlightbackground=C["border"],
            height=height,
            exportselection=False,
        )

    def _make_placeholder_icon(self):
        img = Image.new("RGBA", (ICON_SIZE, ICON_SIZE), (40, 28, 60, 255))
        self._placeholder_icon = ImageTk.PhotoImage(img)

    # ── Config ───────────────────────────────────────────────────────────────
    def _load_config(self):
        self.config = {
            "client_token": str(uuid.uuid4()),
            "last_version": None,
            "session": None,
            "modpacks": [],
            "active_modpack_id": None,
        }
        try:
            if self.config_path.exists():
                data = json.loads(self.config_path.read_text(encoding="utf-8"))
                self.config.update(data)
        except Exception:
            pass
        if not self.config.get("client_token"):
            self.config["client_token"] = str(uuid.uuid4())
        if "modpacks" not in self.config or not isinstance(self.config["modpacks"], list):
            self.config["modpacks"] = []

    def _save_config(self):
        try:
            self.config["last_version"] = self.current_version
            self.config_path.write_text(json.dumps(self.config, indent=2), encoding="utf-8")
        except Exception:
            pass

    def get_modpacks(self):
        return list(self.config.get("modpacks") or [])

    def get_modpack(self, pack_id):
        for p in self.get_modpacks():
            if p.get("id") == pack_id:
                return p
        return None

    def get_active_modpack(self):
        return self.get_modpack(self.config.get("active_modpack_id"))

    def mods_target_dir(self) -> Path:
        pack = self.get_active_modpack()
        if pack:
            d = self.instances_dir / pack["folder"] / "mods"
            d.mkdir(parents=True, exist_ok=True)
            return d
        d = self.minecraft_dir / "mods"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _try_restore_session(self):
        sess = self.config.get("session")
        if not sess or sess.get("mode") != "ely":
            return
        threading.Thread(target=self._restore_ely_session, args=(sess,), daemon=True).start()

    def _restore_ely_session(self, sess):
        try:
            r = requests.post(
                ELY_VALIDATE,
                json={"accessToken": sess.get("accessToken")},
                headers={"User-Agent": USER_AGENT, "Content-Type": "application/json"},
                timeout=15,
            )
            access = sess.get("accessToken")
            client = sess.get("clientToken") or self.config["client_token"]
            if r.status_code != 200:
                r2 = requests.post(
                    ELY_REFRESH,
                    json={"accessToken": access, "clientToken": client, "requestUser": True},
                    headers={"User-Agent": USER_AGENT, "Content-Type": "application/json"},
                    timeout=15,
                )
                if r2.status_code != 200:
                    self.set_status("Ely.by session expired — please log in again")
                    return
                data = r2.json()
                access = data["accessToken"]
                profile = data.get("selectedProfile") or {}
                sess = {
                    "mode": "ely",
                    "accessToken": access,
                    "clientToken": data.get("clientToken", client),
                    "username": profile.get("name") or sess.get("username"),
                    "uuid": format_uuid(profile.get("id") or sess.get("uuid", "")),
                }
                self.config["session"] = sess
                self._save_config()

            self.session = sess
            self.auth_mode = "ely"
            self.options = {
                "username": sess["username"],
                "uuid": format_uuid(sess["uuid"]),
                "token": sess["accessToken"],
                "launcherName": "EclipseClient",
                "launcherVersion": "1.2",
            }
            self.root.after(
                0,
                lambda: self.login_lbl.config(text=f"Ely.by · {sess['username']}", fg=C["success"]),
            )
            self.set_status(f"Restored Ely.by session for {sess['username']}")
        except Exception as e:
            self.set_status(f"Session restore failed: {str(e)[:80]}")

    # ── UI shell ─────────────────────────────────────────────────────────────
    def create_ui(self):
        header = tk.Frame(self.root, bg=C["bg"])
        header.pack(fill="x", padx=16, pady=(12, 4))
        left_h = tk.Frame(header, bg=C["bg"])
        left_h.pack(side="left")
        tk.Label(
            left_h,
            text="☀  ECLIPSE CLIENT",
            font=("Segoe UI", 22, "bold"),
            fg=C["corona"],
            bg=C["bg"],
        ).pack(anchor="w")
        tk.Label(
            left_h,
            text="Solar-themed Minecraft launcher  ·  Ely.by  ·  Modrinth",
            font=("Segoe UI", 9),
            fg=C["moon_dim"],
            bg=C["bg"],
        ).pack(anchor="w")

        tk.Frame(self.root, bg=C["corona_dim"], height=2).pack(fill="x", padx=16, pady=(0, 8))

        self.nb = ttk.Notebook(self.root)
        self.nb.pack(fill="both", expand=True, padx=12, pady=(0, 8))

        self.tab_versions = tk.Frame(self.nb, bg=C["bg"])
        self.tab_mods = tk.Frame(self.nb, bg=C["bg"])
        self.tab_account = tk.Frame(self.nb, bg=C["bg"])
        self.nb.add(self.tab_versions, text="  Versions  ")
        self.nb.add(self.tab_mods, text="  Modrinth Mods  ")
        self.nb.add(self.tab_account, text="  Account  ")

        self._build_versions_tab()
        self._build_mods_tab()
        self._build_account_tab()
        self._build_bottom_bar()

    def _build_versions_tab(self):
        top = tk.Frame(self.tab_versions, bg=C["bg"])
        top.pack(fill="x", padx=12, pady=10)

        tk.Label(top, text="Filter", bg=C["bg"], fg=C["moon_dim"], font=("Segoe UI", 9)).pack(
            side="left", padx=(0, 8)
        )
        self.filter_var = tk.StringVar(value="all")
        for value, label in [
            ("all", "All"),
            ("release", "Release"),
            ("snapshot", "Snapshot"),
            ("old_alpha", "Alpha"),
            ("old_beta", "Beta"),
            ("modded", "Modded"),
        ]:
            tk.Radiobutton(
                top,
                text=label,
                variable=self.filter_var,
                value=value,
                bg=C["bg"],
                fg=C["moon"],
                selectcolor=C["bg3"],
                activebackground=C["bg"],
                activeforeground=C["corona"],
                command=self.refresh_versions,
                font=("Segoe UI", 9),
            ).pack(side="left", padx=6)

        list_wrap = tk.Frame(self.tab_versions, bg=C["bg"])
        list_wrap.pack(fill="both", expand=True, padx=12, pady=4)

        self.vlist = self._listbox(list_wrap, height=18)
        sc = ttk.Scrollbar(list_wrap, orient="vertical", command=self.vlist.yview)
        self.vlist.config(yscrollcommand=sc.set)
        self.vlist.pack(side="left", fill="both", expand=True)
        sc.pack(side="right", fill="y")
        self.vlist.bind("<<ListboxSelect>>", self._on_version_select)
        self.vlist.bind("<Double-Button-1>", lambda e: self.install_selected())

        btnf = tk.Frame(self.tab_versions, bg=C["bg"])
        btnf.pack(fill="x", padx=12, pady=10)
        self._btn(btnf, "↻ Refresh", self.refresh_versions).pack(side="left", padx=4)
        self._btn(btnf, "⬇ Install Selected", self.install_selected, kind="primary").pack(side="left", padx=4)
        self._btn(btnf, "🗑 Uninstall", self.uninstall_selected, kind="danger").pack(side="left", padx=4)

        self.version_info = tk.Label(
            btnf,
            text="Select a version to install or play",
            bg=C["bg"],
            fg=C["moon_dim"],
            font=("Segoe UI", 9),
        )
        self.version_info.pack(side="right", padx=8)

    def _build_mods_tab(self):
        # Top search + pack bar
        top = tk.Frame(self.tab_mods, bg=C["bg"])
        top.pack(fill="x", padx=10, pady=(10, 4))

        self.mod_search_var = tk.StringVar()
        entry = self._entry(top, textvariable=self.mod_search_var)
        entry.pack(side="left", fill="x", expand=True, padx=(0, 6), ipady=6)
        entry.bind("<Return>", lambda e: self.search_mods(reset_page=True))

        tk.Label(top, text="Loader", bg=C["bg"], fg=C["moon_dim"]).pack(side="left", padx=(4, 4))
        self.loader_var = tk.StringVar(value="fabric")
        ttk.Combobox(
            top,
            textvariable=self.loader_var,
            values=["fabric", "forge", "neoforge", "quilt", "any"],
            width=10,
            state="readonly",
        ).pack(side="left", padx=2)

        self._btn(top, "🔍 Search", lambda: self.search_mods(reset_page=True), kind="mod").pack(
            side="left", padx=6
        )

        # Modpack controls
        pack_row = tk.Frame(self.tab_mods, bg=C["bg2"])
        pack_row.pack(fill="x", padx=10, pady=(4, 8))

        tk.Label(
            pack_row,
            text="Modpack",
            bg=C["bg2"],
            fg=C["corona"],
            font=("Segoe UI", 9, "bold"),
        ).pack(side="left", padx=(8, 6), pady=8)

        self.pack_combo_var = tk.StringVar(value="(global mods folder)")
        self.pack_combo = ttk.Combobox(
            pack_row,
            textvariable=self.pack_combo_var,
            state="readonly",
            width=36,
        )
        self.pack_combo.pack(side="left", padx=4, pady=8)
        self.pack_combo.bind("<<ComboboxSelected>>", self._on_pack_combo)

        self._btn(pack_row, "＋ New modpack", self.create_modpack_dialog, kind="primary").pack(
            side="left", padx=6
        )
        self._btn(pack_row, "Select for play", self.select_active_pack_for_play).pack(side="left", padx=2)
        self._btn(pack_row, "Delete pack", self.delete_active_modpack, kind="danger").pack(side="left", padx=2)

        self.pack_status = tk.Label(
            pack_row,
            text="Installing mods → global .minecraft/mods",
            bg=C["bg2"],
            fg=C["moon_dim"],
            font=("Segoe UI", 8),
        )
        self.pack_status.pack(side="right", padx=10)

        # Body: left sidebar + grid
        body = tk.Frame(self.tab_mods, bg=C["bg"])
        body.pack(fill="both", expand=True, padx=10, pady=2)

        # Left installed sidebar
        side = tk.Frame(body, bg=C["panel"], width=200)
        side.pack(side="left", fill="y", padx=(0, 8))
        side.pack_propagate(False)

        tk.Label(
            side,
            text="Installed",
            bg=C["panel"],
            fg=C["corona"],
            font=("Segoe UI", 10, "bold"),
        ).pack(anchor="w", padx=10, pady=(10, 4))

        self.installed_list = self._listbox(side, height=22)
        self.installed_list.pack(fill="both", expand=True, padx=8, pady=4)
        self.installed_list.config(font=("Segoe UI", 8))

        sbtn = tk.Frame(side, bg=C["panel"])
        sbtn.pack(fill="x", padx=6, pady=8)
        self._btn(sbtn, "Remove", self.remove_installed_mod, kind="danger", padx=6, pady=3).pack(
            fill="x", pady=2
        )
        self._btn(sbtn, "Folder", self.open_mods_folder, padx=6, pady=3).pack(fill="x", pady=2)
        self._btn(sbtn, "Refresh", self.refresh_installed_mods, padx=6, pady=3).pack(fill="x", pady=2)

        # Right: scrollable card grid
        right = tk.Frame(body, bg=C["bg"])
        right.pack(side="left", fill="both", expand=True)

        canvas_frame = tk.Frame(right, bg=C["bg"])
        canvas_frame.pack(fill="both", expand=True)

        self.mod_canvas = tk.Canvas(canvas_frame, bg=C["bg"], highlightthickness=0, bd=0)
        self.mod_scroll = ttk.Scrollbar(canvas_frame, orient="vertical", command=self.mod_canvas.yview)
        self.mod_canvas.configure(yscrollcommand=self.mod_scroll.set)
        self.mod_scroll.pack(side="right", fill="y")
        self.mod_canvas.pack(side="left", fill="both", expand=True)

        self.mod_grid = tk.Frame(self.mod_canvas, bg=C["bg"])
        self._grid_window = self.mod_canvas.create_window((0, 0), window=self.mod_grid, anchor="nw")

        def _on_grid_configure(_e=None):
            self.mod_canvas.configure(scrollregion=self.mod_canvas.bbox("all"))

        def _on_canvas_configure(e):
            self.mod_canvas.itemconfig(self._grid_window, width=e.width)

        self.mod_grid.bind("<Configure>", _on_grid_configure)
        self.mod_canvas.bind("<Configure>", _on_canvas_configure)
        self.mod_canvas.bind_all("<MouseWheel>", self._on_mousewheel)

        # Pagination
        pager = tk.Frame(right, bg=C["panel"])
        pager.pack(fill="x", pady=(6, 0))
        self._btn(pager, "◀ Previous", self.prev_mod_page).pack(side="left", padx=8, pady=8)
        self.page_lbl = tk.Label(
            pager,
            text="Page 1",
            bg=C["panel"],
            fg=C["moon"],
            font=("Segoe UI", 9, "bold"),
        )
        self.page_lbl.pack(side="left", expand=True)
        self._btn(pager, "Next ▶", self.next_mod_page).pack(side="right", padx=8, pady=8)

    def _on_mousewheel(self, event):
        try:
            if self.nb.index(self.nb.select()) == self.nb.index(self.tab_mods):
                self.mod_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        except Exception:
            pass

    def _build_account_tab(self):
        wrap = tk.Frame(self.tab_account, bg=C["bg"])
        wrap.pack(fill="both", expand=True, padx=24, pady=16)

        off = tk.LabelFrame(
            wrap,
            text="  Offline play  ",
            bg=C["panel"],
            fg=C["moon_dim"],
            font=("Segoe UI", 10, "bold"),
            padx=12,
            pady=12,
            labelanchor="n",
        )
        off.pack(fill="x", pady=(0, 14))
        tk.Label(
            off,
            text="Play singleplayer or offline servers. No account required.",
            bg=C["panel"],
            fg=C["moon_dim"],
            font=("Segoe UI", 9),
        ).pack(anchor="w")
        orow = tk.Frame(off, bg=C["panel"])
        orow.pack(fill="x", pady=8)
        tk.Label(orow, text="Username", bg=C["panel"], fg=C["moon"]).pack(side="left")
        self.offline_user = tk.StringVar(value="EclipsePlayer")
        self._entry(orow, textvariable=self.offline_user, width=28).pack(side="left", padx=10, ipady=4)
        self._btn(orow, "Use Offline", self.offline_login).pack(side="left", padx=4)

        ely = tk.LabelFrame(
            wrap,
            text="  Ely.by login  ",
            bg=C["panel"],
            fg=C["corona"],
            font=("Segoe UI", 10, "bold"),
            padx=12,
            pady=12,
            labelanchor="n",
        )
        ely.pack(fill="x", pady=(0, 14))
        tk.Label(
            ely,
            text="Real Ely.by authentication (token + authlib-injector for skins / Ely servers).",
            bg=C["panel"],
            fg=C["moon_dim"],
            font=("Segoe UI", 9),
            wraplength=860,
            justify="left",
        ).pack(anchor="w")

        form = tk.Frame(ely, bg=C["panel"])
        form.pack(fill="x", pady=10)
        tk.Label(form, text="Email / Username", bg=C["panel"], fg=C["moon"], width=16, anchor="w").grid(
            row=0, column=0, sticky="w", pady=4
        )
        self.ely_user = tk.StringVar()
        self._entry(form, textvariable=self.ely_user, width=36).grid(row=0, column=1, sticky="we", pady=4, ipady=4)
        tk.Label(form, text="Password", bg=C["panel"], fg=C["moon"], width=16, anchor="w").grid(
            row=1, column=0, sticky="w", pady=4
        )
        self.ely_pass = tk.StringVar()
        self._entry(form, textvariable=self.ely_pass, show="•", width=36).grid(
            row=1, column=1, sticky="we", pady=4, ipady=4
        )
        tk.Label(form, text="2FA code (if any)", bg=C["panel"], fg=C["moon"], width=16, anchor="w").grid(
            row=2, column=0, sticky="w", pady=4
        )
        self.ely_2fa = tk.StringVar()
        self._entry(form, textvariable=self.ely_2fa, width=12).grid(row=2, column=1, sticky="w", pady=4, ipady=4)
        form.columnconfigure(1, weight=1)

        brow = tk.Frame(ely, bg=C["panel"])
        brow.pack(fill="x", pady=(4, 0))
        self._btn(brow, "Sign in with Ely.by", self.ely_login, kind="ely").pack(side="left", padx=2)
        self._btn(brow, "Log out", self.logout, kind="danger").pack(side="left", padx=6)
        self._btn(brow, "Open ely.by", lambda: webbrowser.open("https://ely.by")).pack(side="left", padx=2)
        self._btn(brow, "Create account", lambda: webbrowser.open("https://account.ely.by")).pack(
            side="left", padx=2
        )

        tk.Label(
            wrap,
            text="Password is sent only to authserver.ely.by and never stored. Session tokens stay local for auto-login.",
            bg=C["bg"],
            fg=C["moon_dim"],
            font=("Segoe UI", 8),
            wraplength=860,
            justify="left",
        ).pack(anchor="w", pady=8)

    def _build_bottom_bar(self):
        bar = tk.Frame(self.root, bg=C["panel"], height=70)
        bar.pack(side="bottom", fill="x")
        bar.pack_propagate(False)
        tk.Frame(self.root, bg=C["corona_dim"], height=1).pack(side="bottom", fill="x")

        inner = tk.Frame(bar, bg=C["panel"])
        inner.pack(fill="both", expand=True, padx=16, pady=10)

        self.login_lbl = tk.Label(
            inner, text="Not logged in", fg=C["warn"], bg=C["panel"], font=("Segoe UI", 10, "bold")
        )
        self.login_lbl.pack(side="left")

        self.status_var = tk.StringVar(value="Ready")
        tk.Label(inner, textvariable=self.status_var, fg=C["success"], bg=C["panel"], font=("Segoe UI", 9)).pack(
            side="left", padx=18
        )
        self.progress = ttk.Progressbar(inner, length=180, mode="determinate")
        self.progress.pack(side="left", padx=(8, 4))
        self.percent_var = tk.StringVar(value="0%")
        tk.Label(inner, textvariable=self.percent_var, fg=C["corona"], bg=C["panel"], font=("Segoe UI", 10, "bold")).pack(
            side="left", padx=(0, 8)
        )

        self._btn(
            inner, "▶  PLAY", self.launch_game, kind="play", font=("Segoe UI", 12, "bold"), padx=28, pady=8
        ).pack(side="right", padx=4)
        quick = tk.Frame(inner, bg=C["panel"])
        quick.pack(side="right", padx=12)
        self._btn(quick, "Offline", self.offline_login_quick).pack(side="left", padx=3)
        self._btn(quick, "Ely.by", lambda: self.nb.select(self.tab_account), kind="ely").pack(side="left", padx=3)

    # ── Queue ────────────────────────────────────────────────────────────────
    def process_queue(self):
        try:
            while True:
                item = self.status_queue.get_nowait()
                if item[0] == "status":
                    self.status_var.set(item[1])
                elif item[0] == "progress":
                    self.progress["value"] = item[1]
                elif item[0] == "percent":
                    self.percent_var.set(f"{item[1]:.2f}%")
                elif item[0] == "max":
                    self.progress["maximum"] = max(1, item[1])
                elif item[0] == "ui":
                    item[1]()
        except queue.Empty:
            pass
        self.root.after(80, self.process_queue)

    def set_status(self, text):
        self.status_queue.put(("status", text))

    def update_progress(self, val):
        self.status_queue.put(("progress", val))
        self.status_queue.put(("percent", float(val)))

    def set_max(self, val):
        self.status_queue.put(("max", val))

    def ui(self, fn):
        self.status_queue.put(("ui", fn))

    # ── Versions ─────────────────────────────────────────────────────────────
    def refresh_versions(self):
        def work():
            try:
                online = get_available_versions(str(self.minecraft_dir))
            except Exception as e:
                self.set_status(f"Could not load versions: {e}")
                online = []
            try:
                installed = get_installed_versions(str(self.minecraft_dir))
            except Exception:
                installed = []

            installed_ids = {v["id"] for v in installed}
            want = (self.filter_var.get() or "all").lower()
            entries = []

            # Modpacks only show under the Modded filter to avoid confusion with regular versions
            if want == "modded":
                for pack in self.get_modpacks():
                    ready = bool(pack.get("game_version_id")) and (
                        self.minecraft_dir / "versions" / pack["game_version_id"]
                    ).is_dir()
                    mark = "Installed   " if ready else "Uninstalled  "
                    entries.append(
                        {
                            "kind": "modpack",
                            "id": f"mp:{pack['id']}",
                            "pack_id": pack["id"],
                            "label": f"{mark}☀ {pack['name']}  ·  {pack.get('loader','?').title()}  ·  {pack.get('mc_version','?')}  [modpack]",
                            "mc_version": pack.get("mc_version"),
                            "loader": pack.get("loader"),
                            "game_version_id": pack.get("game_version_id"),
                        }
                    )

            if want == "modded":
                # Installed loader versions
                for v in installed:
                    vid = v["id"]
                    if is_modded_version_id(vid):
                        entries.append(
                            {
                                "kind": "version",
                                "id": vid,
                                "label": f"Installed   {vid}  ·  {loader_label_from_id(vid)}",
                                "installed": True,
                            }
                        )
                # Offer Fabric / Forge / Quilt for recent releases
                releases = [v for v in online if (v.get("type") or "").lower() == "release"][:LOADER_OFFER_LIMIT]
                for v in releases:
                    mc = v["id"]
                    for loader in ("fabric", "forge", "quilt"):
                        # Skip if an installed version already covers this
                        already = any(
                            is_modded_version_id(i) and mc in i and loader in i.lower() for i in installed_ids
                        )
                        # Also skip if a pack uses it
                        synth = f"loader:{loader}:{mc}"
                        mark = "Installed   " if already else "Uninstalled  "
                        entries.append(
                            {
                                "kind": "loader_offer",
                                "id": synth,
                                "label": f"{mark}{mc}  ·  {loader.title()}",
                                "mc_version": mc,
                                "loader": loader,
                                "installed": already,
                            }
                        )
            else:
                type_filter = None if want == "all" else want
                for v in online:
                    vtype = (v.get("type") or "").lower()
                    if type_filter and vtype != type_filter:
                        continue
                    vid = v["id"]
                    installed_flag = vid in installed_ids or (
                        self.minecraft_dir / "versions" / vid
                    ).is_dir()
                    mark = "Installed   " if installed_flag else "Uninstalled  "
                    entries.append(
                        {
                            "kind": "version",
                            "id": vid,
                            "label": f"{mark}{vid}  ·  {v.get('type', '?')}",
                            "installed": installed_flag,
                        }
                    )
                # Also show installed custom (loader) versions not in online list when All
                if want == "all":
                    online_ids = {v["id"] for v in online}
                    for v in installed:
                        vid = v["id"]
                        if vid not in online_ids:
                            entries.append(
                                {
                                    "kind": "version",
                                    "id": vid,
                                    "label": f"Installed   {vid}  ·  installed",
                                    "installed": True,
                                }
                            )

            def apply():
                self.vlist.delete(0, tk.END)
                self._version_entries = entries
                for e in entries:
                    self.vlist.insert(tk.END, e["label"])
                last = self.config.get("last_version") or self.current_version
                if last:
                    for i, e in enumerate(entries):
                        if e["id"] == last:
                            self.vlist.selection_set(i)
                            self.vlist.see(i)
                            self.current_version = last
                            self.selected_entry = e
                            clean = e['label']
                            for p in ("Installed   ", "Uninstalled  ", "● ", "○ "):
                                if clean.startswith(p):
                                    clean = clean[len(p):]
                                    break
                            self.version_info.config(text=f"Selected: {clean}")
                            break
                self.set_status(f"Loaded {len(entries)} entries")

            self.ui(apply)

        threading.Thread(target=work, daemon=True).start()

    def _on_version_select(self, _event=None):
        sel = self.vlist.curselection()
        if not sel or not hasattr(self, "_version_entries"):
            return
        entry = self._version_entries[sel[0]]
        self.selected_entry = entry
        self.current_version = entry["id"]
        self.config["last_version"] = entry["id"]
        self._save_config()
        clean = entry['label']
        for p in ("Installed   ", "Uninstalled  ", "● ", "○ "):
            if clean.startswith(p):
                clean = clean[len(p):]
                break
        self.version_info.config(text=f"Selected: {clean}")

        # If modpack, set active pack for installs
        if entry.get("kind") == "modpack":
            self.config["active_modpack_id"] = entry.get("pack_id")
            self._save_config()
            self._refresh_modpack_combo()
            self.refresh_installed_mods()

    def get_selected_entry(self):
        sel = self.vlist.curselection()
        if sel and hasattr(self, "_version_entries") and sel[0] < len(self._version_entries):
            return self._version_entries[sel[0]]
        return self.selected_entry

    def install_selected(self):
        entry = self.get_selected_entry()
        if not entry:
            messagebox.showwarning("Eclipse Client", "Select a version first.")
            return
        kind = entry.get("kind")
        if kind == "modpack":
            threading.Thread(target=self._ensure_modpack_loader, args=(entry["pack_id"],), daemon=True).start()
        elif kind == "loader_offer":
            threading.Thread(
                target=self._install_loader,
                args=(entry["loader"], entry["mc_version"]),
                daemon=True,
            ).start()
        else:
            ver = entry["id"]
            if entry.get("installed"):
                messagebox.showinfo("Already installed", f"{ver} is already on disk.\nSelect it and press Play.")
                return
            self.current_version = ver
            threading.Thread(target=self._install_vanilla, args=(ver,), daemon=True).start()

    def _install_vanilla(self, version):
        try:
            self.set_status(f"Downloading {version}…")
            cb = {"setStatus": self.set_status, "setProgress": self.update_progress, "setMax": self.set_max}
            install_minecraft_version(version, str(self.minecraft_dir), callback=cb)
            self.set_status(f"Installed {version}")
            self.ui(lambda: messagebox.showinfo("Eclipse Client", f"{version} is ready."))
            self.refresh_versions()
        except Exception as e:
            self.set_status(f"Install error: {str(e)[:100]}")
            self.ui(lambda: messagebox.showerror("Install Failed", str(e)))

    def _install_loader(self, loader: str, mc_version: str, quiet: bool = False) -> str | None:
        """Install loader for MC version. Returns installed version id or None."""
        try:
            self.set_status(f"Installing {loader.title()} for {mc_version}…")
            cb = {"setStatus": self.set_status, "setProgress": self.update_progress, "setMax": self.set_max}
            install_minecraft_version(mc_version, str(self.minecraft_dir), callback=cb)

            if loader == "fabric":
                fabric.install_fabric(mc_version, str(self.minecraft_dir), callback=cb)
            elif loader == "quilt":
                quilt.install_quilt(mc_version, str(self.minecraft_dir), callback=cb)
            elif loader == "forge":
                fv = forge.find_forge_version(mc_version)
                if not fv:
                    raise RuntimeError(f"No Forge version found for {mc_version}")
                forge.install_forge_version(fv, str(self.minecraft_dir), callback=cb)
            else:
                raise RuntimeError(f"Unsupported loader: {loader}")

            gvid = self._find_loader_version_id(loader, mc_version)
            self.set_status(f"{loader.title()} for {mc_version} installed")
            if not quiet:
                self.ui(
                    lambda: messagebox.showinfo(
                        "Modded install", f"{loader.title()} for Minecraft {mc_version} is ready."
                    )
                )
            self.refresh_versions()
            return gvid
        except Exception as e:
            self.set_status(f"Loader install failed: {e}")
            self.ui(lambda: messagebox.showerror("Loader Install Failed", str(e)))
            return None

    def _find_loader_version_id(self, loader: str, mc_version: str):
        """Best-effort resolve installed loader profile id."""
        try:
            installed = get_installed_versions(str(self.minecraft_dir))
        except Exception:
            installed = []
        loader = loader.lower()
        candidates = []
        for v in installed:
            vid = v["id"]
            low = vid.lower()
            if mc_version in vid and loader in low:
                candidates.append(vid)
        if candidates:
            candidates.sort(key=len, reverse=True)
            return candidates[0]
        if loader == "forge":
            fv = forge.find_forge_version(mc_version)
            if fv:
                return forge.forge_to_installed_version(fv)
        return None

    def _ensure_modpack_loader(self, pack_id: str):
        pack = self.get_modpack(pack_id)
        if not pack:
            return
        try:
            mc = pack["mc_version"]
            loader = pack["loader"]
            self.set_status(f"Preparing modpack “{pack['name']}”…")
            # Reuse existing loader install if present
            gvid = self._find_loader_version_id(loader, mc)
            if not gvid or not (self.minecraft_dir / "versions" / gvid).is_dir():
                gvid = self._install_loader(loader, mc, quiet=True)
            if gvid:
                pack["game_version_id"] = gvid
                self._save_config()
            (self.instances_dir / pack["folder"] / "mods").mkdir(parents=True, exist_ok=True)
            if gvid:
                self.set_status(f"Modpack “{pack['name']}” ready ({gvid})")
                self.ui(
                    lambda: messagebox.showinfo(
                        "Modpack ready",
                        f"“{pack['name']}” is ready to play.\n\nLoader profile:\n{gvid}",
                    )
                )
            self.refresh_versions()
        except Exception as e:
            self.ui(lambda: messagebox.showerror("Modpack", str(e)))

    def uninstall_selected(self):
        entry = self.get_selected_entry()
        if not entry:
            return
        if entry.get("kind") == "modpack":
            if messagebox.askyesno("Delete modpack", f"Delete modpack “{entry.get('pack_id')}”?"):
                self._delete_modpack(entry["pack_id"])
            return
        if entry.get("kind") == "loader_offer":
            messagebox.showinfo("Eclipse Client", "This entry is not installed yet.")
            return
        ver = entry["id"]
        if not messagebox.askyesno("Uninstall", f"Delete version {ver}?"):
            return
        try:
            shutil.rmtree(self.minecraft_dir / "versions" / ver, ignore_errors=True)
            if self.current_version == ver:
                self.current_version = None
            self.refresh_versions()
            self.set_status(f"Removed {ver}")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    # ── Modpacks ─────────────────────────────────────────────────────────────
    def _refresh_modpack_combo(self):
        packs = self.get_modpacks()
        labels = ["(global mods folder)"]
        self._pack_combo_map = {"(global mods folder)": None}
        active_id = self.config.get("active_modpack_id")
        active_label = "(global mods folder)"
        for p in packs:
            label = f"{p['name']}  ({p.get('loader','?')} · {p.get('mc_version','?')})"
            labels.append(label)
            self._pack_combo_map[label] = p["id"]
            if p["id"] == active_id:
                active_label = label
        self.pack_combo["values"] = labels
        self.pack_combo_var.set(active_label)
        self._update_pack_status_label()

    def _update_pack_status_label(self):
        pack = self.get_active_modpack()
        if pack:
            self.pack_status.config(
                text=f"Installing mods → pack “{pack['name']}” ({pack.get('loader')} {pack.get('mc_version')})"
            )
        else:
            self.pack_status.config(text="Installing mods → global .minecraft/mods")

    def _on_pack_combo(self, _e=None):
        label = self.pack_combo_var.get()
        pack_id = (getattr(self, "_pack_combo_map", {}) or {}).get(label)
        self.config["active_modpack_id"] = pack_id
        self._save_config()
        self._update_pack_status_label()
        self.refresh_installed_mods()
        # Sync loader filter with pack
        pack = self.get_active_modpack()
        if pack and pack.get("loader"):
            self.loader_var.set(pack["loader"])

    def create_modpack_dialog(self):
        win = tk.Toplevel(self.root)
        win.title("New modpack")
        win.configure(bg=C["bg"])
        win.geometry("420x280")
        win.transient(self.root)
        win.grab_set()

        tk.Label(win, text="Create modpack", bg=C["bg"], fg=C["corona"], font=("Segoe UI", 14, "bold")).pack(
            pady=(16, 8)
        )

        form = tk.Frame(win, bg=C["bg"])
        form.pack(fill="x", padx=24)

        name_var = tk.StringVar(value="My Modpack")
        mc_var = tk.StringVar(value="1.20.1")
        loader_var = tk.StringVar(value="fabric")

        for i, (lab, var, width) in enumerate(
            [
                ("Name", name_var, 28),
                ("Minecraft version", mc_var, 16),
            ]
        ):
            tk.Label(form, text=lab, bg=C["bg"], fg=C["moon_dim"]).grid(row=i, column=0, sticky="w", pady=6)
            self._entry(form, textvariable=var, width=width).grid(row=i, column=1, sticky="we", pady=6, ipady=3)

        tk.Label(form, text="Mod loader", bg=C["bg"], fg=C["moon_dim"]).grid(row=2, column=0, sticky="w", pady=6)
        ttk.Combobox(
            form, textvariable=loader_var, values=["fabric", "forge", "quilt"], state="readonly", width=14
        ).grid(row=2, column=1, sticky="w", pady=6)
        form.columnconfigure(1, weight=1)

        def create():
            name = safe_pack_name(name_var.get())
            mc = (mc_var.get() or "").strip()
            loader = (loader_var.get() or "fabric").strip().lower()
            if not mc:
                messagebox.showwarning("Modpack", "Enter a Minecraft version.", parent=win)
                return
            pack_id = uuid.uuid4().hex[:10]
            folder = safe_pack_name(name).replace(" ", "_") + "_" + pack_id
            pack = {
                "id": pack_id,
                "name": name,
                "mc_version": mc,
                "loader": loader,
                "folder": folder,
                "game_version_id": None,
                "mods": [],
            }
            self.config.setdefault("modpacks", []).append(pack)
            self.config["active_modpack_id"] = pack_id
            self._save_config()
            (self.instances_dir / folder / "mods").mkdir(parents=True, exist_ok=True)
            win.destroy()
            self._refresh_modpack_combo()
            self.refresh_installed_mods()
            self.refresh_versions()
            self.set_status(f"Created modpack “{name}” — installing {loader}…")
            # Install loader in background
            threading.Thread(target=self._ensure_modpack_loader, args=(pack_id,), daemon=True).start()
            messagebox.showinfo(
                "Modpack created",
                f"“{name}” created.\n\n"
                f"Loader: {loader.title()}\n"
                f"MC: {mc}\n\n"
                "It appears under Versions → Modded.\n"
                "Select this pack in the Modrinth tab to add mods to it.",
            )

        self._btn(win, "Create", create, kind="primary").pack(pady=18)

    def select_active_pack_for_play(self):
        pack = self.get_active_modpack()
        if not pack:
            messagebox.showinfo("Modpack", "Select a modpack from the dropdown first.")
            return
        self.current_version = f"mp:{pack['id']}"
        self.config["last_version"] = self.current_version
        self._save_config()
        self.version_info.config(
            text=f"Selected modpack: {pack['name']} ({pack.get('loader')} {pack.get('mc_version')})"
        )
        self.set_status(f"Will play modpack “{pack['name']}”")
        self.nb.select(self.tab_versions)
        self.filter_var.set("modded")
        self.refresh_versions()

    def delete_active_modpack(self):
        pack = self.get_active_modpack()
        if not pack:
            messagebox.showinfo("Modpack", "No modpack selected.")
            return
        if not messagebox.askyesno("Delete modpack", f"Delete “{pack['name']}” and its instance folder?"):
            return
        self._delete_modpack(pack["id"])

    def _delete_modpack(self, pack_id):
        packs = self.get_modpacks()
        pack = next((p for p in packs if p["id"] == pack_id), None)
        self.config["modpacks"] = [p for p in packs if p["id"] != pack_id]
        if self.config.get("active_modpack_id") == pack_id:
            self.config["active_modpack_id"] = None
        if pack:
            shutil.rmtree(self.instances_dir / pack.get("folder", ""), ignore_errors=True)
        self._save_config()
        self._refresh_modpack_combo()
        self.refresh_installed_mods()
        self.refresh_versions()
        self.set_status("Modpack deleted")

    # ── Modrinth search / cards ──────────────────────────────────────────────
    def search_mods(self, reset_page=False):
        if reset_page:
            self.mod_page = 0
        self.mod_query = (self.mod_search_var.get() or "").strip()
        self.set_status("Searching Modrinth…")
        threading.Thread(target=self._search_mods_worker, daemon=True).start()

    def prev_mod_page(self):
        if self.mod_page > 0:
            self.mod_page -= 1
            self.search_mods(reset_page=False)

    def next_mod_page(self):
        max_page = max(0, (self.mod_total - 1) // MODS_PER_PAGE)
        if self.mod_page < max_page:
            self.mod_page += 1
            self.search_mods(reset_page=False)

    def _search_mods_worker(self):
        try:
            facets = [["project_type:mod"]]
            loader = (self.loader_var.get() or "any").lower()
            if loader and loader != "any":
                facets.append([f"categories:{loader}"])

            pack = self.get_active_modpack()
            mc_ver = pack.get("mc_version") if pack else None
            if not mc_ver and self.selected_entry and self.selected_entry.get("mc_version"):
                mc_ver = self.selected_entry["mc_version"]
            # plain version from current if it looks like MC version
            if not mc_ver and self.current_version and re.match(r"^\d+\.\d+", str(self.current_version)):
                mc_ver = self.current_version
            if mc_ver and re.match(r"^\d+\.\d+", str(mc_ver)):
                facets.append([f"versions:{mc_ver}"])

            params = {
                "query": self.mod_query or " ",
                "limit": MODS_PER_PAGE,
                "offset": self.mod_page * MODS_PER_PAGE,
                "index": "relevance" if self.mod_query else "downloads",
                "facets": json.dumps(facets),
            }
            r = requests.get(
                f"{MODRINTH_API}/search",
                params=params,
                headers={"User-Agent": USER_AGENT},
                timeout=25,
            )
            r.raise_for_status()
            data = r.json()
            hits = data.get("hits") or []
            self.mod_total = int(data.get("total_hits") or 0)
            self.mod_results = hits
            # Prefetch icons
            icons = {}
            for h in hits:
                pid = h.get("project_id") or h.get("slug")
                url = h.get("icon_url")
                icons[pid] = self._load_icon(pid, url)

            def apply():
                self._render_mod_cards(hits, icons)
                max_page = max(0, (self.mod_total - 1) // MODS_PER_PAGE)
                self.page_lbl.config(
                    text=f"Page {self.mod_page + 1} / {max_page + 1}  ·  {self.mod_total:,} mods"
                )
                self.set_status(f"Modrinth: {len(hits)} results (page {self.mod_page + 1})")

            self.ui(apply)
        except Exception as e:
            self.set_status(f"Modrinth search failed: {e}")
            self.ui(lambda: messagebox.showerror("Modrinth", str(e)))

    def _load_icon(self, project_id, url):
        """Download/cache icon and return PhotoImage (must be used on main thread eventually).
        Returns raw path or None; PhotoImage created on main thread."""
        if not url:
            return None
        cache = self.icon_cache_dir / f"{project_id}.png"
        try:
            if not cache.exists() or cache.stat().st_size < 50:
                resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=15)
                resp.raise_for_status()
                img = Image.open(io.BytesIO(resp.content)).convert("RGBA")
                img = img.resize((ICON_SIZE, ICON_SIZE), Image.Resampling.LANCZOS)
                img.save(cache, format="PNG")
            return str(cache)
        except Exception:
            return None

    def _photo_from_path(self, path):
        try:
            if path and Path(path).exists():
                img = Image.open(path).convert("RGBA").resize((ICON_SIZE, ICON_SIZE), Image.Resampling.LANCZOS)
                ph = ImageTk.PhotoImage(img)
                self._photo_refs.append(ph)
                return ph
        except Exception:
            pass
        return self._placeholder_icon

    def _render_mod_cards(self, hits, icon_paths):
        for w in self.mod_grid.winfo_children():
            w.destroy()
        self._photo_refs = [self._placeholder_icon]

        if not hits:
            tk.Label(
                self.mod_grid,
                text="No mods found. Try another search or loader.",
                bg=C["bg"],
                fg=C["moon_dim"],
                font=("Segoe UI", 11),
            ).grid(row=0, column=0, padx=20, pady=40)
            return

        cols = 3
        for i, h in enumerate(hits):
            r, c = divmod(i, cols)
            card = self._make_mod_card(self.mod_grid, h, icon_paths.get(h.get("project_id") or h.get("slug")))
            card.grid(row=r, column=c, padx=8, pady=8, sticky="nsew")
        for c in range(cols):
            self.mod_grid.columnconfigure(c, weight=1)

    def _make_mod_card(self, parent, hit, icon_path):
        card = tk.Frame(
            parent,
            bg=C["card"],
            highlightthickness=1,
            highlightbackground=C["border"],
            width=200,
            height=210,
        )
        card.pack_propagate(False)

        slug = hit.get("slug") or hit.get("project_id")
        title = hit.get("title") or slug or "Mod"
        project_id = hit.get("project_id") or slug
        url = f"https://modrinth.com/mod/{slug}" if slug else f"https://modrinth.com/mod/{project_id}"

        photo = self._photo_from_path(icon_path)

        icon_btn = tk.Label(card, image=photo, bg=C["card"], cursor="hand2")
        icon_btn.image = photo
        icon_btn.pack(pady=(14, 6))
        icon_btn.bind("<Button-1>", lambda e, u=url: webbrowser.open(u))
        icon_btn.bind("<Enter>", lambda e: card.config(highlightbackground=C["corona"]))
        icon_btn.bind("<Leave>", lambda e: card.config(highlightbackground=C["border"]))

        name_lbl = tk.Label(
            card,
            text=title if len(title) <= 28 else title[:26] + "…",
            bg=C["card"],
            fg=C["moon"],
            font=("Segoe UI", 9, "bold"),
            wraplength=170,
            justify="center",
            cursor="hand2",
        )
        name_lbl.pack(padx=6)
        name_lbl.bind("<Button-1>", lambda e, u=url: webbrowser.open(u))

        dl = hit.get("downloads") or 0
        dl_s = f"{dl/1_000_000:.1f}M" if dl >= 1_000_000 else (f"{dl/1000:.0f}k" if dl >= 1000 else str(dl))
        tk.Label(card, text=f"{dl_s} downloads", bg=C["card"], fg=C["moon_dim"], font=("Segoe UI", 8)).pack()

        self._btn(
            card,
            "Install",
            lambda h=hit: self.install_mod_hit(h),
            kind="primary",
            padx=16,
            pady=4,
        ).pack(pady=(8, 12))

        return card

    def install_mod_hit(self, hit):
        pack = self.get_active_modpack()
        if pack:
            game_version = pack.get("mc_version")
            loader = pack.get("loader")
        else:
            game_version = None
            loader = (self.loader_var.get() or "fabric").lower()
            if loader == "any":
                loader = None
            entry = self.get_selected_entry()
            if entry and entry.get("mc_version"):
                game_version = entry["mc_version"]
            elif self.current_version and re.match(r"^\d+\.\d+", str(self.current_version or "")):
                game_version = self.current_version
            if not game_version:
                game_version = simpledialog.askstring(
                    "Minecraft version",
                    "Minecraft version for this mod (e.g. 1.20.1):",
                    initialvalue="1.20.1",
                )
                if not game_version:
                    return

        self.set_status(f"Installing {hit.get('title')}…")
        threading.Thread(
            target=self._install_mod_with_deps,
            args=(hit, game_version, loader),
            daemon=True,
        ).start()

    def _install_mod_with_deps(self, hit, game_version, loader):
        with self._mod_install_lock:
            try:
                installed = []
                seen = set()
                self._install_project_recursive(
                    hit.get("project_id") or hit.get("slug"),
                    game_version,
                    loader,
                    installed,
                    seen,
                    title_hint=hit.get("title"),
                )
                # Track in modpack
                pack = self.get_active_modpack()
                if pack:
                    for item in installed:
                        if item not in pack.setdefault("mods", []):
                            pack["mods"].append(item)
                    self._save_config()

                names = ", ".join(installed[:8]) + ("…" if len(installed) > 8 else "")
                self.set_status(f"Installed {len(installed)} file(s)")
                self.refresh_installed_mods()
                self.ui(
                    lambda: messagebox.showinfo(
                        "Mods installed",
                        f"Installed {len(installed)} file(s) including dependencies:\n\n{names}\n\n"
                        f"Folder:\n{self.mods_target_dir()}",
                    )
                )
            except Exception as e:
                self.set_status(f"Mod install failed: {e}")
                self.ui(lambda: messagebox.showerror("Modrinth Install Failed", str(e)))

    def _pick_version(self, project_id, game_version, loader):
        param_sets = []
        if game_version and loader:
            param_sets.append({"game_versions": json.dumps([game_version]), "loaders": json.dumps([loader])})
        if game_version:
            param_sets.append({"game_versions": json.dumps([game_version])})
        param_sets.append({})

        versions = None
        for params in param_sets:
            try:
                r = requests.get(
                    f"{MODRINTH_API}/project/{project_id}/version",
                    params=params,
                    headers={"User-Agent": USER_AGENT},
                    timeout=25,
                )
                r.raise_for_status()
                versions = r.json() or []
                if versions:
                    break
            except Exception:
                continue

        if not versions:
            return None

        def rank(v):
            t = (v.get("version_type") or "").lower()
            return {"release": 0, "beta": 1, "alpha": 2}.get(t, 3)

        versions.sort(key=rank)
        return versions[0]

    def _download_primary_file(self, version_obj, dest_dir: Path) -> str:
        files = version_obj.get("files") or []
        if not files:
            raise RuntimeError("Version has no files")
        primary = next((f for f in files if f.get("primary")), files[0])
        fname = safe_filename(primary.get("filename") or "mod.jar")
        dest = dest_dir / fname
        if dest.exists() and dest.stat().st_size > 0:
            return fname
        url = primary["url"]
        self.set_status(f"Downloading {fname}…")
        with requests.get(url, headers={"User-Agent": USER_AGENT}, stream=True, timeout=120) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("content-length") or 0)
            done = 0
            self.set_max(100)
            with open(dest, "wb") as out:
                for chunk in resp.iter_content(chunk_size=65536):
                    if not chunk:
                        continue
                    out.write(chunk)
                    done += len(chunk)
                    if total:
                        self.update_progress(int(done * 100 / total))
        self.update_progress(100)
        return fname

    def _install_project_recursive(
        self, project_id, game_version, loader, installed_list, seen, title_hint=None, depth=0
    ):
        if not project_id or project_id in seen or depth > 8:
            return
        seen.add(project_id)

        ver = self._pick_version(project_id, game_version, loader)
        if not ver:
            if depth == 0:
                raise RuntimeError(f"No compatible version for {title_hint or project_id}")
            return

        # Required dependencies first
        for dep in ver.get("dependencies") or []:
            if (dep.get("dependency_type") or "").lower() != "required":
                continue
            dep_pid = dep.get("project_id")
            if dep_pid:
                self.set_status(f"Dependency: {dep_pid}…")
                self._install_project_recursive(
                    dep_pid, game_version, loader, installed_list, seen, depth=depth + 1
                )

        dest_dir = self.mods_target_dir()
        fname = self._download_primary_file(ver, dest_dir)
        if fname not in installed_list:
            installed_list.append(fname)

    def refresh_installed_mods(self):
        mods_dir = self.mods_target_dir()

        def apply():
            self.installed_list.delete(0, tk.END)
            jars = sorted(mods_dir.glob("*.jar"), key=lambda p: p.name.lower())
            if not jars:
                self.installed_list.insert(tk.END, "(empty)")
                return
            for j in jars:
                self.installed_list.insert(tk.END, j.name)

        if threading.current_thread() is threading.main_thread():
            apply()
        else:
            self.ui(apply)

    def remove_installed_mod(self):
        sel = self.installed_list.curselection()
        if not sel:
            return
        name = self.installed_list.get(sel[0])
        if name.startswith("("):
            return
        path = self.mods_target_dir() / name
        if not path.exists():
            messagebox.showerror("Mods", f"File not found: {name}")
            return
        if not messagebox.askyesno("Remove mod", f"Delete {name}?"):
            return
        try:
            path.unlink()
            pack = self.get_active_modpack()
            if pack and name in pack.get("mods", []):
                pack["mods"] = [m for m in pack["mods"] if m != name]
                self._save_config()
            self.refresh_installed_mods()
            self.set_status(f"Removed {name}")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def open_mods_folder(self):
        mods_dir = self.mods_target_dir()
        mods_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(mods_dir))
        except Exception:
            webbrowser.open(mods_dir.as_uri())

    # ── Auth ─────────────────────────────────────────────────────────────────
    def offline_login_quick(self):
        u = simpledialog.askstring(
            "Offline Login", "Username:", initialvalue=self.offline_user.get() or "EclipsePlayer"
        )
        if u:
            self.offline_user.set(u)
            self.offline_login()

    def offline_login(self):
        u = (self.offline_user.get() or "").strip() or "EclipsePlayer"
        self.options = minecraft_launcher_lib.utils.generate_test_options()
        self.options["username"] = u
        self.options["launcherName"] = "EclipseClient"
        self.options["launcherVersion"] = "1.2"
        self.auth_mode = "offline"
        self.session = {}
        self.config["session"] = {"mode": "offline", "username": u}
        self._save_config()
        self.login_lbl.config(text=f"Offline · {u}", fg=C["warn"])
        self.set_status(f"Playing offline as {u}")

    def logout(self):
        self.options = None
        self.auth_mode = None
        self.session = {}
        self.config["session"] = None
        self._save_config()
        self.login_lbl.config(text="Not logged in", fg=C["warn"])
        self.ely_pass.set("")
        self.ely_2fa.set("")
        self.set_status("Logged out")

    def ely_login(self):
        username = (self.ely_user.get() or "").strip()
        password = self.ely_pass.get() or ""
        totp = (self.ely_2fa.get() or "").strip()
        if not username or not password:
            messagebox.showwarning("Ely.by", "Enter your Ely.by email/username and password.")
            return
        self.set_status("Signing in to Ely.by…")
        threading.Thread(target=self._ely_authenticate, args=(username, password, totp), daemon=True).start()

    def _ely_authenticate(self, username: str, password: str, totp: str = ""):
        client_token = self.config.get("client_token") or str(uuid.uuid4())
        self.config["client_token"] = client_token
        pwd = f"{password}:{totp}" if totp else password
        payload = {
            "username": username,
            "password": pwd,
            "clientToken": client_token,
            "requestUser": True,
        }
        headers = {
            "User-Agent": USER_AGENT,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        try:
            r = requests.post(ELY_AUTH, json=payload, headers=headers, timeout=20)
            data = {}
            try:
                data = r.json()
            except Exception:
                pass

            if r.status_code != 200:
                err = data.get("errorMessage") or data.get("error") or r.text or f"HTTP {r.status_code}"
                if "two factor" in err.lower() or "two-factor" in err.lower() or "2fa" in err.lower():

                    def ask_2fa():
                        code = simpledialog.askstring(
                            "Two-Factor Authentication",
                            "This Ely.by account has 2FA enabled.\nEnter your 6-digit code:",
                        )
                        if code:
                            self.ely_2fa.set(code.strip())
                            threading.Thread(
                                target=self._ely_authenticate,
                                args=(username, password, code.strip()),
                                daemon=True,
                            ).start()
                        else:
                            self.set_status("Login cancelled (2FA required)")

                    self.ui(ask_2fa)
                    return

                self.set_status(f"Ely.by login failed: {err[:90]}")
                self.ui(lambda: messagebox.showerror("Ely.by Login Failed", err))
                return

            profile = data.get("selectedProfile") or {}
            if not profile and data.get("availableProfiles"):
                profile = data["availableProfiles"][0]
            uname = profile.get("name") or username
            uid = format_uuid(profile.get("id", ""))
            access = data.get("accessToken")
            if not access:
                self.ui(lambda: messagebox.showerror("Ely.by", "No access token in response."))
                return

            sess = {
                "mode": "ely",
                "accessToken": access,
                "clientToken": data.get("clientToken", client_token),
                "username": uname,
                "uuid": uid,
            }
            self.session = sess
            self.auth_mode = "ely"
            self.options = {
                "username": uname,
                "uuid": uid,
                "token": access,
                "launcherName": "EclipseClient",
                "launcherVersion": "1.2",
            }
            self.config["session"] = sess
            self._save_config()
            self.set_status("Preparing authlib-injector…")
            self._ensure_authlib_injector()
            self.ui(
                lambda: (
                    self.login_lbl.config(text=f"Ely.by · {uname}", fg=C["success"]),
                    self.ely_pass.set(""),
                    self.ely_2fa.set(""),
                )
            )
            self.set_status(f"Signed in as {uname} (Ely.by)")
            self.ui(
                lambda: messagebox.showinfo(
                    "Ely.by",
                    f"Welcome, {uname}!\n\nSession active. Skins / Ely servers use authlib-injector.",
                )
            )
        except requests.RequestException as e:
            self.set_status(f"Network error: {e}")
            self.ui(lambda: messagebox.showerror("Network Error", str(e)))
        except Exception as e:
            self.set_status(f"Login error: {e}")
            self.ui(lambda: messagebox.showerror("Ely.by Error", str(e)))

    def _ensure_authlib_injector(self) -> bool:
        if self.authlib_path.exists() and self.authlib_path.stat().st_size > 1000:
            return True
        try:
            self.set_status("Downloading authlib-injector…")
            r = requests.get(AUTHLIB_RELEASES, headers={"User-Agent": USER_AGENT}, timeout=20)
            r.raise_for_status()
            rel = r.json()
            asset = None
            for a in rel.get("assets", []):
                name = a.get("name", "")
                if name.endswith(".jar") and "authlib-injector" in name and "sources" not in name:
                    asset = a
                    break
            if not asset:
                for a in rel.get("assets", []):
                    if a.get("name", "").endswith(".jar"):
                        asset = a
                        break
            if not asset:
                return False
            jar = requests.get(asset["browser_download_url"], headers={"User-Agent": USER_AGENT}, timeout=60)
            jar.raise_for_status()
            self.authlib_path.write_bytes(jar.content)
            self.set_status("authlib-injector ready")
            return True
        except Exception as e:
            self.set_status(f"authlib-injector download failed: {e}")
            return False

    # ── Launch ───────────────────────────────────────────────────────────────
    def launch_game(self):
        entry = self.get_selected_entry()
        version_id = None
        game_dir = self.minecraft_dir

        if entry:
            kind = entry.get("kind")
            if kind == "modpack":
                pack = self.get_modpack(entry.get("pack_id"))
                if not pack:
                    messagebox.showerror("Eclipse Client", "Modpack not found.")
                    return
                version_id = pack.get("game_version_id") or self._find_loader_version_id(
                    pack.get("loader", "fabric"), pack.get("mc_version", "")
                )
                if not version_id:
                    if messagebox.askyesno(
                        "Modpack",
                        f"Loader for “{pack['name']}” is not installed yet.\nInstall it now?",
                    ):
                        threading.Thread(
                            target=self._ensure_modpack_loader, args=(pack["id"],), daemon=True
                        ).start()
                    return
                pack["game_version_id"] = version_id
                self._save_config()
                game_dir = self.instances_dir / pack["folder"]
                game_dir.mkdir(parents=True, exist_ok=True)
                (game_dir / "mods").mkdir(exist_ok=True)
            elif kind == "loader_offer":
                if entry.get("installed"):
                    version_id = self._find_loader_version_id(entry["loader"], entry["mc_version"])
                if not version_id:
                    if messagebox.askyesno(
                        "Install loader",
                        f"Install {entry['loader'].title()} for {entry['mc_version']} first?",
                    ):
                        self.install_selected()
                    return
            else:
                version_id = entry.get("id")
        else:
            version_id = self.current_version
            if version_id and str(version_id).startswith("mp:"):
                pack = self.get_modpack(str(version_id)[3:])
                if pack:
                    version_id = pack.get("game_version_id")
                    game_dir = self.instances_dir / pack["folder"]

        if not version_id:
            messagebox.showerror("Eclipse Client", "Select an installed version or modpack first.")
            return

        if not (self.minecraft_dir / "versions" / version_id).is_dir():
            if messagebox.askyesno("Not installed", f"{version_id} is not installed.\nInstall it now?"):
                self.install_selected()
            return

        if not self.options:
            messagebox.showerror("Eclipse Client", "Log in first (Offline or Ely.by).")
            self.nb.select(self.tab_account)
            return

        options = dict(self.options)
        options["gameDirectory"] = str(game_dir)
        options["launcherName"] = "EclipseClient"
        options["launcherVersion"] = "1.2"

        if self.auth_mode == "ely":
            if not self._ensure_authlib_injector():
                if not messagebox.askyesno(
                    "authlib-injector",
                    "Could not download authlib-injector.\nLaunch without it?",
                ):
                    return
            if self.authlib_path.exists():
                agent = f"-javaagent:{self.authlib_path}=ely.by"
                jvm = list(options.get("jvmArguments") or [])
                jvm = [agent] + [a for a in jvm if "authlib-injector" not in a]
                options["jvmArguments"] = jvm

        def work():
            try:
                self.set_status(f"Launching {version_id}…")
                cmd = get_minecraft_command(version_id, str(self.minecraft_dir), options)
                subprocess.Popen(cmd, cwd=str(game_dir))
                self.set_status(f"Game started · {version_id}")
            except Exception as e:
                self.set_status(f"Launch error: {e}")
                self.ui(lambda: messagebox.showerror("Launch Error", str(e)))

        threading.Thread(target=work, daemon=True).start()

    def on_close(self):
        try:
            self.mod_canvas.unbind_all("<MouseWheel>")
        except Exception:
            pass
        if messagebox.askokcancel("Exit", "Close Eclipse Client?"):
            self._save_config()
            self.root.destroy()


if __name__ == "__main__":
    app = EclipseClient()
    app.root.mainloop()
