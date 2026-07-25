import customtkinter as ctk
import tkinter as tk
from tkinter import messagebox
import os
import sys
import json
import re
import shutil
import subprocess
import threading
import queue
import traceback
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).parent.resolve()
INPUT_DIR = BASE_DIR / "input"
OUTPUT_DIR = BASE_DIR / "output"
TRANSLATED_DIR = OUTPUT_DIR / "translated"
CONFIG_FILE = BASE_DIR / "config.json"

DEFAULT_CONFIG = {
    "ollama_url": "http://localhost:11434/api/generate",
    "model": "translategemma:12b",
    "num_predict": 512,
    "timeout": 120,
    "source_lang": "English",
    "target_lang": "Bulgarian",
}

VIDEO_EXTENSIONS = {".mkv", ".mp4", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v"}

FFMPEG_PATHS = [
    r"D:\Program Files\ffmpeg\bin\ffmpeg.exe",
    r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
    r"C:\ffmpeg\bin\ffmpeg.exe",
]
FFPROBE_PATHS = [p.replace("ffmpeg.exe", "ffprobe.exe") for p in FFMPEG_PATHS]


def _find_executable(names: list[str], fallback_paths: list[str]) -> str:
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    for path in fallback_paths:
        if os.path.isfile(path):
            return path
    return names[0]  # fallback to bare name, will fail with clear error later


class Config:
    def __init__(self):
        self.data = dict(DEFAULT_CONFIG)
        self.load()

    def load(self):
        try:
            if CONFIG_FILE.exists():
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    self.data.update(loaded)
        except Exception as e:
            print(f"Config load error: {e}")

    def save(self):
        try:
            CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2, ensure_ascii=False)
            return True
        except Exception as e:
            return str(e)

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value


