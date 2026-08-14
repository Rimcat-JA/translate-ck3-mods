#!/usr/bin/env python3
"""English desktop UI for the multilingual CK3 Mod Translator."""
from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import os
import queue
import re
import sys
import threading
import tkinter as tk
import urllib.parse
from collections.abc import Callable, Iterable
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

import ck3_clone as clone_engine
from ck3_languages import AUTO_LANGUAGE_ID, LANGUAGES, language_spec
from ck3_mod_scanner import (
    LocalizationInfo,
    ModCandidate,
    scan_descriptor,
    scan_mod_folder,
    scan_mod_library,
)
from ck3_providers import PROVIDERS, get_provider, validate_endpoint
from windows_credentials import delete_api_key, load_api_key, save_api_key

APP_NAME = "CK3 Mod Translator"
APP_VERSION = "2.0.1"
DEFAULT_ENDPOINT = "http://127.0.0.1:1234/v1/chat/completions"
AUTO_LANGUAGE_LABEL = "Auto-detect (Recommended)"
PROVIDER_DISPLAY = {
    "local": "Local LLM (Free, on-device)",
    "openai": "OpenAI API",
    "openrouter": "OpenRouter API",
    "nanogpt": "NanoGPT API (Pay-as-you-go)",
    "nanogpt_subscription": "NanoGPT API (Subscription)",
}
PROVIDER_LABELS = {
    PROVIDER_DISPLAY.get(provider_id, provider.label): provider_id
    for provider_id, provider in PROVIDERS.items()
}
SOURCE_LANGUAGE_LABELS = {AUTO_LANGUAGE_LABEL: AUTO_LANGUAGE_ID} | {
    language.display_name: language.language_id for language in LANGUAGES
}
TARGET_LANGUAGE_LABELS = {language.display_name: language.language_id for language in LANGUAGES}


def _clone_function() -> Callable[..., object]:
    """Prefer the multilingual engine while retaining v1 source compatibility."""
    return getattr(clone_engine, "create_localized_clone", clone_engine.create_japanese_clone)


def _clone_options(**values: object) -> object:
    """Allow the GUI to run while upgrading from the v1 CloneOptions schema."""
    accepted = {field.name for field in dataclasses.fields(clone_engine.CloneOptions)}
    return clone_engine.CloneOptions(**{key: value for key, value in values.items() if key in accepted})


def _ck3_mod_directory() -> Path | None:
    finder = getattr(clone_engine, "ck3_mod_directory", None)
    return finder() if callable(finder) else None


def _default_work_root() -> Path:
    return clone_engine.default_work_root()


