#!/usr/bin/env python3
"""Japanese desktop UI for the CK3 Japanese Mod Maker."""
from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import os
import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

from ck3_clone import (
    CancelledError,
    CloneOptions,
    create_japanese_clone,
    default_output,
    default_work_root,
    discover_models,
)
from ck3_providers import PROVIDERS, get_provider
from windows_credentials import delete_api_key, load_api_key, save_api_key

APP_NAME = "CK3 MOD 日本語化メーカー"
APP_VERSION = "1.0.0"
DEFAULT_ENDPOINT = "http://127.0.0.1:1234/v1/chat/completions"
PROVIDER_LABELS = {provider.label: provider_id for provider_id, provider in PROVIDERS.items()}


class JapaneseModMaker:
    def __init__(self, root: tk.Tk, auto_connect: bool = True) -> None:
        self.root = root
        self.events: queue.Queue[dict[str, object]] = queue.Queue()
        self.cancel_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.running = False
        self.models_verified = False
        self.advanced_is_open = False
        self.persistence_enabled = auto_connect
        self.last_output: Path | None = None
        self.settings_path = default_work_root() / "settings.json"
        local_today = dt.datetime.now(dt.timezone.utc).astimezone().date()
        self.log_path = default_work_root() / "logs" / f"{local_today.isoformat()}.log"

        settings = self.load_settings()
        initial_provider = str(settings.get("provider", "local"))
        if initial_provider not in PROVIDERS:
            initial_provider = "local"
        self.active_provider = initial_provider
        self.models_by_provider = dict(settings.get("models", {})) if isinstance(settings.get("models"), dict) else {}
        self.source_var = tk.StringVar(value=str(settings.get("last_source", "")))
        self.output_var = tk.StringVar(value=str(settings.get("last_output", "")))
        self.last_source_for_output = self.source_var.get().strip()
        self.last_auto_output = str(default_output(Path(self.last_source_for_output))) if self.last_source_for_output else ""
        self.provider_var = tk.StringVar(value=get_provider(initial_provider).label)
        self.api_key_var = tk.StringVar(value="")
        self.remember_key_var = tk.BooleanVar(value=True)
        self.local_endpoint = str(settings.get("local_endpoint", DEFAULT_ENDPOINT))
        initial_endpoint = self.local_endpoint if initial_provider == "local" else get_provider(initial_provider).chat_endpoint
        self.endpoint_var = tk.StringVar(value=initial_endpoint)
        self.model_var = tk.StringVar(value=str(self.models_by_provider.get(initial_provider, "")))
        try:
            initial_workers = max(1, min(8, int(settings.get("workers", 4))))
        except (TypeError, ValueError):
            initial_workers = 4
        self.workers_var = tk.IntVar(value=initial_workers)
        self.status_var = tk.StringVar(value="MODフォルダを選択してください")
        self.server_var = tk.StringVar(value="ローカルLLMを確認中…")
        self.output_hint_var = tk.StringVar(value="出力先はCK3のローカルMODフォルダへ自動設定されます")

        self.configure_window()
        self.build_ui()
        self.apply_provider(initial=True)
        if self.source_var.get() and not self.output_var.get():
            self.source_changed()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(100, self.poll_events)
        if auto_connect and (initial_provider == "local" or self.api_key_var.get()):
            self.refresh_models()
        else:
            self.server_var.set("スモークテスト")

    def configure_window(self) -> None:
        self.root.title(f"{APP_NAME}  v{APP_VERSION}")
        if os.name == "nt":
            try:
                icon_source = Path(sys.executable) if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent / "packaging" / "app.ico"
                if icon_source.is_file():
                    self.root.iconbitmap(default=str(icon_source))
            except tk.TclError:
                pass
        self.root.geometry("860x690")
        self.root.minsize(760, 600)
        self.root.configure(bg="#f3f5f8")
        self.root.update_idletasks()
        width, height = 860, 690
        x = max(0, (self.root.winfo_screenwidth() - width) // 2)
        y = max(0, (self.root.winfo_screenheight() - height) // 2)
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        style = ttk.Style(self.root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("TButton", font=("Yu Gothic UI", 10), padding=(12, 7))
        style.configure("Accent.TButton", font=("Yu Gothic UI", 11, "bold"), padding=(20, 11))
        style.configure("TLabel", background="#ffffff", font=("Yu Gothic UI", 10))
        style.configure("Card.TFrame", background="#ffffff")
        style.configure("Card.TLabelframe", background="#ffffff")
        style.configure("Card.TLabelframe.Label", background="#ffffff", font=("Yu Gothic UI", 10, "bold"))

    def build_ui(self) -> None:
        header = tk.Frame(self.root, bg="#27364b", height=112)
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Label(header, text=APP_NAME, bg="#27364b", fg="white", font=("Yu Gothic UI", 22, "bold")).pack(anchor="w", padx=32, pady=(18, 0))
        tk.Label(
            header,
            text="MODを選ぶだけ。元データを守りながら、日本語化した完全コピーを作成します。",
            bg="#27364b",
            fg="#dce6f3",
            font=("Yu Gothic UI", 10),
        ).pack(anchor="w", padx=34, pady=(4, 0))

        body = ttk.Frame(self.root, style="Card.TFrame", padding=24)
        body.pack(fill="both", expand=True, padx=24, pady=20)

        ttk.Label(body, text="1. 日本語化するCK3 MODフォルダ", font=("Yu Gothic UI", 12, "bold")).pack(anchor="w")
        source_row = ttk.Frame(body, style="Card.TFrame")
        source_row.pack(fill="x", pady=(8, 4))
        self.source_entry = ttk.Entry(source_row, textvariable=self.source_var, font=("Yu Gothic UI", 10))
        self.source_entry.pack(side="left", fill="x", expand=True, ipady=5)
        self.source_entry.bind("<FocusOut>", lambda _event: self.source_changed())
        self.browse_button = ttk.Button(source_row, text="フォルダを選択…", command=self.choose_source)
        self.browse_button.pack(side="left", padx=(10, 0))
        ttk.Label(body, textvariable=self.output_hint_var, foreground="#5e6b7a").pack(anchor="w", pady=(0, 14))

        provider_row = ttk.Frame(body, style="Card.TFrame")
        provider_row.pack(fill="x", pady=(0, 12))
        ttk.Label(provider_row, text="翻訳方式", font=("Yu Gothic UI", 11, "bold")).pack(side="left", padx=(0, 12))
        self.provider_combo = ttk.Combobox(
            provider_row,
            textvariable=self.provider_var,
            values=list(PROVIDER_LABELS),
            state="readonly",
            width=34,
        )
        self.provider_combo.pack(side="left")
        self.provider_combo.bind("<<ComboboxSelected>>", lambda _event: self.apply_provider())
        self.provider_hint = ttk.Label(provider_row, text="英文をPC外へ送信しません", foreground="#3f7750")
        self.provider_hint.pack(side="left", padx=(12, 0))

        self.advanced_button = ttk.Button(body, text="▶ 詳細設定（通常は変更不要）", command=self.toggle_advanced)
        self.advanced_button.pack(anchor="w", pady=(0, 8))
        self.advanced_window = tk.Toplevel(self.root)
        self.advanced_window.withdraw()
        self.advanced_window.title("詳細設定 - CK3 MOD 日本語化メーカー")
        self.advanced_window.geometry("700x380")
        self.advanced_window.minsize(620, 350)
        self.advanced_window.transient(self.root)
        self.advanced_window.protocol("WM_DELETE_WINDOW", self.toggle_advanced)
        self.advanced = ttk.LabelFrame(self.advanced_window, text="翻訳・出力設定", style="Card.TLabelframe", padding=18)
        self.advanced.pack(fill="both", expand=True, padx=16, pady=16)
        self.advanced.columnconfigure(1, weight=1)
        self.endpoint_label = ttk.Label(self.advanced, text="APIエンドポイント")
        self.endpoint_label.grid(row=0, column=0, sticky="w", padx=(0, 10), pady=4)
        self.endpoint_entry = ttk.Entry(self.advanced, textvariable=self.endpoint_var)
        self.endpoint_entry.grid(row=0, column=1, sticky="ew", pady=4)
        self.refresh_button = ttk.Button(self.advanced, text="モデル一覧を取得", command=self.refresh_models)
        self.refresh_button.grid(row=0, column=2, padx=(8, 0), pady=4)

        self.api_key_label = ttk.Label(self.advanced, text="APIキー")
        self.api_key_label.grid(row=1, column=0, sticky="w", padx=(0, 10), pady=4)
        self.api_key_entry = ttk.Entry(self.advanced, textvariable=self.api_key_var, show="●")
        self.api_key_entry.grid(row=1, column=1, sticky="ew", pady=4)
        self.delete_key_button = ttk.Button(self.advanced, text="保存キーを削除", command=self.delete_saved_key)
        self.delete_key_button.grid(row=1, column=2, padx=(8, 0), pady=4)
        self.remember_key_check = ttk.Checkbutton(
            self.advanced,
            text="Windows資格情報マネージャーに暗号化保存（このPCのみ）",
            variable=self.remember_key_var,
        )
        self.remember_key_check.grid(row=2, column=1, columnspan=2, sticky="w", pady=(0, 4))

        ttk.Label(self.advanced, text="翻訳モデル").grid(row=3, column=0, sticky="w", padx=(0, 10), pady=4)
        self.model_combo = ttk.Combobox(self.advanced, textvariable=self.model_var, state="readonly")
        self.model_combo.grid(row=3, column=1, columnspan=2, sticky="ew", pady=4)
        ttk.Label(self.advanced, text="並列数").grid(row=4, column=0, sticky="w", padx=(0, 10), pady=4)
        self.workers_spin = ttk.Spinbox(self.advanced, from_=1, to=8, textvariable=self.workers_var, width=8)
        self.workers_spin.grid(row=4, column=1, sticky="w", pady=4)
        ttk.Label(self.advanced, text="出力先").grid(row=5, column=0, sticky="w", padx=(0, 10), pady=4)
        output_row = ttk.Frame(self.advanced, style="Card.TFrame")
        output_row.grid(row=5, column=1, columnspan=2, sticky="ew", pady=4)
        output_row.columnconfigure(0, weight=1)
        self.output_entry = ttk.Entry(output_row, textvariable=self.output_var)
        self.output_entry.grid(row=0, column=0, sticky="ew")
        self.output_button = ttk.Button(output_row, text="変更…", command=self.choose_output)
        self.output_button.grid(row=0, column=1, padx=(8, 0))
        ttk.Label(self.advanced, textvariable=self.server_var, foreground="#45627d").grid(row=6, column=0, columnspan=3, sticky="w", pady=(8, 0))

        ttk.Separator(body).pack(fill="x", pady=(10, 14))
        action_row = ttk.Frame(body, style="Card.TFrame")
        action_row.pack(fill="x")
        self.start_button = ttk.Button(action_row, text="2. 日本語化MODを作成", style="Accent.TButton", command=self.start)
        self.start_button.pack(side="left")
        self.cancel_button = ttk.Button(action_row, text="キャンセル", command=self.cancel, state="disabled")
        self.cancel_button.pack(side="left", padx=(10, 0))
        self.open_button = ttk.Button(action_row, text="作成先を開く", command=self.open_output, state="disabled")
        self.open_button.pack(side="right")

        self.progress = ttk.Progressbar(body, mode="determinate", maximum=100)
        self.progress.pack(fill="x", pady=(16, 5))
        ttk.Label(body, textvariable=self.status_var, foreground="#34495e").pack(anchor="w")

        self.log = scrolledtext.ScrolledText(
            body,
            height=8,
            font=("Consolas", 9),
            bg="#f7f9fb",
            fg="#34495e",
            relief="solid",
            borderwidth=1,
            state="disabled",
        )
        self.log.pack(fill="both", expand=True, pady=(12, 0))
        self.append_log("準備完了。翻訳方式とMODを選択してください。既定はPC内だけで処理するローカルLLMです。")

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
            self.settings_path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "provider": self.active_provider,
                "local_endpoint": self.local_endpoint,
                "models": self.models_by_provider,
                "workers": workers,
                "last_source": self.source_var.get().strip(),
                "last_output": self.output_var.get().strip(),
            }
            temporary = self.settings_path.with_suffix(f".{os.getpid()}.tmp")
            temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.replace(temporary, self.settings_path)
        except OSError:
            pass

    def provider_id(self) -> str:
        return PROVIDER_LABELS.get(self.provider_var.get(), "local")

    def apply_provider(self, initial: bool = False) -> None:
        previous = self.active_provider
        if not initial:
            self.models_by_provider[previous] = self.model_var.get().strip()
            if previous == "local":
                self.local_endpoint = self.endpoint_var.get().strip() or DEFAULT_ENDPOINT
        provider_id = self.provider_id()
        self.active_provider = provider_id
        provider = get_provider(provider_id)
        self.models_verified = False
        self.model_var.set(str(self.models_by_provider.get(provider_id, "")))
        self.model_combo.configure(values=[])
        if provider.remote:
            self.endpoint_var.set(provider.chat_endpoint)
            self.endpoint_entry.configure(state="readonly")
            self.model_combo.configure(state="normal")
            self.provider_hint.configure(text="英文を選択サービスへ送信（料金・規約を確認）", foreground="#a05a22")
            for widget in (self.api_key_label, self.api_key_entry, self.delete_key_button, self.remember_key_check):
                widget.grid()
            try:
                saved = load_api_key(provider_id) if self.persistence_enabled else None
            except OSError as exc:
                saved = None
                self.append_log(f"資格情報の読込エラー: {exc}")
            self.api_key_var.set(saved or "")
            self.server_var.set("APIキーとモデルを確認し、「モデル一覧を取得」を押してください。")
            if not self.advanced_visible():
                self.toggle_advanced()
            if saved and not initial:
                self.refresh_models()
        else:
            self.endpoint_var.set(self.local_endpoint or DEFAULT_ENDPOINT)
            self.endpoint_entry.configure(state="normal")
            self.model_combo.configure(state="readonly")
            self.provider_hint.configure(text="英文をPC外へ送信しません", foreground="#3f7750")
            for widget in (self.api_key_label, self.api_key_entry, self.delete_key_button, self.remember_key_check):
                widget.grid_remove()
            self.api_key_var.set("")
            self.server_var.set("ローカルLLMを確認してください。")
            if not initial:
                self.refresh_models()

    def delete_saved_key(self) -> None:
        provider_id = self.provider_id()
        if provider_id == "local":
            return
        try:
            deleted = delete_api_key(provider_id)
            self.api_key_var.set("")
            self.server_var.set("保存済みAPIキーを削除しました。" if deleted else "保存済みAPIキーはありません。")
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"APIキーを削除できませんでした。\n\n{exc}")

    def choose_source(self) -> None:
        selected = filedialog.askdirectory(title="日本語化するCK3 MODフォルダを選択")
        if selected:
            self.source_var.set(selected)
            self.source_changed()

    def source_changed(self) -> None:
        raw = self.source_var.get().strip().strip('"')
        if not raw:
            return
        source = Path(raw).expanduser()
        self.source_var.set(str(source))
        suggested = default_output(source)
        source_changed = str(source) != self.last_source_for_output
        current_output = self.output_var.get().strip()
        if source_changed or not current_output:
            self.output_var.set(str(suggested))
        self.last_source_for_output = str(source)
        self.last_auto_output = str(suggested)
        self.output_hint_var.set(f"作成先: {self.output_var.get()}")

    def choose_output(self) -> None:
        initial = self.output_var.get().strip() or self.source_var.get().strip()
        selected = filedialog.askdirectory(title="出力先の親フォルダを選択", initialdir=str(Path(initial).parent) if initial else None)
        if selected:
            source_name = Path(self.source_var.get().strip()).name or "CK3_Mod"
            self.output_var.set(str(Path(selected) / f"{source_name}_Japanese"))
            self.output_hint_var.set(f"作成先: {self.output_var.get()}")

    def toggle_advanced(self) -> None:
        if self.advanced_visible():
            self.advanced_window.withdraw()
            self.advanced_is_open = False
            self.advanced_button.configure(text="▶ 詳細設定（通常は変更不要）")
        else:
            self.advanced_window.deiconify()
            self.advanced_window.lift()
            self.advanced_window.focus_set()
            self.advanced_is_open = True
            self.advanced_button.configure(text="▼ 詳細設定")

    def advanced_visible(self) -> bool:
        return self.advanced_is_open

    def refresh_models(self) -> None:
        if self.running:
            return
        provider_id = self.provider_id()
        provider = get_provider(provider_id)
        api_key = self.api_key_var.get().strip() or None
        if provider.requires_key and not api_key:
            self.server_var.set("APIキーを入力してください。")
            self.api_key_entry.focus_set()
            return
        self.models_verified = False
        self.server_var.set(f"{provider.label}へ接続してモデル一覧を取得中…")
        self.refresh_button.configure(state="disabled")
        endpoint = self.endpoint_var.get().strip()

        def work() -> None:
            try:
                models = discover_models(endpoint, provider=provider_id, api_key=api_key)
                self.events.put({"event": "models", "models": models, "provider": provider_id})
            except Exception as exc:  # noqa: BLE001 - report every background-worker failure in the GUI
                self.events.put({"event": "models_error", "message": str(exc), "provider": provider_id})

        threading.Thread(target=work, daemon=True).start()

    def start(self) -> None:
        source_text = self.source_var.get().strip().strip('"')
        if not source_text:
            messagebox.showwarning(APP_NAME, "先に日本語化するMODフォルダを選択してください。")
            return
        source = Path(source_text)
        output_text = self.output_var.get().strip()
        if not output_text:
            output_text = str(default_output(source))
            self.output_var.set(output_text)
        output = Path(output_text)
        provider_id = self.provider_id()
        provider = get_provider(provider_id)
        api_key = self.api_key_var.get().strip() or None
        selected_model = self.model_var.get().strip() or None
        if provider.requires_key and not api_key:
            messagebox.showwarning(APP_NAME, f"{provider.label}のAPIキーを入力してください。")
            if not self.advanced_visible():
                self.toggle_advanced()
            self.api_key_entry.focus_set()
            return
        if provider.remote and not selected_model:
            messagebox.showwarning(APP_NAME, "使用する翻訳モデルを入力または選択してください。")
            if not self.advanced_visible():
                self.toggle_advanced()
            self.model_combo.focus_set()
            return
        if provider.remote and not messagebox.askyesno(
            APP_NAME,
            f"{provider.label}を使用します。\n\nMODの英文が公式APIへ送信され、利用料金が発生する場合があります。APIキーや翻訳本文は本アプリのログには記録しません。\n\n続行しますか？",
            icon="warning",
        ):
            return
        launcher = output.parent / f"{output.name}.mod"
        overwrite = output.exists() or launcher.exists()
        if overwrite and not messagebox.askyesno(
            APP_NAME,
            "同名の日本語化MODが既にあります。\n\n既存版をバックアップして、新しく作り直しますか？",
            icon="warning",
        ):
            return
        try:
            workers = max(1, min(8, int(self.workers_var.get())))
        except (ValueError, tk.TclError):
            workers = 4
        if provider.remote and api_key:
            try:
                if self.remember_key_var.get():
                    save_api_key(provider_id, api_key)
                else:
                    delete_api_key(provider_id)
            except OSError as exc:
                messagebox.showerror(APP_NAME, f"APIキーをWindows資格情報マネージャーへ保存できませんでした。\n\n{exc}")
                return
        self.save_settings()
        self.set_running(True)
        self.cancel_event.clear()
        self.progress.configure(mode="indeterminate")
        self.progress.start(12)
        self.status_var.set(f"入力と{provider.label}を確認しています…")
        self.append_log(f"開始: {source} / 翻訳方式: {provider.label} / モデル: {selected_model or '自動検出'}")
        options = CloneOptions(
            source=source,
            output=output,
            endpoint=self.endpoint_var.get().strip(),
            provider=provider_id,
            api_key=api_key,
            model=selected_model if provider.remote or self.models_verified else None,
            workers=workers,
            overwrite=overwrite,
        )

        def work() -> None:
            try:
                result = create_japanese_clone(options, self.events.put, self.cancel_event)
                self.events.put({"event": "worker_done", "result": result})
            except CancelledError as exc:
                self.events.put({"event": "worker_cancelled", "message": str(exc)})
            except Exception as exc:  # noqa: BLE001 - keep the GUI alive and surface the worker failure
                self.events.put({"event": "worker_error", "message": str(exc)})

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def cancel(self) -> None:
        if self.running and messagebox.askyesno(APP_NAME, "処理をキャンセルしますか？\n完了済みの翻訳キャッシュは残ります。"):
            self.cancel_event.set()
            self.cancel_button.configure(state="disabled")
            self.status_var.set("キャンセルしています（現在の翻訳要求の終了を待っています）…")

    def set_running(self, running: bool) -> None:
        self.running = running
        normal = "disabled" if running else "normal"
        for widget in (
            self.source_entry, self.browse_button, self.advanced_button, self.start_button, self.refresh_button,
            self.workers_spin, self.output_entry, self.output_button, self.api_key_entry,
            self.delete_key_button, self.remember_key_check,
        ):
            widget.configure(state=normal)
        if running:
            self.provider_combo.configure(state="disabled")
            self.endpoint_entry.configure(state="disabled")
            self.model_combo.configure(state="disabled")
        else:
            self.provider_combo.configure(state="readonly")
            remote = get_provider(self.provider_id()).remote
            self.endpoint_entry.configure(state="readonly" if remote else "normal")
            self.model_combo.configure(state="normal" if remote else "readonly")
        self.cancel_button.configure(state="normal" if running else "disabled")

    def append_log(self, message: str) -> None:
        api_key = self.api_key_var.get().strip()
        if api_key:
            message = message.replace(api_key, "[APIキー非表示]")
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

    def handle_event(self, event: dict[str, object]) -> None:
        kind = str(event.get("event", ""))
        if kind == "models":
            if event.get("provider") != self.provider_id():
                return
            models = [str(value) for value in event.get("models", [])]
            self.model_combo.configure(values=models)
            if models:
                self.models_verified = True
                if self.provider_id() == "local" and self.model_var.get() not in models:
                    self.model_var.set(models[0])
                selected = self.model_var.get().strip()
                suffix = f"（{selected}）" if selected else "。使用モデルを選択してください。"
                self.server_var.set(f"接続済み: {len(models)}モデル取得{suffix}")
            else:
                self.models_verified = False
                if self.provider_id() == "local":
                    self.model_var.set("")
                self.server_var.set("モデル一覧を取得できませんでした。モデルIDを直接入力できます。")
            self.refresh_button.configure(state="normal" if not self.running else "disabled")
        elif kind == "models_error":
            if event.get("provider") != self.provider_id():
                return
            self.models_verified = False
            provider = get_provider(self.provider_id())
            self.server_var.set(f"未接続: {provider.label}の設定を確認してください")
            self.append_log(f"接続確認: {event.get('message', '')}")
            self.refresh_button.configure(state="normal" if not self.running else "disabled")
        elif kind == "model_selected":
            self.model_var.set(str(event.get("model", "")))
            self.status_var.set(f"{get_provider(self.provider_id()).label}で翻訳を開始します…")
            self.append_log(f"使用モデル: {self.model_var.get()}")
        elif kind == "translation_started":
            total = int(event.get("entries", 0))
            pending = int(event.get("pending", 0))
            hits = int(event.get("cache_hits", 0))
            self.progress.stop()
            self.progress.configure(mode="determinate", value=(hits / total * 100 if total else 0))
            self.status_var.set(f"翻訳中: 全{total}項目 / 未処理{pending}項目")
            self.append_log(f"翻訳項目: {total}（キャッシュ再利用: {hits}）")
        elif kind == "translation_progress":
            completed = int(event.get("completed_batches", 0))
            total_batches = int(event.get("total_batches", 0))
            translated = int(event.get("translated", 0))
            value = completed / total_batches * 85 if total_batches else 85
            self.progress.configure(value=value)
            self.status_var.set(f"翻訳中: {completed}/{total_batches}バッチ完了（{translated}項目）")
        elif kind == "translation_validated":
            self.progress.configure(value=88)
            self.status_var.set("翻訳ファイルの構文検証に合格しました")
        elif kind == "copying":
            self.progress.configure(value=90)
            self.status_var.set(str(event.get("message", "元MODを複製しています…")))
        elif kind == "verifying":
            self.progress.configure(value=96)
            self.status_var.set(str(event.get("message", "最終検証中…")))
        elif kind == "log":
            self.append_log(str(event.get("message", "")))
        elif kind == "done":
            self.progress.configure(value=100)
        elif kind == "worker_done":
            result = event["result"]
            self.last_output = result.output
            self.finish_state("作成完了。日本語化MODを使用できます。")
            self.open_button.configure(state="normal")
            self.append_log(f"完成: {result.output}")
            messagebox.showinfo(
                APP_NAME,
                f"日本語化MODを作成しました。\n\n{result.output}\n\n翻訳項目: {result.entries}\nファイル: {result.localization_files}",
            )
        elif kind == "worker_cancelled":
            self.finish_state("キャンセルしました。次回は翻訳済み項目を再利用します。")
            self.append_log(str(event.get("message", "キャンセルしました")))
        elif kind == "worker_error":
            message = str(event.get("message", "不明なエラー"))
            self.finish_state("エラーが発生しました。元MODは変更されていません。")
            self.append_log(f"エラー: {message}")
            messagebox.showerror(APP_NAME, f"日本語化MODを作成できませんでした。\n\n{message}\n\n元MODは変更されていません。")

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
        if not self.last_output:
            return
        folder = self.last_output if self.last_output.is_dir() else self.last_output.parent
        try:
            os.startfile(folder)  # type: ignore[attr-defined]
        except OSError as exc:
            messagebox.showerror(APP_NAME, str(exc))

    def on_close(self) -> None:
        if self.running:
            if messagebox.askyesno(APP_NAME, "処理中です。キャンセルして終了しますか？"):
                self.cancel_event.set()
                self.status_var.set("キャンセルしています…")
            return
        self.save_settings()
        self.root.destroy()


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
        try:
            result = create_japanese_clone(
                CloneOptions(
                    source=Path(args.headless_source),
                    output=Path(args.headless_output),
                    endpoint=args.endpoint or get_provider(args.provider).chat_endpoint,
                    provider=args.provider,
                    api_key=os.environ.get(args.api_key_env) if args.api_key_env else None,
                    model=args.model,
                    workers=args.workers,
                    work_root=Path(args.work_root) if args.work_root else None,
                    overwrite=args.overwrite,
                )
            )
            payload = {"ok": True, "result": dataclasses.asdict(result)}
            result_path.parent.mkdir(parents=True, exist_ok=True)
            result_path.write_text(json.dumps(payload, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
            return
        except Exception as exc:
            result_path.parent.mkdir(parents=True, exist_ok=True)
            result_path.write_text(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False) + "\n", encoding="utf-8")
            raise SystemExit(1) from exc
    enable_windows_dpi_awareness()
    root = tk.Tk()
    if args.smoke_test:
        root.withdraw()
        app = JapaneseModMaker(root, auto_connect=False)
        root.update_idletasks()
        advanced_hidden = not app.advanced_visible()
        app.provider_var.set(PROVIDERS["openai"].label)
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
    JapaneseModMaker(root)
    root.mainloop()


if __name__ == "__main__":
    main()