class FileListFrame(ctk.CTkFrame):
    def __init__(self, master, title: str, **kwargs):
        super().__init__(master, **kwargs)
        self.title = title
        self.listbox: Optional[tk.Listbox] = None
        self._build()

    def _build(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        lbl = ctk.CTkLabel(self, text=self.title, font=ctk.CTkFont(size=14, weight="bold"))
        lbl.grid(row=0, column=0, padx=6, pady=(6, 2), sticky="w")

        container = ctk.CTkFrame(self, fg_color="transparent")
        container.grid(row=1, column=0, padx=6, pady=(0, 6), sticky="nsew")
        container.grid_columnconfigure(0, weight=1)
        container.grid_rowconfigure(0, weight=1)

        self.count_label = ctk.CTkLabel(container, text="0 files", font=ctk.CTkFont(size=11))
        self.count_label.grid(row=1, column=0, pady=(0, 2), sticky="w")

        self.listbox = tk.Listbox(
            container,
            selectmode=tk.EXTENDED,
            activestyle="none",
            borderwidth=0,
            highlightthickness=1,
            font=("Segoe UI", 11),
        )

        self.listbox.configure(
            bg="#1a1a1a",
            fg="#c0c0c0",
            selectbackground="#444444",
            selectforeground="#ffffff",
            highlightbackground="#2a2a2a",
        )

        self.listbox.grid(row=0, column=0, sticky="nsew")

        scrollbar = ctk.CTkScrollbar(container, orientation="vertical", command=self.listbox.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.listbox.configure(yscrollcommand=scrollbar.set)

    def populate(self, items: list[str]):
        self.listbox.delete(0, tk.END)
        for item in sorted(items):
            self.listbox.insert(tk.END, item)
        self.count_label.configure(text=f"{len(items)} file{'s' if len(items) != 1 else ''}")

    def get_selected(self) -> list[str]:
        return [self.listbox.get(i) for i in self.listbox.curselection()]

    def clear_selection(self):
        self.listbox.selection_clear(0, tk.END)


class SubtitleApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.config = Config()
        self.queue: queue.Queue = queue.Queue()
        self._worker: Optional[threading.Thread] = None
        self._stop_flag = threading.Event()
        self.ffmpeg_path = _find_executable(["ffmpeg"], FFMPEG_PATHS)
        self.ffprobe_path = _find_executable(["ffprobe"], FFPROBE_PATHS)

        self.title("Movie Subtitle Ripper & Translator")
        self.geometry("1200x800")
        self.minsize(1000, 700)
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self._build_ui()
        self._apply_grayscale_theme()
        self.after(200, self._check_queue)
        self.after(500, self._initial_scan)
        self.after(3000, self._auto_refresh)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self):
        self.tabview = ctk.CTkTabview(self, command=self._on_tab_change)
        self.tabview.grid(row=0, column=0, padx=10, pady=10, sticky="nsew")

        self.tab_extract = self.tabview.add("Extract Subtitles")
        self.tab_translate = self.tabview.add("Translate Subtitles")
        self.tab_settings = self.tabview.add("Settings")

        self._build_extract_tab()
        self._build_translate_tab()
        self._build_settings_tab()

    def _apply_grayscale_theme(self):
        grey_btn = "#444444"
        grey_btn_hover = "#555555"
        grey_frame = "#1a1a1a"
        grey_frame2 = "#222222"
        grey_text = "#c0c0c0"
        grey_progress = "#777777"
        grey_scrollbar = "#555555"

        self.configure(fg_color=grey_frame)
        self.tabview.configure(fg_color=grey_frame)

        for btn in [self.extract_scan_btn, self.extract_selected_btn, self.extract_all_btn,
                     self.extract_open_in_btn, self.extract_open_out_btn,
                     self.translate_scan_btn, self.translate_selected_btn, self.translate_all_btn,
                     self.translate_retry_btn, self.translate_stop_btn, self.translate_open_btn,
                     self.settings_fetch_btn]:
            btn.configure(fg_color=grey_btn, hover_color=grey_btn_hover, text_color=grey_text)

        for pb in [self.extract_progress, self.translate_progress]:
            pb.configure(progress_color=grey_progress, fg_color=grey_frame2)

        self.settings_model_combo.configure(
            fg_color=grey_frame2, button_color=grey_btn, button_hover_color=grey_btn_hover,
            dropdown_fg_color=grey_frame2, dropdown_hover_color=grey_btn_hover,
            dropdown_text_color=grey_text, text_color=grey_text,
        )

    # ─── Extract Tab ────────────────────────────────────────────────

    def _build_extract_tab(self):
        t = self.tab_extract
        t.grid_columnconfigure(0, weight=1)
        t.grid_columnconfigure(1, weight=1)
        t.grid_rowconfigure(2, weight=1)

        header = ctk.CTkLabel(t, text="Extract Embedded Subtitles from Video Files",
                               font=ctk.CTkFont(size=18, weight="bold"))
        header.grid(row=0, column=0, columnspan=2, pady=(10, 4))

        sub = ctk.CTkLabel(t, text="Select video files from the input folder and extract their subtitle streams.",
                           font=ctk.CTkFont(size=12))
        sub.grid(row=1, column=0, columnspan=2, pady=(0, 8))

        self.extract_video_list = FileListFrame(t, "Video Files (input/)")
        self.extract_video_list.grid(row=2, column=0, padx=(10, 5), pady=5, sticky="nsew")

        self.extract_srt_list = FileListFrame(t, "Extracted SRT Files (output/)")
        self.extract_srt_list.grid(row=2, column=1, padx=(5, 10), pady=5, sticky="nsew")

        btn_frame = ctk.CTkFrame(t, fg_color="transparent")
        btn_frame.grid(row=3, column=0, columnspan=2, pady=(6, 10))
        for i in range(6):
            btn_frame.grid_columnconfigure(i, weight=1)

        self.extract_scan_btn = ctk.CTkButton(btn_frame, text="Scan Input", command=self._scan_input,
                                              width=120)
        self.extract_scan_btn.grid(row=0, column=0, padx=4)

        self.extract_selected_btn = ctk.CTkButton(btn_frame, text="Extract Selected \u2192",
                                                  command=self._extract_selected, width=160)
        self.extract_selected_btn.grid(row=0, column=1, padx=4)

        self.extract_all_btn = ctk.CTkButton(btn_frame, text="Extract All \u2192\u2192",
                                             command=self._extract_all, width=140)
        self.extract_all_btn.grid(row=0, column=2, padx=4)

        self.extract_open_in_btn = ctk.CTkButton(btn_frame, text="Open Input Folder",
                                                  command=lambda: os.startfile(INPUT_DIR), width=120)
        self.extract_open_in_btn.grid(row=0, column=3, padx=4)

        self.extract_open_out_btn = ctk.CTkButton(btn_frame, text="Open Output Folder",
                                                   command=lambda: os.startfile(OUTPUT_DIR), width=130)
        self.extract_open_out_btn.grid(row=0, column=4, padx=4)

        self.extract_status = ctk.CTkLabel(t, text="", font=ctk.CTkFont(size=12))
        self.extract_status.grid(row=4, column=0, columnspan=2, pady=(0, 2), sticky="w", padx=15)

        self.extract_progress = ctk.CTkProgressBar(t, height=8)
        self.extract_progress.grid(row=5, column=0, columnspan=2, padx=15, pady=(0, 8), sticky="ew")
        self.extract_progress.set(0)

    # ─── Translate Tab ──────────────────────────────────────────────

    def _build_translate_tab(self):
        t = self.tab_translate
        t.grid_columnconfigure(0, weight=1)
        t.grid_columnconfigure(1, weight=1)
        t.grid_rowconfigure(3, weight=1)

        header = ctk.CTkLabel(t, text="Translate SRT Subtitles (English \u2192 Bulgarian)",
                              font=ctk.CTkFont(size=18, weight="bold"))
        header.grid(row=0, column=0, columnspan=2, pady=(10, 4))

        sub = ctk.CTkLabel(t, text="Select extracted SRT files and translate them using the local Ollama model.",
                           font=ctk.CTkFont(size=12))
        sub.grid(row=1, column=0, columnspan=2, pady=(0, 8))

        self.translate_src_list = FileListFrame(t, "Available SRT Files (output/)")
        self.translate_src_list.grid(row=2, column=0, padx=(10, 5), pady=5, sticky="nsew")

        self.translate_dst_list = FileListFrame(t, "Translated SRT Files (output/translated/)")
        self.translate_dst_list.grid(row=2, column=1, padx=(5, 10), pady=5, sticky="nsew")

        btn_frame = ctk.CTkFrame(t, fg_color="transparent")
        btn_frame.grid(row=3, column=0, columnspan=2, pady=(4, 0), sticky="ew")
        for i in range(6):
            btn_frame.grid_columnconfigure(i, weight=1)

        self.translate_scan_btn = ctk.CTkButton(btn_frame, text="Scan SRTs", command=self._scan_output_srts,
                                                width=100)
        self.translate_scan_btn.grid(row=0, column=0, padx=4)

        self.translate_selected_btn = ctk.CTkButton(btn_frame, text="Translate Selected \u2192",
                                                    command=self._translate_selected, width=160)
        self.translate_selected_btn.grid(row=0, column=1, padx=4)

        self.translate_all_btn = ctk.CTkButton(btn_frame, text="Translate All \u2192\u2192",
                                               command=self._translate_all, width=140)
        self.translate_all_btn.grid(row=0, column=2, padx=4)

        self.translate_retry_btn = ctk.CTkButton(btn_frame, text="Retry Failed Blocks",
                                                 command=self._retry_failed, width=140)
        self.translate_retry_btn.grid(row=0, column=3, padx=4)

        self.translate_stop_btn = ctk.CTkButton(btn_frame, text="Stop", fg_color="#555555",
                                                 hover_color="#666666", command=self._stop_worker, width=80)
        self.translate_stop_btn.grid(row=0, column=4, padx=4)

        self.translate_open_btn = ctk.CTkButton(btn_frame, text="Open Translated Folder",
                                                 command=lambda: os.startfile(TRANSLATED_DIR), width=150)
        self.translate_open_btn.grid(row=0, column=5, padx=4)

        progress_frame = ctk.CTkFrame(t, fg_color="transparent")
        progress_frame.grid(row=4, column=0, columnspan=2, padx=15, pady=(6, 2), sticky="ew")
        progress_frame.grid_columnconfigure(1, weight=1)

        self.translate_status = ctk.CTkLabel(progress_frame, text="", font=ctk.CTkFont(size=12))
        self.translate_status.grid(row=0, column=0, padx=(0, 10), sticky="w")
        self.translate_progress = ctk.CTkProgressBar(progress_frame, height=8)
        self.translate_progress.grid(row=0, column=1, padx=(0, 4), sticky="ew")
        self.translate_progress.set(0)
        self.translate_pct = ctk.CTkLabel(progress_frame, text="0%", font=ctk.CTkFont(size=11), width=40)
        self.translate_pct.grid(row=0, column=2, padx=(4, 0), sticky="e")

        log_frame = ctk.CTkFrame(t)
        log_frame.grid(row=5, column=0, columnspan=2, padx=10, pady=(4, 10), sticky="nsew")
        log_frame.grid_columnconfigure(0, weight=1)
        log_frame.grid_rowconfigure(1, weight=1)

        log_lbl = ctk.CTkLabel(log_frame, text="Translation Log", font=ctk.CTkFont(size=13, weight="bold"))
        log_lbl.grid(row=0, column=0, padx=6, pady=(4, 0), sticky="w")

        self.translate_log = ctk.CTkTextbox(log_frame, font=("Consolas", 10), wrap="none", state="disabled")
        self.translate_log.grid(row=1, column=0, padx=6, pady=6, sticky="nsew")

        log_scroll = ctk.CTkScrollbar(log_frame, orientation="vertical", command=self.translate_log.yview)
        log_scroll.grid(row=1, column=1, sticky="ns", pady=6)
        self.translate_log.configure(yscrollcommand=log_scroll.set)

        self._translate_available = []
        self._translate_failed_blocks: dict[str, list[dict]] = {}

    # ─── Settings Tab ───────────────────────────────────────────────

    def _build_settings_tab(self):
        t = self.tab_settings
        t.grid_columnconfigure(0, weight=1)
        t.grid_rowconfigure(0, weight=1)

        outer = ctk.CTkScrollableFrame(t)
        outer.grid(row=0, column=0, padx=20, pady=20, sticky="nsew")
        outer.grid_columnconfigure(0, weight=1)

        header = ctk.CTkLabel(outer, text="Settings", font=ctk.CTkFont(size=20, weight="bold"))
        header.grid(row=0, column=0, pady=(0, 15))

        frame = ctk.CTkFrame(outer)
        frame.grid(row=1, column=0, sticky="ew", padx=10)
        frame.grid_columnconfigure(1, weight=1)

        self.settings_entries = {}
        row = 0

        # Ollama URL
        lbl = ctk.CTkLabel(frame, text="Ollama URL:", font=ctk.CTkFont(size=13), anchor="w")
        lbl.grid(row=row, column=0, padx=(10, 8), pady=6, sticky="w")
        self._settings_url_var = tk.StringVar(value=str(self.config.get("ollama_url", "http://localhost:11434/api/generate")))
        entry = ctk.CTkEntry(frame, textvariable=self._settings_url_var, font=ctk.CTkFont(size=13))
        entry.grid(row=row, column=1, padx=(0, 10), pady=6, sticky="ew")
        self.settings_entries["ollama_url"] = self._settings_url_var
        row += 1

        # Model Name (ComboBox + Fetch button)
        lbl = ctk.CTkLabel(frame, text="Model Name:", font=ctk.CTkFont(size=13), anchor="w")
        lbl.grid(row=row, column=0, padx=(10, 8), pady=6, sticky="w")

        model_frame = ctk.CTkFrame(frame, fg_color="transparent")
        model_frame.grid(row=row, column=1, padx=(0, 10), pady=6, sticky="ew")
        model_frame.grid_columnconfigure(0, weight=1)

        self._settings_model_var = tk.StringVar(value=str(self.config.get("model", "translategemma:12b")))
        self.settings_model_combo = ctk.CTkOptionMenu(
            model_frame, variable=self._settings_model_var, values=[], font=ctk.CTkFont(size=13)
        )
        self.settings_model_combo.grid(row=0, column=0, sticky="ew")

        self.settings_fetch_btn = ctk.CTkButton(
            model_frame, text="Fetch Models", width=110,
            command=self._fetch_models
        )
        self.settings_fetch_btn.grid(row=0, column=1, padx=(6, 0))

        self.settings_entries["model"] = self._settings_model_var
        row += 1

        # Remaining settings (entries)
        remaining = [
            ("Max Tokens (num_predict):", "num_predict", "512"),
            ("Request Timeout (seconds):", "timeout", "120"),
            ("Source Language:", "source_lang", "English"),
            ("Target Language:", "target_lang", "Bulgarian"),
        ]
        for label, key, placeholder in remaining:
            lbl = ctk.CTkLabel(frame, text=label, font=ctk.CTkFont(size=13), anchor="w")
            lbl.grid(row=row, column=0, padx=(10, 8), pady=6, sticky="w")
            var = tk.StringVar(value=str(self.config.get(key, placeholder)))
            entry = ctk.CTkEntry(frame, textvariable=var, font=ctk.CTkFont(size=13))
            entry.grid(row=row, column=1, padx=(0, 10), pady=6, sticky="ew")
            self.settings_entries[key] = var
            row += 1

        btn_save = ctk.CTkButton(frame, text="Save Settings", command=self._save_settings, width=140)
        btn_save.grid(row=row, column=0, padx=(10, 5), pady=16)

        btn_reset = ctk.CTkButton(frame, text="Reset to Defaults", command=self._reset_settings,
                                  fg_color="#555555", hover_color="#666666", width=140)
        btn_reset.grid(row=row, column=1, padx=(5, 10), pady=16, sticky="w")
        row += 1

        status_lbl = ctk.CTkLabel(frame, text="", font=ctk.CTkFont(size=11))
        status_lbl.grid(row=row, column=0, columnspan=2, pady=(0, 8))
        self.settings_status = status_lbl
        row += 1

        # Ops log
        ops_lbl = ctk.CTkLabel(frame, text="Operation Log", font=ctk.CTkFont(size=12, weight="bold"))
        ops_lbl.grid(row=row, column=0, columnspan=2, padx=10, pady=(8, 2), sticky="w")
        row += 1

        self.settings_log = ctk.CTkTextbox(frame, font=("Consolas", 10), height=90, wrap="none", state="disabled")
        self.settings_log.grid(row=row, column=0, columnspan=2, padx=10, pady=(0, 6), sticky="ew")
        row += 1

        sep = ctk.CTkFrame(outer, height=2, fg_color="#444444")
        sep.grid(row=2, column=0, pady=15, sticky="ew", padx=10)

        about_header = ctk.CTkLabel(outer, text="About", font=ctk.CTkFont(size=16, weight="bold"))
        about_header.grid(row=3, column=0, pady=(0, 6))

        about_text = (
            "Movie Subtitle Ripper & Translator v1.0\n\n"
            "Extracts embedded subtitles from video files using ffmpeg/ffprobe\n"
            "and translates them from English to Bulgarian using a local Ollama model.\n\n"
            "Built with Python 3.13, CustomTkinter, ffmpeg, and Ollama."
        )
        about_lbl = ctk.CTkLabel(outer, text=about_text, font=ctk.CTkFont(size=12), justify="left")
        about_lbl.grid(row=4, column=0, pady=(0, 20), padx=10)

    # ─── Settings helpers ──────────────────────────────────────────

    def _append_settings_log(self, msg: str):
        self.after(0, lambda: self._settings_log_impl(msg))

    def _settings_log_impl(self, msg: str):
        self.settings_log.configure(state="normal")
        self.settings_log.insert("end", msg + "\n")
        self.settings_log.see("end")
        self.settings_log.configure(state="disabled")

    def _fetch_models(self):
        url = self._settings_url_var.get().strip().rstrip("/")
        tags_url = url.replace("/api/generate", "") + "/api/tags"
        self._append_settings_log(f"Fetching models from {tags_url}")
        self.settings_fetch_btn.configure(state="disabled", text="Fetching...")
        self.settings_status.configure(text="")
        threading.Thread(target=self._fetch_models_worker, args=(tags_url,), daemon=True).start()

    def _fetch_models_worker(self, tags_url: str):
        try:
            import urllib.request as ur
            import urllib.error as ue
            import json as j
            req = ur.Request(tags_url, method="GET")
            resp = ur.urlopen(req, timeout=10)
            data = j.loads(resp.read().decode())
            models = data.get("models", [])
            names = [m["name"] for m in models if "name" in m]
            if not names:
                self._append_settings_log("No models found — download more models for Ollama")
                self.after(0, lambda: self.settings_status.configure(
                    text="No models found — download more models for Ollama", text_color="#ffcc00"
                ))
                self.after(0, lambda: self.settings_fetch_btn.configure(state="normal", text="Fetch Models"))
                return
            self.after(0, lambda: self._fetch_models_done(names))
            self._append_settings_log(f"Successfully fetched {len(names)} model(s)")
        except ue.URLError:
            self._append_settings_log("Error: cannot reach endpoint — ensure you have Ollama installed and running")
            self.after(0, lambda: self.settings_status.configure(
                text="Ensure you have Ollama installed and running", text_color="#ff6666"
            ))
            self.after(0, lambda: self.settings_fetch_btn.configure(state="normal", text="Fetch Models"))
        except Exception as e:
            self._append_settings_log(f"Error: {e}")
            self.after(0, lambda: self.settings_status.configure(
                text=f"Error: {e}", text_color="#ff6666"
            ))
            self.after(0, lambda: self.settings_fetch_btn.configure(state="normal", text="Fetch Models"))

    def _fetch_models_done(self, names: list[str]):
        self.settings_model_combo.configure(values=names)
        if names and self._settings_model_var.get() not in names:
            self._settings_model_var.set(names[0])
        self.settings_fetch_btn.configure(state="normal", text="Fetch Models")
        self.settings_status.configure(
            text=f"Loaded {len(names)} model(s) from server", text_color="#c0c0c0"
        )

    # ─── Scanning ───────────────────────────────────────────────────

    def _initial_scan(self):
        self._scan_input()
        self._scan_output_srts()
        self._scan_translated()

    def _auto_refresh(self):
        self._scan_input()
        self._scan_output_srts()
        self._scan_translated()
        self.after(3000, self._auto_refresh)

    def _on_tab_change(self):
        self._scan_input()
        self._scan_output_srts()
        self._scan_translated()

    def _scan_input(self):
        try:
            INPUT_DIR.mkdir(parents=True, exist_ok=True)
            files = [f.name for f in INPUT_DIR.iterdir() if f.suffix.lower() in VIDEO_EXTENSIONS and f.is_file()]
            self.extract_video_list.populate(files)
            self._log_extract(f"Found {len(files)} video file{'s' if len(files) != 1 else ''} in input/")
        except Exception as e:
            self.extract_video_list.populate([])
            self._log_extract(f"Error scanning input/: {e}")

    def _scan_output_srts(self):
        try:
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            files = [f.name for f in OUTPUT_DIR.iterdir() if f.suffix.lower() == ".srt" and f.is_file()]
            self.extract_srt_list.populate(files)
            self.translate_src_list.populate(files)
            self._translate_available = files
        except Exception as e:
            self.extract_srt_list.populate([])
            self.translate_src_list.populate([])
            self._log_extract(f"Error scanning output/: {e}")

    def _scan_translated(self):
        try:
            TRANSLATED_DIR.mkdir(parents=True, exist_ok=True)
            files = [f.name for f in TRANSLATED_DIR.iterdir() if f.suffix.lower() == ".srt" and f.is_file()]
            self.translate_dst_list.populate(files)
        except Exception as e:
            self.translate_dst_list.populate([])

    # ─── Extraction ─────────────────────────────────────────────────

    def _extract_selected(self):
        selected = self.extract_video_list.get_selected()
        if not selected:
            messagebox.showwarning("No Selection", "Please select at least one video file from the list.")
            return
        self._start_extraction(selected)

    def _extract_all(self):
        all_videos = [self.extract_video_list.listbox.get(i) for i in range(self.extract_video_list.listbox.size())]
        if not all_videos:
            messagebox.showwarning("No Videos", "No video files found in the input/ folder.")
            return
        self._start_extraction(all_videos)

    def _start_extraction(self, videos: list[str]):
        if self._worker and self._worker.is_alive():
            messagebox.showwarning("Busy", "An operation is already in progress.")
            return
        self._reapply_button_state(True)
        self.extract_progress.set(0)
        self.extract_status.configure(text="Starting extraction...")
        self._stop_flag.clear()
        self._worker = threading.Thread(target=self._extract_worker, args=(videos,), daemon=True)
        self._worker.start()

    def _extract_worker(self, videos: list[str]):
        try:
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            total = len(videos)
            logs = []
            ok_count = 0
            fail_count = 0
            for idx, video in enumerate(videos):
                if self._stop_flag.is_set():
                    break
                self.queue.put({"type": "extract_progress", "value": idx / total if total > 0 else 0})
                self.queue.put({"type": "extract_status", "message": f"[{idx + 1}/{total}] Extracting: {video[:60]}..."})
                ok, reason = self._extract_single(video, idx + 1, total)
                logs.append(f"[{idx + 1}/{total}] {video}")
                if ok:
                    logs.append(f"  OK")
                    ok_count += 1
                else:
                    logs.append(f"  FAILED: {reason}")
                    fail_count += 1
            self.queue.put({"type": "extract_progress", "value": 1.0})
            msg = f"Done: {ok_count} OK, {fail_count} failed ({total} total)"
            self.queue.put({"type": "extract_status", "message": msg})
            self.queue.put({"type": "extract_logs", "logs": logs})
        except Exception as e:
            tb = traceback.format_exc()
            self.queue.put({"type": "extract_status", "message": f"Worker error: {e}"})
            self.queue.put({"type": "extract_logs", "logs": [f"Worker crashed: {e}", tb]})
        finally:
            self.queue.put({"type": "extract_done"})
            self.queue.put({"type": "scan_output"})

    def _extract_single(self, video: str, num: int, total: int):
        video_path = INPUT_DIR / video
        try:
            if not video_path.is_file():
                return False, f"File not found: {video_path}"
            result = subprocess.run(
                [self.ffprobe_path, "-v", "error", "-select_streams", "s",
                 "-show_entries", "stream=index:stream_tags=language,title",
                 "-of", "csv=p=0", str(video_path)],
                capture_output=True, text=True, timeout=30,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            if result.returncode != 0:
                return False, f"ffprobe error (rc={result.returncode}): {result.stderr.strip()[:200]}"
            streams = [line.strip() for line in result.stdout.strip().split("\n") if line.strip()]
            if not streams:
                return False, "No subtitle streams found"
            for stream_line in streams:
                if self._stop_flag.is_set():
                    return False, "Stopped by user"
                parts = stream_line.split(",")
                stream_index = parts[0].strip()
                video_stem = video_path.stem
                out_name = f"{video_stem}.srt"
                out_path = OUTPUT_DIR / out_name
                res = subprocess.run(
                    [self.ffmpeg_path, "-loglevel", "error", "-i", str(video_path),
                     "-map", f"0:{stream_index}", "-c:s", "copy", str(out_path), "-y"],
                    capture_output=True, text=True, timeout=120,
                    creationflags=subprocess.CREATE_NO_WINDOW
                )
                if out_path.is_file() and out_path.stat().st_size > 0:
                    continue
                err = res.stderr.strip()[:300] if res.stderr.strip() else f"exit code {res.returncode}"
                return False, f"ffmpeg failed: {err}"
            return True, ""
        except FileNotFoundError:
            return False, f"ffprobe/ffmpeg not found at configured paths"
        except subprocess.TimeoutExpired:
            return False, "process timed out"
        except Exception as e:
            return False, f"Unexpected error: {e}"

    # ─── Translation ────────────────────────────────────────────────

    def _translate_selected(self):
        selected = self.translate_src_list.get_selected()
        if not selected:
            messagebox.showwarning("No Selection", "Please select at least one SRT file from the list.")
            return
        self._start_translation(selected)

    def _translate_all(self):
        all_srts = [self.translate_src_list.listbox.get(i) for i in range(self.translate_src_list.listbox.size())]
        if not all_srts:
            messagebox.showwarning("No SRTs", "No SRT files found in the output/ folder.")
            return
        self._start_translation(all_srts)

    def _start_translation(self, srt_files: list[str]):
        if self._worker and self._worker.is_alive():
            messagebox.showwarning("Busy", "A translation operation is already in progress.")
            return
        self._reapply_button_state(True)
        self.translate_progress.set(0)
        self.translate_pct.configure(text="0%")
        self.translate_status.configure(text="Starting translation...")
        self.translate_log.configure(state="normal")
        self.translate_log.delete("1.0", tk.END)
        self.translate_log.configure(state="disabled")
        self._translate_failed_blocks = {}
        self._stop_flag.clear()
        self._worker = threading.Thread(target=self._translate_worker, args=(srt_files,), daemon=True)
        self._worker.start()

    def _retry_failed(self):
        if not self._translate_failed_blocks:
            messagebox.showinfo("No Failures", "No failed blocks to retry.")
            return
        if self._worker and self._worker.is_alive():
            messagebox.showwarning("Busy", "A translation operation is already in progress.")
            return
        self._reapply_button_state(True)
        self.translate_progress.set(0)
        self.translate_pct.configure(text="0%")
        self.translate_status.configure(text="Retrying failed blocks...")
        self._stop_flag.clear()
        self._worker = threading.Thread(target=self._retry_worker, daemon=True)
        self._worker.start()

    def _retry_worker(self):
        try:
            total_fails = sum(len(v) for v in self._translate_failed_blocks.values())
            if total_fails == 0:
                return
            self.queue.put({"type": "translate_log", "message": f"Retrying {total_fails} failed block{'s' if total_fails != 1 else ''}..."})
            new_fails = {}
            processed = 0
            for srt_name, blocks in list(self._translate_failed_blocks.items()):
                if self._stop_flag.is_set():
                    break
                out_srt = TRANSLATED_DIR / srt_name
                new_blocks = []
                for b in blocks:
                    if self._stop_flag.is_set():
                        new_blocks.append(b)
                        continue
                    processed += 1
                    self.queue.put({
                        "type": "translate_progress",
                        "value": processed / total_fails,
                        "file": srt_name,
                        "detail": f"Retrying block {b['idx']}..."
                    })
                    translated = self._call_ollama(b["content"])
                    if translated.startswith("[TRANSLATION ERROR"):
                        new_blocks.append(b)
                        self.queue.put({"type": "translate_log", "message": f"  Retry failed for block {b['idx']}: {translated}"})
                    else:
                        block_str = f"{b['idx']}\n{b['timestamp']}\n{translated}"
                        self._append_to_file(out_srt, block_str + "\n\n")
                        self._write_log_entry(srt_name, f"Block {b['idx']} - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} (retry)")
                        self.queue.put({"type": "translate_log", "message": f"  Block {b['idx']} retry succeeded."})
                if new_blocks:
                    new_fails[srt_name] = new_blocks
            self._translate_failed_blocks = new_fails
            remaining = sum(len(v) for v in new_fails.values())
            if remaining == 0:
                self.queue.put({"type": "translate_log", "message": "All retries succeeded."})
            else:
                self.queue.put({"type": "translate_log", "message": f"{remaining} block{'s' if remaining != 1 else ''} still failed after retry."})
        except Exception as e:
            self.queue.put({"type": "translate_log", "message": f"Retry worker error: {e}"})
            import traceback
            self.queue.put({"type": "translate_log", "message": traceback.format_exc()})
        finally:
            self.queue.put({"type": "translate_progress", "value": 1.0, "file": "", "detail": ""})
            self.queue.put({"type": "translate_done"})

    def _translate_worker(self, srt_files: list[str]):
        try:
            total_blocks_est = 0
            for srt_name in srt_files:
                srt_path = OUTPUT_DIR / srt_name
                blocks = self._parse_srt(srt_path)
                if blocks is None:
                    self.queue.put({"type": "translate_log", "message": f"Skipping '{srt_name}': parse error."})
                else:
                    total_blocks_est += len(blocks)

            self._translate_failed_blocks = {}
            processed = 0

            for srt_name in srt_files:
                try:
                    if self._stop_flag.is_set():
                        break
                    srt_path = OUTPUT_DIR / srt_name
                    blocks = self._parse_srt(srt_path)
                    if blocks is None:
                        continue

                    out_srt = TRANSLATED_DIR / srt_name
                    out_log = TRANSLATED_DIR / srt_name.replace(".srt", ".txt")

                    TRANSLATED_DIR.mkdir(parents=True, exist_ok=True)
                    with open(out_srt, "w", encoding="utf-8") as f:
                        f.write("")
                    with open(out_log, "w", encoding="utf-8") as f:
                        f.write("")

                    file_fails = []
                    file_total = len(blocks)

                    for bi, (idx, timestamp, content) in enumerate(blocks):
                        if self._stop_flag.is_set():
                            file_fails.extend(blocks[bi:])
                            break
                        processed += 1
                        pct = processed / total_blocks_est if total_blocks_est > 0 else 0
                        self.queue.put({
                            "type": "translate_progress",
                            "value": pct,
                            "file": srt_name,
                            "detail": f"Block {idx}/{file_total}..."
                        })

                        self.queue.put({
                            "type": "translate_log",
                            "message": f"[{srt_name}] Block {idx}/{file_total}..."
                        })

                        translated = self._call_ollama(content)

                        if translated.startswith("[TRANSLATION ERROR"):
                            self.queue.put({
                                "type": "translate_log",
                                "message": f"  FAILED: {translated}"
                            })
                            file_fails.append({
                                "idx": idx, "timestamp": timestamp, "content": content
                            })
                        else:
                            block_str = f"{idx}\n{timestamp}\n{translated}"
                            try:
                                self._append_to_file(out_srt, block_str + "\n\n")
                                self._write_log_entry(srt_name, f"Block {idx} - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
                            except Exception as e:
                                self.queue.put({
                                    "type": "translate_log",
                                    "message": f"  Error writing block {idx}: {e}"
                                })

                    if file_fails:
                        self._translate_failed_blocks[srt_name] = file_fails
                        self.queue.put({
                            "type": "translate_log",
                            "message": f"[{srt_name}] {len(file_fails)}/{file_total} blocks failed."
                        })
                    else:
                        self.queue.put({
                            "type": "translate_log",
                            "message": f"[{srt_name}] All {file_total} blocks translated successfully."
                        })
                except Exception as e:
                    self.queue.put({"type": "translate_log", "message": f"Error processing '{srt_name}': {e}"})
                    import traceback
                    for line in traceback.format_exc().split("\n"):
                        if line.strip():
                            self.queue.put({"type": "translate_log", "message": line})

            self.queue.put({"type": "translate_progress", "value": 1.0, "file": "", "detail": ""})
            self.queue.put({"type": "translate_status", "message": "Translation complete."})
        except Exception as e:
            self.queue.put({"type": "translate_log", "message": f"Translate worker error: {e}"})
            import traceback
            self.queue.put({"type": "translate_log", "message": traceback.format_exc()})
        finally:
            self.queue.put({"type": "translate_done"})
            self.queue.put({"type": "scan_translated"})

    def _call_ollama(self, text: str) -> str:
        prompt = (
            f"You are a precise subtitle translation assistant. Translate the following SRT subtitle text from "
            f"{self.config.get('source_lang', 'English')} to {self.config.get('target_lang', 'Bulgarian')}.\n"
            "CRITICAL RULES:\n"
            "1. Translate the text naturally and idiomatically into "
            f"{self.config.get('target_lang', 'Bulgarian')}, adapting cultural nuances where necessary.\n"
            "2. DO NOT wrap the output in markdown code blocks.\n"
            "3. DO NOT add any introductory text, explanations, or closing remarks. Output ONLY the translated text.\n"
            f"Text to translate:\n{text}"
        )
        payload = json.dumps({
            "model": self.config.get("model", "translategemma:12b"),
            "prompt": prompt,
            "stream": False,
            "options": {"num_predict": int(self.config.get("num_predict", 512))}
        }).encode("utf-8")

        req = urllib.request.Request(
            self.config.get("ollama_url", "http://localhost:11434/api/generate"),
            data=payload,
            headers={"Content-Type": "application/json"}
        )
        try:
            timeout = int(self.config.get("timeout", 120))
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                response = result.get("response", "").strip()
                if not response:
                    return "[TRANSLATION ERROR: Empty response from model]"
                return response
        except urllib.error.HTTPError as e:
            return f"[TRANSLATION ERROR: HTTP {e.code} - {e.reason}]"
        except urllib.error.URLError as e:
            return f"[TRANSLATION ERROR: Cannot reach Ollama - {e.reason}]"
        except json.JSONDecodeError:
            return "[TRANSLATION ERROR: Invalid JSON response from Ollama]"
        except OSError as e:
            return f"[TRANSLATION ERROR: Network error - {e}]"
        except Exception as e:
            return f"[TRANSLATION ERROR: {e}]"

    def _parse_srt(self, filepath: Path):
        if not filepath.is_file():
            self.queue.put({"type": "translate_log", "message": f"File not found: {filepath}"})
            return None
        try:
            with open(filepath, "r", encoding="utf-8-sig") as f:
                text = f.read()
        except PermissionError:
            self.queue.put({"type": "translate_log", "message": f"Permission denied: {filepath}"})
            return None
        except Exception as e:
            self.queue.put({"type": "translate_log", "message": f"Error reading '{filepath.name}': {e}"})
            return None

        if not text.strip():
            self.queue.put({"type": "translate_log", "message": f"Empty file: {filepath.name}"})
            return None

        blocks = re.split(r'\n\s*\n', text.strip())
        result = []
        for block in blocks:
            lines = block.strip().split("\n")
            if len(lines) >= 2 and "-->" in lines[1]:
                idx = lines[0].strip()
                timestamp = lines[1].strip()
                content = "\n".join(lines[2:]).strip()
                if content:
                    result.append((idx, timestamp, content))
        if not result:
            self.queue.put({"type": "translate_log", "message": f"No valid SRT blocks found in '{filepath.name}'."})
            return None
        return result

    @staticmethod
    def _append_to_file(filepath: Path, content: str):
        with open(filepath, "a", encoding="utf-8") as f:
            f.write(content)

    def _write_log_entry(self, srt_name: str, line: str):
        log_path = TRANSLATED_DIR / srt_name.replace(".srt", ".txt")
        try:
            if log_path.is_file():
                with open(log_path, "r", encoding="utf-8") as f:
                    old = f.read()
            else:
                old = ""
            with open(log_path, "w", encoding="utf-8") as f:
                f.write(line + "\n" + old)
        except Exception as e:
            self.queue.put({"type": "translate_log", "message": f"Error writing log: {e}"})

    # ─── Queue consumer ─────────────────────────────────────────────

    def _check_queue(self):
        try:
            while True:
                msg = self.queue.get_nowait()
                self._handle_message(msg)
        except queue.Empty:
            pass
        self.after(150, self._check_queue)

    def _handle_message(self, msg):
        t = msg.get("type", "")

        if t == "extract_log":
            self._log_extract(msg["message"])
        elif t == "extract_logs":
            logs = msg.get("logs", [])
            if logs:
                last = logs[-1]
                self._log_extract(last)
            for line in logs:
                self._append_log(line)
        elif t == "extract_status":
            self.extract_status.configure(text=msg["message"])
        elif t == "extract_progress":
            self.extract_progress.set(msg["value"])
        elif t == "extract_done":
            self._reapply_button_state(False)
            self._scan_output_srts()
            self._scan_translated()
            self.extract_progress.set(0)
        elif t == "scan_output":
            self._scan_output_srts()
            self._scan_translated()
        elif t == "scan_translated":
            self._scan_translated()
        elif t == "translate_log":
            self._append_log(msg["message"])
        elif t == "translate_progress":
            self.translate_progress.set(msg["value"])
            pct = int(msg["value"] * 100)
            self.translate_pct.configure(text=f"{pct}%")
            file_info = msg.get("file", "")
            detail = msg.get("detail", "")
            if file_info and detail:
                self.translate_status.configure(text=f"{file_info} - {detail}")
            elif file_info:
                self.translate_status.configure(text=f"Translating: {file_info}")
        elif t == "translate_status":
            self.translate_status.configure(text=msg["message"])
        elif t == "translate_done":
            self._reapply_button_state(False)
            self._scan_translated()
            self.translate_progress.set(0)
            self.translate_pct.configure(text="")
            remaining = sum(len(v) for v in self._translate_failed_blocks.values())
            if remaining > 0:
                self._append_log(f"\n*** {remaining} block{'s' if remaining != 1 else ''} failed. Click 'Retry Failed Blocks' to retry. ***")

    def _log_extract(self, message: str):
        self.extract_status.configure(text=message)

    def _append_log(self, message: str):
        self.translate_log.configure(state="normal")
        self.translate_log.insert(tk.END, message + "\n")
        self.translate_log.see(tk.END)
        self.translate_log.configure(state="disabled")

    def _reapply_button_state(self, busy: bool = False):
        state = "disabled" if busy else "normal"
        for btn in [self.extract_selected_btn, self.extract_all_btn,
                     self.translate_selected_btn, self.translate_all_btn,
                     self.translate_retry_btn, self.extract_scan_btn,
                     self.translate_scan_btn]:
            btn.configure(state=state)

    # ─── Settings ───────────────────────────────────────────────────

    def _save_settings(self):
        for key, var in self.settings_entries.items():
            val = var.get().strip()
            if not val:
                self.settings_status.configure(text=f"Cannot be empty: {key}", text_color="#cc6666")
                return
            self.config.set(key, val)
        result = self.config.save()
        if result is True:
            self.settings_status.configure(text="Settings saved successfully.", text_color="#88aa88")
        else:
            self.settings_status.configure(text=f"Error saving: {result}", text_color="#cc6666")
        self.after(3000, lambda: self.settings_status.configure(text=""))

    def _reset_settings(self):
        self.config.data = dict(DEFAULT_CONFIG)
        for key, var in self.settings_entries.items():
            var.set(str(DEFAULT_CONFIG.get(key, "")))
        self.config.save()
        self.settings_status.configure(text="Settings reset to defaults.", text_color="#88aa88")
        self.after(3000, lambda: self.settings_status.configure(text=""))

    # ─── Misc ───────────────────────────────────────────────────────

    def _stop_worker(self):
        if self._worker and self._worker.is_alive():
            self._stop_flag.set()
            self._append_log("*** Stop requested, finishing current block... ***")
        else:
            self._append_log("No operation in progress.")

    def _on_close(self):
        if self._worker and self._worker.is_alive():
            if not messagebox.askokcancel("Quit", "A task is running. Are you sure you want to exit?"):
                return
            self._stop_flag.set()
        self.destroy()


if __name__ == "__main__":
    try:
        app = SubtitleApp()
        app.mainloop()
    except Exception as e:
        messagebox.showerror("Fatal Error", f"Application failed to start:\n{e}")
        sys.exit(1)