def _safe_output_suffix(display_name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", display_name).strip("_") or "Translation"


def validate_user_endpoint(provider_id: str, endpoint: str) -> None:
    """Validate an endpoint at the UI boundary before persistence or requests."""
    validate_endpoint(provider_id, endpoint)
    if provider_id != "local":
        return
    parsed = urllib.parse.urlsplit(endpoint)
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Local endpoint URLs cannot contain credentials, queries, or fragments")


def safe_local_endpoint(candidate: object) -> str:
    """Return only a persistable, credential-free local endpoint."""
    endpoint = str(candidate).strip() or DEFAULT_ENDPOINT
    try:
        validate_user_endpoint("local", endpoint)
    except ValueError:
        return DEFAULT_ENDPOINT
    return endpoint


def endpoint_error_message(provider_id: str) -> str:
    """Describe endpoint requirements without echoing possibly secret input."""
    if provider_id == "local":
        return (
            "Use a loopback OpenAI-compatible endpoint such as "
            f"{DEFAULT_ENDPOINT}. Do not put a username, password, query string, or fragment in the URL. "
            "Enter an optional server token in its separate credential field."
        )
    return "Use the official endpoint shown for the selected remote provider."


def local_server_error_message(message: str, endpoint: str = DEFAULT_ENDPOINT) -> str:
    """Turn low-level local-server errors into an actionable LM Studio hint."""
    lowered = message.casefold()
    if any(marker in lowered for marker in ("401", "403", "unauthorized", "forbidden")):
        return (
            "LM Studio rejected the request. If server authentication is enabled, enter its API token "
            "above and click Refresh Models. The token is optional when authentication is disabled."
        )
    if "404" in lowered or "not found" in lowered:
        return (
            "The local server was reached, but no supported model-list endpoint was found "
            "(/api/v1/models, /v1/models, or /api/v0/models). "
            f"Check the endpoint ({endpoint}) and server version. You may still type the exact model ID manually."
        )
    if any(marker in lowered for marker in ("timed out", "timeout")):
        return (
            "The local server timed out. Wait for the model to finish loading in LM Studio, keep the "
            "Local Server running, and click Refresh Models again."
        )
    if any(
        marker in lowered
        for marker in (
            "connection refused",
            "failed to establish",
            "no connection could be made",
            "urlopen error",
            "winerror 10061",
        )
    ):
        return (
            "LM Studio Local Server is not reachable. In LM Studio, load a model, open Developer, and "
            "start the Local Server. Then check the endpoint and click Refresh Models."
        )
    return (
        "Could not query the local server. Confirm that an OpenAI-compatible server is running, check "
        "the endpoint and optional token, then click Refresh Models. You may also type the exact model ID manually."
    )


class CK3ModTranslator:
    """Tk application that discovers, classifies, and translates multiple CK3 mods."""

    def __init__(self, root: tk.Tk, auto_connect: bool = True) -> None:
        self.root = root
        self.events: queue.Queue[dict[str, object]] = queue.Queue()
        self.cancel_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.running = False
        self.scanning = False
        self.models_verified = False
        self.advanced_is_open = False
        self.persistence_enabled = auto_connect
        self.scan_generation = 0
        self.candidates: dict[str, ModCandidate] = {}
        self.checked: set[str] = set()
        self.last_outputs: list[Path] = []
        self.settings_path = _default_work_root() / "settings.json"
        local_today = dt.datetime.now(dt.timezone.utc).astimezone().date()
        self.log_path = _default_work_root() / "logs" / f"{local_today.isoformat()}.log"

        # Smoke tests and embedders that disable persistence must not inherit a
        # previous interactive session's provider, language, model, or endpoint.
        settings = self.load_settings() if auto_connect else {}
        initial_provider = str(settings.get("provider", "local"))
        if initial_provider not in PROVIDERS:
            initial_provider = "local"
        self.active_provider = initial_provider
        raw_models = settings.get("models", {})
        self.models_by_provider = dict(raw_models) if isinstance(raw_models, dict) else {}
        self.available_models_by_provider: dict[str, list[str]] = {}
        self.api_keys_by_provider: dict[str, str] = {}
        default_library = _ck3_mod_directory()
        self.library_var = tk.StringVar(value=str(settings.get("last_library", default_library or "")))
        self.source_var = self.library_var  # v1 compatibility
        saved_output = str(settings.get("output_parent", ""))
        if not saved_output and settings.get("last_output"):
            saved_output = str(Path(str(settings["last_output"])).parent)
        self.output_parent_var = tk.StringVar(value=saved_output or str(default_library or ""))
        self.output_var = self.output_parent_var  # v1 compatibility
        self.source_language_var = tk.StringVar(
            value=self.language_label(str(settings.get("source_language", AUTO_LANGUAGE_ID)), source=True)
        )
        self.target_language_var = tk.StringVar(
            value=self.language_label(str(settings.get("target_language", "japanese")), source=False)
        )
        self.provider_var = tk.StringVar(value=PROVIDER_DISPLAY.get(initial_provider, get_provider(initial_provider).label))
        self.api_key_var = tk.StringVar(value="")
        self.remember_key_var = tk.BooleanVar(value=True)
        self.local_endpoint = safe_local_endpoint(settings.get("local_endpoint", DEFAULT_ENDPOINT))
        endpoint = self.local_endpoint if initial_provider == "local" else get_provider(initial_provider).chat_endpoint
        self.endpoint_var = tk.StringVar(value=endpoint)
        self.model_var = tk.StringVar(value=str(self.models_by_provider.get(initial_provider, "")))
        try:
            initial_workers = max(1, min(8, int(settings.get("workers", 4))))
        except (TypeError, ValueError):
            initial_workers = 4
        self.workers_var = tk.IntVar(value=initial_workers)
        self.status_var = tk.StringVar(value="Scan a CK3 mod folder to begin.")
        self.server_var = tk.StringVar(value="Checking the local LLM…")
        self.selection_var = tk.StringVar(value="No mods loaded")

        self.configure_window()
        self.build_ui()
        self.apply_provider(initial=True)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(100, self.poll_events)
        if auto_connect and (initial_provider == "local" or self.api_key_var.get()):
            self.refresh_models()
        else:
            self.server_var.set("Smoke-test mode")
        if auto_connect and default_library and default_library.is_dir():
            self.root.after(250, self.scan_default_library)

    @staticmethod
    def language_label(language_id: str, *, source: bool) -> str:
        mapping = SOURCE_LANGUAGE_LABELS if source else TARGET_LANGUAGE_LABELS
        return next((label for label, value in mapping.items() if value == language_id), AUTO_LANGUAGE_LABEL if source else "Japanese")

    def source_language_id(self) -> str:
        return SOURCE_LANGUAGE_LABELS.get(self.source_language_var.get(), AUTO_LANGUAGE_ID)

    def target_language_id(self) -> str:
        return TARGET_LANGUAGE_LABELS.get(self.target_language_var.get(), "japanese")

    def configure_window(self) -> None:
        self.root.title(f"{APP_NAME}  v{APP_VERSION}")
        if os.name == "nt":
            try:
                icon_source = (
                    Path(sys.executable)
                    if getattr(sys, "frozen", False)
                    else Path(__file__).resolve().parent.parent / "packaging" / "app.ico"
                )
                if icon_source.is_file():
                    self.root.iconbitmap(default=str(icon_source))
            except tk.TclError:
                pass
        self.root.geometry("1100x790")
        self.root.minsize(900, 680)
        self.root.configure(bg="#eef2f6")
        self.root.update_idletasks()
        width, height = 1100, 790
        x = max(0, (self.root.winfo_screenwidth() - width) // 2)
        y = max(0, (self.root.winfo_screenheight() - height) // 2)
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        style = ttk.Style(self.root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("TButton", font=("Segoe UI", 9), padding=(10, 6))
        style.configure("Accent.TButton", font=("Segoe UI", 10, "bold"), padding=(18, 9))
        style.configure("TLabel", background="#ffffff", font=("Segoe UI", 9))
        style.configure("Card.TFrame", background="#ffffff")
        style.configure("Card.TLabelframe", background="#ffffff")
        style.configure("Card.TLabelframe.Label", background="#ffffff", font=("Segoe UI", 10, "bold"))
        style.configure("Mods.Treeview", rowheight=25, font=("Segoe UI", 9))
        style.configure("Mods.Treeview.Heading", font=("Segoe UI", 9, "bold"))

    def build_ui(self) -> None:
        header = tk.Frame(self.root, bg="#27364b", height=92)
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Label(header, text=APP_NAME, bg="#27364b", fg="white", font=("Segoe UI", 21, "bold")).pack(
            anchor="w", padx=28, pady=(14, 0)
        )
        tk.Label(
            header,
            text="Find CK3 mods, detect their actual language, and create safe translated copies.",
            bg="#27364b",
            fg="#dce6f3",
            font=("Segoe UI", 10),
        ).pack(anchor="w", padx=30, pady=(2, 0))

        body = ttk.Frame(self.root, style="Card.TFrame", padding=18)
        body.pack(fill="both", expand=True, padx=20, pady=16)
        scan_bar = ttk.Frame(body, style="Card.TFrame")
        scan_bar.pack(fill="x")
        ttk.Label(scan_bar, text="1. Choose mods", font=("Segoe UI", 11, "bold")).pack(side="left", padx=(0, 14))
        self.default_scan_button = ttk.Button(scan_bar, text="Scan CK3 Mod Folder", command=self.scan_default_library)
        self.default_scan_button.pack(side="left")
        self.library_scan_button = ttk.Button(scan_bar, text="Scan Another Folder…", command=self.choose_library)
        self.library_scan_button.pack(side="left", padx=(7, 0))
        self.add_folder_button = ttk.Button(scan_bar, text="Add Mod Folder…", command=self.choose_mod_folder)
        self.add_folder_button.pack(side="left", padx=(7, 0))
        self.add_descriptor_button = ttk.Button(scan_bar, text="Add .mod Files…", command=self.choose_descriptors)
        self.add_descriptor_button.pack(side="left", padx=(7, 0))

        library_row = ttk.Frame(body, style="Card.TFrame")
        library_row.pack(fill="x", pady=(8, 9))
        self.source_entry = ttk.Entry(library_row, textvariable=self.library_var, state="readonly")
        self.source_entry.pack(side="left", fill="x", expand=True, ipady=2)
        self.browse_button = self.library_scan_button

        language_row = ttk.Frame(body, style="Card.TFrame")
        language_row.pack(fill="x", pady=(0, 9))
        ttk.Label(language_row, text="Source language").pack(side="left")
        self.source_language_combo = ttk.Combobox(
            language_row,
            textvariable=self.source_language_var,
            values=list(SOURCE_LANGUAGE_LABELS),
            state="readonly",
            width=28,
        )
        self.source_language_combo.pack(side="left", padx=(8, 22))
        self.source_language_combo.bind("<<ComboboxSelected>>", self.language_changed)
        ttk.Label(language_row, text="Target language").pack(side="left")
        self.target_language_combo = ttk.Combobox(
            language_row,
            textvariable=self.target_language_var,
            values=list(TARGET_LANGUAGE_LABELS),
            state="readonly",
            width=22,
        )
        self.target_language_combo.pack(side="left", padx=(8, 0))
        self.target_language_combo.bind("<<ComboboxSelected>>", self.language_changed)
        ttk.Label(
            language_row,
            text="Mods already in the target language are disabled automatically.",
            foreground="#526779",
        ).pack(side="left", padx=(18, 0))

        tree_frame = ttk.Frame(body, style="Card.TFrame")
        tree_frame.pack(fill="both", expand=True)
        columns = ("check", "name", "language", "strings", "status", "version", "path")
        self.mod_tree = ttk.Treeview(
            tree_frame,
            columns=columns,
            show="headings",
            style="Mods.Treeview",
            selectmode="browse",
        )
        headings = {
            "check": "Translate",
            "name": "Mod",
            "language": "Detected source",
            "strings": "Strings",
            "status": "Status",
            "version": "Version",
            "path": "Path",
        }
        widths = {"check": 72, "name": 185, "language": 155, "strings": 62, "status": 250, "version": 85, "path": 300}
        anchors = {"check": "center", "strings": "e", "version": "center"}
        for column in columns:
            self.mod_tree.heading(column, text=headings[column])
            self.mod_tree.column(
                column,
                width=widths[column],
                minwidth=55,
                anchor=anchors.get(column, "w"),
                stretch=column in {"name", "status", "path"},
            )
        y_scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.mod_tree.yview)
        x_scroll = ttk.Scrollbar(tree_frame, orient="horizontal", command=self.mod_tree.xview)
        self.mod_tree.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
        self.mod_tree.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll.grid(row=1, column=0, sticky="ew")
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)
        self.mod_tree.tag_configure("disabled", foreground="#8b9299")
        self.mod_tree.tag_configure("ready", foreground="#22384d")
        self.mod_tree.bind("<Button-1>", self.tree_clicked)
        self.mod_tree.bind("<Double-1>", self.tree_double_clicked)
        self.mod_tree.bind("<space>", self.tree_space_pressed)

        select_row = ttk.Frame(body, style="Card.TFrame")
        select_row.pack(fill="x", pady=(7, 10))
        self.select_all_button = ttk.Button(select_row, text="Select All Translatable", command=self.select_all)
        self.select_all_button.pack(side="left")
        self.clear_button = ttk.Button(select_row, text="Clear Selection", command=self.clear_selection)
        self.clear_button.pack(side="left", padx=(7, 0))
        ttk.Label(select_row, textvariable=self.selection_var, foreground="#526779").pack(side="right")

        options_row = ttk.Frame(body, style="Card.TFrame")
        options_row.pack(fill="x", pady=(0, 8))
        ttk.Label(options_row, text="2. Translation provider", font=("Segoe UI", 10, "bold")).pack(
            side="left", padx=(0, 10)
        )
        self.provider_combo = ttk.Combobox(
            options_row,
            textvariable=self.provider_var,
            values=list(PROVIDER_LABELS),
            state="readonly",
            width=31,
        )
        self.provider_combo.pack(side="left")
        self.provider_combo.bind("<<ComboboxSelected>>", lambda _event: self.apply_provider())
        self.provider_hint = ttk.Label(options_row, text="Localization text stays on this PC", foreground="#3f7750")
        self.provider_hint.pack(side="left", padx=(12, 0))
        self.advanced_button = ttk.Button(options_row, text="Advanced Settings…", command=self.toggle_advanced)
        self.advanced_button.pack(side="right")

        action_row = ttk.Frame(body, style="Card.TFrame")
        action_row.pack(fill="x")
        self.start_button = ttk.Button(
            action_row,
            text="3. Translate Selected Mods",
            style="Accent.TButton",
            command=self.start,
        )
        self.start_button.pack(side="left")
        self.cancel_button = ttk.Button(action_row, text="Cancel", command=self.cancel, state="disabled")
        self.cancel_button.pack(side="left", padx=(9, 0))
        self.open_button = ttk.Button(action_row, text="Open Output Folder", command=self.open_output, state="disabled")
        self.open_button.pack(side="right")
        self.progress = ttk.Progressbar(body, mode="determinate", maximum=100)
        self.progress.pack(fill="x", pady=(11, 4))
        ttk.Label(body, textvariable=self.status_var, foreground="#34495e").pack(anchor="w")
        self.log = scrolledtext.ScrolledText(
            body,
            height=5,
            font=("Consolas", 9),
            bg="#f7f9fb",
            fg="#34495e",
            relief="solid",
            borderwidth=1,
            state="disabled",
        )
        self.log.pack(fill="x", pady=(8, 0))
        self.append_log("Ready. Language detection and file validation never use an LLM.")
        self.build_advanced_window()

    def build_advanced_window(self) -> None:
        self.advanced_window = tk.Toplevel(self.root)
        self.advanced_window.withdraw()
        self.advanced_window.title(f"Advanced Settings — {APP_NAME}")
        self.advanced_window.geometry("760x420")
        self.advanced_window.minsize(680, 390)
        self.advanced_window.transient(self.root)
        self.advanced_window.protocol("WM_DELETE_WINDOW", self.toggle_advanced)
        self.advanced = ttk.LabelFrame(
            self.advanced_window,
            text="Translation and output",
            style="Card.TLabelframe",
            padding=18,
        )
        self.advanced.pack(fill="both", expand=True, padx=16, pady=16)
        self.advanced.columnconfigure(1, weight=1)
        self.endpoint_label = ttk.Label(self.advanced, text="API endpoint")
        self.endpoint_label.grid(row=0, column=0, sticky="w", padx=(0, 10), pady=4)
        self.endpoint_entry = ttk.Entry(self.advanced, textvariable=self.endpoint_var)
        self.endpoint_entry.grid(row=0, column=1, sticky="ew", pady=4)
        self.refresh_button = ttk.Button(self.advanced, text="Refresh Models", command=self.refresh_models)
        self.refresh_button.grid(row=0, column=2, padx=(8, 0), pady=4)
        self.api_key_label = ttk.Label(self.advanced, text="API key")
        self.api_key_label.grid(row=1, column=0, sticky="w", padx=(0, 10), pady=4)
        self.api_key_entry = ttk.Entry(self.advanced, textvariable=self.api_key_var, show="●")
        self.api_key_entry.grid(row=1, column=1, sticky="ew", pady=4)
        self.delete_key_button = ttk.Button(self.advanced, text="Delete Saved Key", command=self.delete_saved_key)
        self.delete_key_button.grid(row=1, column=2, padx=(8, 0), pady=4)
        self.remember_key_check = ttk.Checkbutton(
            self.advanced,
            text="Save securely in Windows Credential Manager on this PC",
            variable=self.remember_key_var,
        )
        self.remember_key_check.grid(row=2, column=1, columnspan=2, sticky="w", pady=(0, 4))
        ttk.Label(self.advanced, text="Translation model").grid(row=3, column=0, sticky="w", padx=(0, 10), pady=4)
        self.model_combo = ttk.Combobox(self.advanced, textvariable=self.model_var, state="normal")
        self.model_combo.grid(row=3, column=1, columnspan=2, sticky="ew", pady=4)
        ttk.Label(self.advanced, text="Parallel requests").grid(row=4, column=0, sticky="w", padx=(0, 10), pady=4)
        self.workers_spin = ttk.Spinbox(self.advanced, from_=1, to=8, textvariable=self.workers_var, width=8)
        self.workers_spin.grid(row=4, column=1, sticky="w", pady=4)
        ttk.Label(self.advanced, text="Output folder").grid(row=5, column=0, sticky="w", padx=(0, 10), pady=4)
        output_row = ttk.Frame(self.advanced, style="Card.TFrame")
        output_row.grid(row=5, column=1, columnspan=2, sticky="ew", pady=4)
        output_row.columnconfigure(0, weight=1)
        self.output_entry = ttk.Entry(output_row, textvariable=self.output_parent_var)
        self.output_entry.grid(row=0, column=0, sticky="ew")
        self.output_button = ttk.Button(output_row, text="Choose…", command=self.choose_output)
        self.output_button.grid(row=0, column=1, padx=(8, 0))
        ttk.Label(
            self.advanced,
            text="One translated mod folder and one launcher .mod file are created per selected mod.",
            foreground="#5e6b7a",
        ).grid(row=6, column=1, columnspan=2, sticky="w", pady=(0, 4))
        self.server_status_label = ttk.Label(
            self.advanced,
            textvariable=self.server_var,
            foreground="#45627d",
            wraplength=680,
            justify="left",
        )
        self.server_status_label.grid(
            row=7,
            column=0,
            columnspan=3,
            sticky="w",
            pady=(8, 0),
        )

    def load_settings(self) -> dict[str, object]:
        try:
            data = json.loads(self.settings_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def save_settings(self) -> None:
        if not self.persistence_enabled:
            return
        try:
            try:
                workers = max(1, min(8, int(self.workers_var.get())))
            except (TypeError, ValueError, tk.TclError):
                workers = 4
                self.workers_var.set(workers)
            self.models_by_provider[self.active_provider] = self.model_var.get().strip()
            if self.active_provider == "local":
                self.local_endpoint = self.endpoint_var.get().strip()
            persistable_local_endpoint = safe_local_endpoint(self.local_endpoint)
            self.settings_path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "provider": self.active_provider,
                "local_endpoint": persistable_local_endpoint,
                "models": self.models_by_provider,
                "workers": workers,
                "last_library": self.library_var.get().strip(),
                "output_parent": self.output_parent_var.get().strip(),
                # Retain v1 keys so upgrades and external wrappers keep working.
                "last_source": self.source_var.get().strip(),
                "last_output": self.output_var.get().strip(),
                "source_language": self.source_language_id(),
                "target_language": self.target_language_id(),
            }
            temporary = self.settings_path.with_suffix(f".{os.getpid()}.tmp")
            temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.replace(temporary, self.settings_path)
        except OSError:
            pass

    def provider_id(self) -> str:
        return PROVIDER_LABELS.get(self.provider_var.get(), "local")

    def selected_model(self) -> str | None:
        """Return a discovered or manually entered model ID without requiring discovery."""
        return self.model_var.get().strip() or None

    def current_api_key(self) -> str | None:
        """Return the transient provider key/token; it is never written to settings."""
        return self.api_key_var.get().strip() or None

    def apply_provider(self, initial: bool = False) -> None:
        previous = self.active_provider
        if not initial:
            self.models_by_provider[previous] = self.model_var.get().strip()
            self.api_keys_by_provider[previous] = self.api_key_var.get()
            if previous == "local":
                self.local_endpoint = self.endpoint_var.get().strip() or DEFAULT_ENDPOINT
        provider_id = self.provider_id()
        self.active_provider = provider_id
        provider = get_provider(provider_id)
        self.models_verified = False
        self.model_var.set(str(self.models_by_provider.get(provider_id, "")))
        self.model_combo.configure(values=self.available_models_by_provider.get(provider_id, []), state="normal")
        try:
            saved = load_api_key(provider_id) if self.persistence_enabled else None
        except OSError as exc:
            saved = None
            self.append_log(f"Credential read error: {exc}")
        self.api_key_var.set(self.api_keys_by_provider.get(provider_id, saved or ""))
        if provider.remote:
            self.endpoint_var.set(provider.chat_endpoint)
            self.endpoint_entry.configure(state="readonly")
            self.provider_hint.configure(text="Localization text is sent to the selected service", foreground="#a05a22")
            for widget in (self.api_key_label, self.api_key_entry, self.delete_key_button, self.remember_key_check):
                widget.grid()
            self.api_key_label.configure(text="API key")
            self.delete_key_button.configure(text="Delete Saved Key")
            self.remember_key_check.configure(text="Save securely in Windows Credential Manager on this PC")
            self.server_var.set("Enter an API key and model, then click Refresh Models.")
            if not self.advanced_visible():
                self.toggle_advanced()
            if saved and not initial:
                self.refresh_models()
        else:
            self.endpoint_var.set(self.local_endpoint or DEFAULT_ENDPOINT)
            self.endpoint_entry.configure(state="normal")
            self.provider_hint.configure(text="Localization text stays on this PC", foreground="#3f7750")
            for widget in (self.api_key_label, self.api_key_entry, self.delete_key_button, self.remember_key_check):
                widget.grid()
            self.api_key_label.configure(text="Local server token (optional)")
            self.delete_key_button.configure(text="Delete Saved Token")
            self.remember_key_check.configure(text="Save token securely in Windows Credential Manager on this PC")
            self.server_var.set(
                "Load a model and start the Local Server in LM Studio, then click Refresh Models. "
                "You can also type the exact model ID."
            )
            if not initial:
                self.refresh_models()

    def delete_saved_key(self) -> None:
        provider_id = self.provider_id()
        try:
            deleted = delete_api_key(provider_id)
            self.api_key_var.set("")
            self.api_keys_by_provider[provider_id] = ""
            noun = "server token" if provider_id == "local" else "API key"
            self.server_var.set(f"Saved {noun} deleted." if deleted else f"No saved {noun} was found.")
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Could not delete the saved credential.\n\n{exc}")

    def scan_default_library(self) -> None:
        folder = _ck3_mod_directory()
        if folder is None or not folder.is_dir():
            messagebox.showwarning(APP_NAME, "The default CK3 mod folder could not be found. Choose it manually instead.")
            return
        self.library_var.set(str(folder))
        self.begin_scan("default CK3 mod folder", lambda: scan_mod_library(folder), replace=True)

    def choose_library(self) -> None:
        initial = self.library_var.get().strip()
        selected = filedialog.askdirectory(title="Choose a folder containing CK3 mods", initialdir=initial or None)
        if selected:
            folder = Path(selected)
            self.library_var.set(str(folder))
            self.begin_scan(folder.name, lambda: scan_mod_library(folder), replace=True)

    def choose_mod_folder(self) -> None:
        initial = self.library_var.get().strip()
        selected = filedialog.askdirectory(title="Choose one CK3 mod folder", initialdir=initial or None)
        if selected:
            folder = Path(selected)
            self.begin_scan(folder.name, lambda: [scan_mod_folder(folder)], replace=False)

    def choose_source(self) -> None:  # v1 compatibility alias
        self.choose_mod_folder()

    def source_changed(self) -> None:  # v1 compatibility no-op
        return

    def choose_descriptors(self) -> None:
        initial = self.library_var.get().strip()
        selected = filedialog.askopenfilenames(
            title="Choose CK3 launcher .mod files",
            initialdir=initial or None,
            filetypes=(("CK3 mod descriptors", "*.mod"), ("All files", "*.*")),
        )
        if selected:
            paths = [Path(value) for value in selected]
            self.begin_scan(
                f"{len(paths)} descriptor file(s)",
                lambda: [scan_descriptor(path) for path in paths],
                replace=False,
            )

    def begin_scan(self, label: str, scan: Callable[[], list[ModCandidate]], *, replace: bool) -> None:
        if self.running or self.scanning:
            return
        self.scan_generation += 1
        generation = self.scan_generation
        self.scanning = True
        self.update_control_states()
        self.progress.configure(mode="indeterminate")
        self.progress.start(12)
        self.status_var.set(f"Scanning {label} and detecting localization languages…")
        self.append_log(f"Scanning: {label}")

        def work() -> None:
            try:
                candidates = scan()
                self.events.put(
                    {
                        "event": "scan_done",
                        "generation": generation,
                        "candidates": candidates,
                        "replace": replace,
                        "label": label,
                    }
                )
            except Exception as exc:  # noqa: BLE001 - scanner failures must be visible
                self.events.put({"event": "scan_error", "generation": generation, "message": str(exc)})

        threading.Thread(target=work, daemon=True).start()

    def add_candidates(self, candidates: Iterable[ModCandidate], *, replace: bool) -> None:
        if replace:
            self.candidates.clear()
            self.checked.clear()
        for candidate in candidates:
            key = candidate.candidate_id
            if candidate.root:
                duplicate = next(
                    (
                        existing_id
                        for existing_id, existing in self.candidates.items()
                        if existing.root and str(existing.root).casefold() == str(candidate.root).casefold()
                    ),
                    None,
                )
                if duplicate and duplicate != key:
                    self.candidates.pop(duplicate, None)
                    self.checked.discard(duplicate)
            self.candidates[key] = candidate
        self.render_candidates()

    def candidate_state(self, candidate: ModCandidate) -> tuple[bool, LocalizationInfo | None, str, str]:
        if not candidate.valid:
            return False, None, "Unknown", f"Invalid: {candidate.reason}"
        if candidate.is_non_linguistic:
            return False, None, "Non-linguistic", "No natural-language localization found"
        requested = self.source_language_id()
        source = candidate.choose_source(requested, self.target_language_id())
        if source is None:
            requested_name = language_spec(requested).display_name
            return False, None, self.detected_language_summary(candidate), f"No detected {requested_name} source localization"
        detected = source.detected_language
        stored_detail = f"stored as {source.stored_as}"
        if requested == AUTO_LANGUAGE_ID and source.confidence < 0.5:
            return (
                False,
                source,
                f"{detected} (low confidence)",
                f"Low-confidence detection ({stored_detail}) — choose Source language explicitly",
            )
        if source.confidence < 0.55:
            detected = f"{detected} (low confidence)"
        explicit_override = requested != AUTO_LANGUAGE_ID and requested != source.detected_language_id
        if explicit_override:
            override_name = language_spec(requested).display_name
            stored_detail += f", manually treated as {override_name}"
        # An explicit source selection is authoritative when correcting a low-confidence guess.
        effective_source_id = requested if requested != AUTO_LANGUAGE_ID else source.detected_language_id
        if effective_source_id == self.target_language_id():
            return False, source, detected, f"Already {language_spec(self.target_language_id()).display_name} ({stored_detail})"
        return True, source, detected, f"Ready — {stored_detail}"

    @staticmethod
    def detected_language_summary(candidate: ModCandidate) -> str:
        names: list[str] = []
        for item in candidate.localizations:
            if item.detected_language not in names:
                names.append(item.detected_language)
        return ", ".join(names) if names else "Non-linguistic"

    def render_candidates(self) -> None:
        for item in self.mod_tree.get_children():
            self.mod_tree.delete(item)
        eligible_ids: set[str] = set()
        rows = sorted(self.candidates.values(), key=lambda item: (not item.valid, item.name.casefold(), item.candidate_id))
        for candidate in rows:
            eligible, source, detected, status = self.candidate_state(candidate)
            if eligible:
                eligible_ids.add(candidate.candidate_id)
            else:
                self.checked.discard(candidate.candidate_id)
            checked = candidate.candidate_id in self.checked
            version = candidate.version if candidate.version != "—" else candidate.supported_version
            path = str(candidate.root or candidate.descriptor or "")
            self.mod_tree.insert(
                "",
                "end",
                iid=candidate.candidate_id,
                values=(
                    "☑" if checked else "☐",
                    candidate.name,
                    detected,
                    source.translatable_entries if source else 0,
                    status,
                    version,
                    path,
                ),
                tags=("ready" if eligible else "disabled",),
            )
        self.checked.intersection_update(eligible_ids)
        self.update_selection_text()

    def language_changed(self, _event: object | None = None) -> None:
        self.render_candidates()
        self.save_settings()

    def toggle_candidate(self, candidate_id: str) -> None:
        if self.running or self.scanning or candidate_id not in self.candidates:
            return
        eligible, _source, _detected, status = self.candidate_state(self.candidates[candidate_id])
        if not eligible:
            self.status_var.set(status)
            return
        if candidate_id in self.checked:
            self.checked.remove(candidate_id)
        else:
            self.checked.add(candidate_id)
        self.render_candidates()

    def tree_clicked(self, event: tk.Event[tk.Misc]) -> None:
        if self.mod_tree.identify_region(event.x, event.y) != "cell":
            return
        row = self.mod_tree.identify_row(event.y)
        if row and self.mod_tree.identify_column(event.x) == "#1":
            self.root.after_idle(lambda: self.toggle_candidate(row))

    def tree_double_clicked(self, event: tk.Event[tk.Misc]) -> None:
        row = self.mod_tree.identify_row(event.y)
        if row:
            self.root.after_idle(lambda: self.toggle_candidate(row))

    def tree_space_pressed(self, _event: tk.Event[tk.Misc]) -> str:
        selected = self.mod_tree.selection()
        if selected:
            self.toggle_candidate(selected[0])
        return "break"

    def select_all(self) -> None:
        if self.running or self.scanning:
            return
        self.checked = {
            candidate_id
            for candidate_id, candidate in self.candidates.items()
            if self.candidate_state(candidate)[0]
        }
        self.render_candidates()

    def clear_selection(self) -> None:
        if self.running or self.scanning:
            return
        self.checked.clear()
        self.render_candidates()

    def update_selection_text(self) -> None:
        eligible = sum(self.candidate_state(candidate)[0] for candidate in self.candidates.values())
        if not self.candidates:
            text = "No mods loaded"
        else:
            text = f"{len(self.checked)} selected · {eligible} translatable · {len(self.candidates)} found"
        self.selection_var.set(text)

    def choose_output(self) -> None:
        initial = self.output_parent_var.get().strip() or self.library_var.get().strip()
        selected = filedialog.askdirectory(title="Choose the parent folder for translated mods", initialdir=initial or None)
        if selected:
            self.output_parent_var.set(selected)

    def output_for(self, candidate: ModCandidate) -> Path:
        parent_text = self.output_parent_var.get().strip()
        fallback_parent = (candidate.root or Path.cwd()).parent
        parent = Path(parent_text) if parent_text else (_ck3_mod_directory() or fallback_parent)
        source_name = (candidate.root or Path(candidate.name)).name
        suffix = _safe_output_suffix(language_spec(self.target_language_id()).display_name)
        return parent / f"{source_name}_{suffix}"

    def toggle_advanced(self) -> None:
        if self.advanced_visible():
            self.advanced_window.withdraw()
            self.advanced_is_open = False
        else:
            self.advanced_window.deiconify()
            self.advanced_window.lift()
            self.advanced_window.focus_set()
            self.advanced_is_open = True

    def advanced_visible(self) -> bool:
        return self.advanced_is_open

    def refresh_models(self) -> None:
        if self.running:
            return
        provider_id = self.provider_id()
        provider = get_provider(provider_id)
        api_key = self.current_api_key()
        if provider.requires_key and not api_key:
            self.server_var.set("Enter an API key first.")
            self.api_key_entry.focus_set()
            return
        self.models_verified = False
        self.server_var.set(f"Connecting to {PROVIDER_DISPLAY.get(provider_id, provider.label)}…")
        self.refresh_button.configure(state="disabled")
        endpoint = self.endpoint_var.get().strip()
        try:
            validate_user_endpoint(provider_id, endpoint)
        except ValueError:
            self.server_var.set(endpoint_error_message(provider_id))
            self.provider_hint.configure(
                text="Invalid endpoint — open Advanced Settings",
                foreground="#a05a22",
            )
            self.refresh_button.configure(state="normal")
            self.endpoint_entry.focus_set()
            return

        def work() -> None:
            try:
                models = clone_engine.discover_models(endpoint, provider=provider_id, api_key=api_key)
                self.events.put({"event": "models", "models": models, "provider": provider_id})
            except Exception as exc:  # noqa: BLE001 - connection failures must be visible
                self.events.put({"event": "models_error", "message": str(exc), "provider": provider_id})

        threading.Thread(target=work, daemon=True).start()

    def selected_jobs(self) -> list[tuple[ModCandidate, LocalizationInfo, Path, str]]:
        requested = self.source_language_id()
        explicit_source = language_spec(requested).llm_name if requested != AUTO_LANGUAGE_ID else None
        jobs: list[tuple[ModCandidate, LocalizationInfo, Path, str]] = []
        for candidate_id in self.checked:
            candidate = self.candidates.get(candidate_id)
            if not candidate:
                continue
            eligible, source, _detected, _status = self.candidate_state(candidate)
            if eligible and source and candidate.root:
                jobs.append((candidate, source, self.output_for(candidate), explicit_source or source.detected_language))
        return sorted(jobs, key=lambda job: job[0].name.casefold())

    def start(self) -> None:
        jobs = self.selected_jobs()
        if not jobs:
            messagebox.showwarning(APP_NAME, "Select at least one translatable mod first.")
            return
        collision_groups: dict[str, list[str]] = {}
        for candidate, _source, output, _source_language in jobs:
            collision_groups.setdefault(str(output.resolve()).casefold(), []).append(candidate.name)
        collisions = [names for names in collision_groups.values() if len(names) > 1]
        if collisions:
            details = "\n".join(" • " + ", ".join(names) for names in collisions)
            messagebox.showerror(
                APP_NAME,
                "Selected mods would use the same output path. Translate one of each colliding group separately:\n\n" + details,
            )
            return

        provider_id = self.provider_id()
        provider = get_provider(provider_id)
        provider_name = PROVIDER_DISPLAY.get(provider_id, provider.label)
        api_key = self.current_api_key()
        selected_model = self.selected_model()
        endpoint = self.endpoint_var.get().strip()
        try:
            validate_user_endpoint(provider_id, endpoint)
        except ValueError:
            if not self.advanced_visible():
                self.toggle_advanced()
            self.server_var.set(endpoint_error_message(provider_id))
            self.provider_hint.configure(
                text="Invalid endpoint — no settings or credentials were saved",
                foreground="#a05a22",
            )
            self.endpoint_entry.focus_set()
            messagebox.showerror(
                APP_NAME,
                "The API endpoint is not allowed.\n\n"
                f"{endpoint_error_message(provider_id)}\n\n"
                "No settings or credentials were saved.",
            )
            return
        if provider.requires_key and not api_key:
            messagebox.showwarning(APP_NAME, f"Enter an API key for {provider_name}.")
            if not self.advanced_visible():
                self.toggle_advanced()
            self.api_key_entry.focus_set()
            return
        if provider.remote and not selected_model:
            messagebox.showwarning(APP_NAME, "Enter or select a translation model.")
            if not self.advanced_visible():
                self.toggle_advanced()
            self.model_combo.focus_set()
            return
        if provider.remote and not messagebox.askyesno(
            APP_NAME,
            f"Use {provider_name}?\n\nLocalization text will be sent to this service. Charges and provider terms may apply. "
            "API keys and translation text are not written to the application log.\n\nContinue?",
            icon="warning",
        ):
            return
        existing_keys = {
            str(output.resolve()).casefold()
            for _candidate, _source, output, _source_language in jobs
            if output.exists() or (output.parent / f"{output.name}.mod").exists()
        }
        if existing_keys and not messagebox.askyesno(
            APP_NAME,
            f"{len(existing_keys)} translated mod(s) already exist.\n\nBack up and replace the existing copies?",
            icon="warning",
        ):
            return
        try:
            workers = max(1, min(8, int(self.workers_var.get())))
        except (ValueError, tk.TclError):
            workers = 4
        if self.persistence_enabled:
            try:
                if self.remember_key_var.get() and api_key:
                    save_api_key(provider_id, api_key)
                else:
                    delete_api_key(provider_id)
            except OSError as exc:
                messagebox.showerror(APP_NAME, f"Could not update the credential in Windows Credential Manager.\n\n{exc}")
                return
        self.save_settings()
        self.set_running(True)
        self.cancel_event.clear()
        self.progress.configure(mode="determinate", value=0)
        target = language_spec(self.target_language_id())
        model_for_engine = selected_model
        self.status_var.set(f"Preparing {len(jobs)} mod(s) for {target.display_name} translation…")
        self.append_log(f"Starting {len(jobs)} mod(s) · provider: {provider_name} · target: {target.display_name}")

        def work() -> None:
            results: list[object] = []
            failures: list[tuple[str, str]] = []
            for index, (candidate, source, output, source_language) in enumerate(jobs):
                if self.cancel_event.is_set():
                    self.events.put({"event": "batch_cancelled", "completed": len(results), "failures": failures})
                    return
                self.events.put({"event": "mod_started", "index": index, "total": len(jobs), "name": candidate.name})

                def progress(
                    payload: dict[str, object],
                    *,
                    current: int = index,
                    name: str = candidate.name,
                ) -> None:
                    self.events.put(
                        {"event": "clone_progress", "index": current, "total": len(jobs), "name": name, "payload": payload}
                    )

                options = _clone_options(
                    source=candidate.root,
                    output=output,
                    endpoint=endpoint,
                    provider=provider_id,
                    api_key=api_key,
                    model=model_for_engine,
                    workers=workers,
                    overwrite=str(output.resolve()).casefold() in existing_keys,
                    source_locale=source.locale_id,
                    source_language=source_language,
                    language=target.llm_name,
                    locale=target.language_id,
                )
                try:
                    result = _clone_function()(options, progress, self.cancel_event)
                    results.append(result)
                except clone_engine.CancelledError:
                    self.events.put({"event": "batch_cancelled", "completed": len(results), "failures": failures})
                    return
                except Exception as exc:  # noqa: BLE001 - independent mods continue after a failure
                    failures.append((candidate.name, str(exc)))
                    self.events.put({"event": "mod_error", "name": candidate.name, "message": str(exc)})
            self.events.put({"event": "batch_done", "results": results, "failures": failures, "total": len(jobs)})

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def cancel(self) -> None:
        if self.running and messagebox.askyesno(
            APP_NAME,
            "Cancel the current batch?\nCompleted translated mods and cached translations will be kept.",
        ):
            self.cancel_event.set()
            self.cancel_button.configure(state="disabled")
            self.status_var.set("Cancelling after the current translation request finishes…")

    def update_control_states(self) -> None:
        busy = self.running or self.scanning
        normal = "disabled" if busy else "normal"
        for widget in (
            self.default_scan_button,
            self.library_scan_button,
            self.add_folder_button,
            self.add_descriptor_button,
            self.select_all_button,
            self.clear_button,
            self.advanced_button,
            self.start_button,
            self.refresh_button,
            self.workers_spin,
            self.output_entry,
            self.output_button,
            self.api_key_entry,
            self.delete_key_button,
            self.remember_key_check,
        ):
            widget.configure(state=normal)
        if busy:
            self.provider_combo.configure(state="disabled")
            self.source_language_combo.configure(state="disabled")
            self.target_language_combo.configure(state="disabled")
            self.endpoint_entry.configure(state="disabled")
            self.model_combo.configure(state="disabled")
        else:
            self.provider_combo.configure(state="readonly")
            self.source_language_combo.configure(state="readonly")
            self.target_language_combo.configure(state="readonly")
            remote = get_provider(self.provider_id()).remote
            self.endpoint_entry.configure(state="readonly" if remote else "normal")
            self.model_combo.configure(state="normal")
        self.cancel_button.configure(state="normal" if self.running else "disabled")

    def set_running(self, running: bool) -> None:
        self.running = running
        self.update_control_states()

    def append_log(self, message: str) -> None:
        secrets = {value.strip() for value in self.api_keys_by_provider.values() if value.strip()}
        current = self.api_key_var.get().strip()
        if current:
            secrets.add(current)
        for secret in sorted(secrets, key=len, reverse=True):
            message = message.replace(secret, "[API KEY HIDDEN]")
        self.log.configure(state="normal")
        self.log.insert("end", message.rstrip() + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")
        if self.persistence_enabled:
            try:
                self.log_path.parent.mkdir(parents=True, exist_ok=True)
                with self.log_path.open("a", encoding="utf-8") as handle:
                    timestamp = dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds")
                    handle.write(f"{timestamp} {message.rstrip()}\n")
            except OSError:
                pass

    @staticmethod
    def local_progress(payload: dict[str, object]) -> float:
        kind = str(payload.get("event", ""))
        if kind == "translation_started":
            total = int(payload.get("entries", 0))
            hits = int(payload.get("cache_hits", 0))
            return min(82.0, hits / total * 82.0 if total else 5.0)
        if kind == "translation_progress":
            completed = int(payload.get("completed_batches", 0))
            batches = int(payload.get("total_batches", 0))
            return 5.0 + (completed / batches * 78.0 if batches else 78.0)
        return {
            "translation_validated": 87.0,
            "copying": 91.0,
            "verifying": 96.0,
            "done": 100.0,
        }.get(kind, 2.0)

    def handle_clone_progress(self, event: dict[str, object]) -> None:
        payload = event.get("payload")
        if not isinstance(payload, dict):
            return
        index = int(event.get("index", 0))
        total = max(1, int(event.get("total", 1)))
        name = str(event.get("name", "mod"))
        local = self.local_progress(payload)
        self.progress.configure(value=(index + local / 100.0) / total * 100.0)
        kind = str(payload.get("event", ""))
        if kind == "model_selected":
            self.model_var.set(str(payload.get("model", "")))
            self.append_log(f"Model: {self.model_var.get()}")
        elif kind == "translation_started":
            entries = int(payload.get("entries", 0))
            pending = int(payload.get("pending", 0))
            hits = int(payload.get("cache_hits", 0))
            self.status_var.set(f"{name}: translating {entries} strings ({pending} pending, {hits} cached)")
        elif kind == "translation_progress":
            completed = int(payload.get("completed_batches", 0))
            batches = int(payload.get("total_batches", 0))
            translated = int(payload.get("translated", 0))
            self.status_var.set(f"{name}: batch {completed}/{batches} ({translated} strings translated)")
        elif kind == "translation_validated":
            self.status_var.set(f"{name}: translated localization passed validation")
        elif kind in {"copying", "verifying"}:
            self.status_var.set(f"{name}: {payload.get('message', kind.title())}")
        elif kind == "log":
            self.append_log(str(payload.get("message", "")))

    def handle_event(self, event: dict[str, object]) -> None:
        kind = str(event.get("event", ""))
        if kind == "scan_done":
            if int(event.get("generation", -1)) != self.scan_generation:
                return
            self.scanning = False
            self.progress.stop()
            self.progress.configure(mode="determinate", value=0)
            candidates = event.get("candidates", [])
            self.add_candidates(candidates if isinstance(candidates, list) else [], replace=bool(event.get("replace")))
            invalid = sum(not candidate.valid for candidate in self.candidates.values())
            non_linguistic = sum(candidate.is_non_linguistic for candidate in self.candidates.values())
            self.status_var.set(
                f"Found {len(self.candidates)} mod(s): {invalid} invalid, {non_linguistic} non-linguistic. "
                "Select mods to translate."
            )
            self.append_log(f"Scan complete: {len(self.candidates)} mod(s) listed")
            self.update_control_states()
        elif kind == "scan_error":
            if int(event.get("generation", -1)) != self.scan_generation:
                return
            self.scanning = False
            self.progress.stop()
            self.progress.configure(mode="determinate", value=0)
            self.status_var.set("Scan failed. No mod files were changed.")
            self.append_log(f"Scan error: {event.get('message', '')}")
            self.update_control_states()
            messagebox.showerror(APP_NAME, f"Could not scan the selected folder.\n\n{event.get('message', '')}")
        elif kind == "models":
            if event.get("provider") != self.provider_id():
                return
            raw_models = event.get("models", [])
            models = [str(value) for value in raw_models] if isinstance(raw_models, list) else []
            provider_id = self.provider_id()
            self.available_models_by_provider[provider_id] = models
            typed_model = self.selected_model()
            displayed_models = ([typed_model] if typed_model and typed_model not in models else []) + models
            self.model_combo.configure(values=displayed_models)
            if models:
                self.models_verified = True
                if not typed_model:
                    self.model_var.set(models[0])
                selected = self.model_var.get().strip()
                suffix = f" ({selected})" if selected else ". Select a model."
                self.server_var.set(f"Connected: {len(models)} model(s){suffix}")
                if provider_id == "local":
                    self.provider_hint.configure(
                        text=f"Localization text stays on this PC · {len(models)} model(s) available",
                        foreground="#3f7750",
                    )
            else:
                self.models_verified = False
                if provider_id == "local":
                    self.server_var.set(
                        "Connected, but the server reported no models. Load a model in LM Studio and keep the "
                        "Local Server running, or type the exact model ID manually."
                    )
                    self.provider_hint.configure(
                        text="No local model reported — open Advanced Settings",
                        foreground="#a05a22",
                    )
                else:
                    self.server_var.set("No models returned. Enter the exact remote model ID manually.")
            self.refresh_button.configure(state="normal" if not self.running else "disabled")
        elif kind == "models_error":
            if event.get("provider") != self.provider_id():
                return
            self.models_verified = False
            provider_id = self.provider_id()
            provider_name = PROVIDER_DISPLAY.get(provider_id, provider_id)
            error_message = str(event.get("message", ""))
            if provider_id == "local":
                self.server_var.set(local_server_error_message(error_message, self.endpoint_var.get().strip()))
                self.provider_hint.configure(
                    text="LM Studio not connected — open Advanced Settings",
                    foreground="#a05a22",
                )
            else:
                self.server_var.set(f"Not connected: check {provider_name} settings or enter the model ID manually")
            self.append_log(f"Connection check: {error_message}")
            self.refresh_button.configure(state="normal" if not self.running else "disabled")
        elif kind == "mod_started":
            index = int(event.get("index", 0)) + 1
            total = int(event.get("total", 1))
            self.status_var.set(f"Mod {index}/{total}: {event.get('name', '')}")
            self.append_log(f"Translating [{index}/{total}] {event.get('name', '')}")
        elif kind == "clone_progress":
            self.handle_clone_progress(event)
        elif kind == "mod_error":
            self.append_log(f"Failed: {event.get('name', '')}: {event.get('message', '')}")
        elif kind == "batch_cancelled":
            completed = int(event.get("completed", 0))
            self.finish_state(f"Cancelled. {completed} translated mod(s) were completed and kept.")
            self.append_log("Translation batch cancelled")
        elif kind == "batch_done":
            raw_results = event.get("results", [])
            results = raw_results if isinstance(raw_results, list) else []
            raw_failures = event.get("failures", [])
            failures = raw_failures if isinstance(raw_failures, list) else []
            self.last_outputs = [result.output for result in results if hasattr(result, "output")]
            self.progress.configure(value=100)
            self.finish_state(f"Finished: {len(results)} completed, {len(failures)} failed.")
            self.open_button.configure(state="normal" if self.last_outputs else "disabled")
            for result in results:
                if hasattr(result, "output"):
                    self.append_log(f"Completed: {result.output}")
            details = ""
            if failures:
                details = "\n\nFailed:\n" + "\n".join(
                    f"• {name}: {message}" for name, message in failures[:8] if isinstance(name, str)
                )
            messagebox.showinfo(
                APP_NAME,
                f"Translation batch finished.\n\nCompleted: {len(results)}\nFailed: {len(failures)}{details}",
            )

    def finish_state(self, status: str) -> None:
        self.progress.stop()
        self.progress.configure(mode="determinate")
        self.status_var.set(status)
        self.set_running(False)

    def poll_events(self) -> None:
        try:
            while True:
                self.handle_event(self.events.get_nowait())
        except queue.Empty:
            pass
        self.root.after(100, self.poll_events)

    def open_output(self) -> None:
        folder = Path(self.output_parent_var.get().strip()) if self.output_parent_var.get().strip() else None
        if not folder and self.last_outputs:
            folder = self.last_outputs[-1].parent
        if not folder:
            return
        try:
            os.startfile(folder)  # type: ignore[attr-defined]
        except OSError as exc:
            messagebox.showerror(APP_NAME, str(exc))

    def on_close(self) -> None:
        if self.running:
            if messagebox.askyesno(APP_NAME, "Translation is running. Cancel it before closing?"):
                self.cancel_event.set()
                self.status_var.set("Cancelling…")
            return
        self.save_settings()
        self.root.destroy()


# Source compatibility for integrations that imported the v1 class name.
JapaneseModMaker = CK3ModTranslator


def enable_windows_dpi_awareness() -> None:
    if os.name != "nt":
        return
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass


def main() -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--smoke-test", action="store_true")
    parser.add_argument("--smoke-test-output")
    parser.add_argument("--headless-source")
    parser.add_argument("--headless-output")
    parser.add_argument("--headless-result")
    parser.add_argument("--source-language", default="english")
    parser.add_argument("--source-locale")
    parser.add_argument("--target-language", default="japanese")
    parser.add_argument("--target-locale")
    parser.add_argument("--provider", choices=tuple(PROVIDERS), default="local")
    parser.add_argument("--endpoint")
    parser.add_argument("--model")
    parser.add_argument("--api-key-env")
    parser.add_argument("--work-root")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--overwrite", action="store_true")
    args, _unknown = parser.parse_known_args()
    if args.headless_source:
        if not args.headless_output or not args.headless_result:
            raise SystemExit("--headless-output and --headless-result are required with --headless-source")
        result_path = Path(args.headless_result).resolve()
        source = language_spec(args.source_language)
        target = language_spec(args.target_language)
        try:
            result = _clone_function()(
                _clone_options(
                    source=Path(args.headless_source),
                    output=Path(args.headless_output),
                    endpoint=args.endpoint or get_provider(args.provider).chat_endpoint,
                    provider=args.provider,
                    api_key=os.environ.get(args.api_key_env) if args.api_key_env else None,
                    model=args.model,
                    workers=args.workers,
                    work_root=Path(args.work_root) if args.work_root else None,
                    overwrite=args.overwrite,
                    source_language=source.llm_name,
                    source_locale=args.source_locale or source.language_id,
                    language=target.llm_name,
                    locale=args.target_locale or target.language_id,
                )
            )
            payload = {"ok": True, "result": dataclasses.asdict(result)}
            result_path.parent.mkdir(parents=True, exist_ok=True)
            result_path.write_text(json.dumps(payload, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
            return
        except Exception as exc:
            result_path.parent.mkdir(parents=True, exist_ok=True)
            result_path.write_text(
                json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            raise SystemExit(1) from exc
    enable_windows_dpi_awareness()
    root = tk.Tk()
    if args.smoke_test:
        root.withdraw()
        app = CK3ModTranslator(root, auto_connect=False)
        root.update_idletasks()
        advanced_hidden = not app.advanced_visible()
        app.provider_var.set(PROVIDER_DISPLAY["openai"])
        app.apply_provider()
        root.update_idletasks()
        remote_ui = {
            "advanced_visible": app.advanced_visible(),
            "key_visible": bool(app.api_key_entry.winfo_manager()),
            "official_endpoint": app.endpoint_var.get() == PROVIDERS["openai"].chat_endpoint,
            "key_masked": bool(app.api_key_entry.cget("show")),
        }
        evidence = {
            "title": root.title(),
            "widgets": {
                "source": bool(app.source_entry.winfo_exists()),
                "browse": bool(app.browse_button.winfo_exists()),
                "start": bool(app.start_button.winfo_exists()),
                "progress": bool(app.progress.winfo_exists()),
                "log": bool(app.log.winfo_exists()),
                "provider": bool(app.provider_combo.winfo_exists()),
                "mod_tree": bool(app.mod_tree.winfo_exists()),
                "source_language": bool(app.source_language_combo.winfo_exists()),
                "target_language": bool(app.target_language_combo.winfo_exists()),
                "library_scan": bool(app.library_scan_button.winfo_exists()),
            },
            "language_defaults": {
                "source": app.source_language_var.get(),
                "target": app.target_language_var.get(),
            },
            "advanced_hidden": advanced_hidden,
            "remote_provider_ui": remote_ui,
        }
        serialized = json.dumps(evidence, ensure_ascii=False)
        if args.smoke_test_output:
            Path(args.smoke_test_output).write_text(serialized + "\n", encoding="utf-8")
        else:
            print(serialized)
        root.destroy()
        return
    CK3ModTranslator(root)
    root.mainloop()


if __name__ == "__main__":
    main()
