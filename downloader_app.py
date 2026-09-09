# -*- coding: utf-8 -*-
"""メディアダウンローダー (tkinter GUI)

使い方:
    python downloader_app.py
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
import traceback
from tkinter import filedialog, messagebox, ttk

APP_TITLE = "メディアダウンローダー"
APP_VERSION = "1.0"

IS_FROZEN = getattr(sys, "frozen", False)

# exe 化した場合は exe のあるフォルダ、通常実行なら .py のあるフォルダ
APP_DIR = (os.path.dirname(sys.executable) if IS_FROZEN
           else os.path.dirname(os.path.abspath(__file__)))
if not IS_FROZEN and APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)


def _fatal(title, message):
    """起動できないレベルのエラーをダイアログで知らせて終了する。"""
    try:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(title, message)
        root.destroy()
    except Exception:
        print(message)
    sys.exit(1)


try:
    import binaries
    import ytdlp_runner as runner
except Exception as _e:
    _fatal(
        APP_TITLE,
        "必要なファイルが読み込めませんでした。\n\n"
        + ("・同梱ファイルが壊れている可能性があります。\n"
           "　配布元から入手し直してください。\n\n"
           if IS_FROZEN else
           "・downloader_app.py と同じフォルダに\n"
           "　binaries.py / ytdlp_runner.py があるか確認してください。\n\n")
        + "詳細: {}: {}".format(type(_e).__name__, _e),
    )


DISCLAIMER = (
    "このツールについて\n"
    "\n"
    "自分がアップロードした動画、配信のアーカイブ、権利者が許可している\n"
    "コンテンツを手元に保存するためのツールです。\n"
    "\n"
    "権利者に無断で公開されているものをダウンロードする行為は、\n"
    "私的使用が目的であっても違法となる場合があります。\n"
    "利用する各サービスの規約もあわせて確認し、自己責任でご利用ください。\n"
    "\n"
    "初回だけ、動作に必要な部品（yt-dlp と FFmpeg）を\n"
    "配布元から自動で取得します。"
)


def _settings_path():
    """設定ファイルの保存先。書き込めない場所に置かれても動くようにする。"""
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    folder = os.path.join(base, "johukku", "media-downloader")
    try:
        os.makedirs(folder, exist_ok=True)
        return os.path.join(folder, "settings.json")
    except OSError:
        return os.path.join(APP_DIR, "settings.json")


SETTINGS_PATH = _settings_path()


def default_outdir():
    """既定の保存先。ダウンロードフォルダが無ければデスクトップ、それも無ければ home。"""
    home = os.path.expanduser("~")
    for name in ("Downloads", "Desktop"):
        path = os.path.join(home, name)
        if os.path.isdir(path):
            return path
    return home


def looks_like_url(text):
    text = (text or "").strip()
    return text.startswith("http://") or text.startswith("https://")


class App:
    def __init__(self, root):
        self.root = root
        self.queue = queue.Queue()
        self.worker = None
        self.job = None
        self.stop_all = False
        self.settings = self._load_settings()

        self.var_outdir = tk.StringVar(value=self.settings.get("outdir", default_outdir()))
        self.var_mode = tk.StringVar(value=self.settings.get("mode", "video_best"))
        self.var_playlist = tk.BooleanVar(value=self.settings.get("playlist", False))
        self.var_subs = tk.BooleanVar(value=self.settings.get("subs", False))
        self.var_mp4 = tk.BooleanVar(value=self.settings.get("mp4_only", False))
        self.var_status = tk.StringVar(value="準備中...")
        self.var_parts = tk.StringVar(value="")

        self.root.title("{} v{}".format(APP_TITLE, APP_VERSION))
        self.root.geometry("{}x{}".format(
            self.settings.get("width", 780), self.settings.get("height", 640)))
        self.root.minsize(700, 560)

        self._build_ui()
        self._update_states()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(100, self._pump)
        self.root.after(200, self._first_run)

    # ------------------------------------------------------------ 画面

    def _build_ui(self):
        pad = {"padx": 8, "pady": 6}
        outer = ttk.Frame(self.root)
        outer.pack(fill="both", expand=True, padx=10, pady=10)

        # --- 1. URL ---
        f_url = ttk.LabelFrame(outer, text="1. URL")
        f_url.pack(fill="x", **pad)

        body = ttk.Frame(f_url)
        body.pack(fill="both", expand=True, padx=8, pady=8)

        wrap = ttk.Frame(body)
        wrap.pack(side="left", fill="both", expand=True)
        self.txt_url = tk.Text(wrap, height=4, wrap="none", undo=True)
        ysb = ttk.Scrollbar(wrap, orient="vertical", command=self.txt_url.yview)
        self.txt_url.configure(yscrollcommand=ysb.set)
        self.txt_url.grid(row=0, column=0, sticky="nsew")
        ysb.grid(row=0, column=1, sticky="ns")
        wrap.rowconfigure(0, weight=1)
        wrap.columnconfigure(0, weight=1)

        btns = ttk.Frame(body)
        btns.pack(side="left", fill="y", padx=(8, 0))
        ttk.Button(btns, text="貼り付け", width=12,
                   command=self.on_paste).pack(fill="x", pady=2)
        ttk.Button(btns, text="クリア", width=12,
                   command=self.on_clear_url).pack(fill="x", pady=2)

        ttk.Label(f_url, text="複数まとめて処理したいときは、1 行に 1 つずつ貼り付けてください。",
                  foreground="#666").pack(anchor="w", padx=10, pady=(0, 8))

        # --- 2. 保存先 ---
        f_out = ttk.LabelFrame(outer, text="2. 保存先")
        f_out.pack(fill="x", **pad)

        row = ttk.Frame(f_out)
        row.pack(fill="x", padx=8, pady=8)
        self.entry_outdir = ttk.Entry(row, textvariable=self.var_outdir)
        self.entry_outdir.pack(side="left", fill="x", expand=True)
        self.btn_browse = ttk.Button(row, text="参照...", width=10,
                                     command=self.on_browse)
        self.btn_browse.pack(side="left", padx=(6, 0))
        ttk.Button(row, text="開く", width=8,
                   command=self.on_open_outdir).pack(side="left", padx=(6, 0))

        # --- 3. 形式 ---
        f_mode = ttk.LabelFrame(outer, text="3. 形式")
        f_mode.pack(fill="x", **pad)

        grid = ttk.Frame(f_mode)
        grid.pack(fill="x", padx=8, pady=(8, 4))
        for i, (label, key) in enumerate(runner.MODES):
            ttk.Radiobutton(grid, text=label, value=key, variable=self.var_mode,
                            command=self._update_states).grid(
                                row=i // 3, column=i % 3, sticky="w", padx=(0, 16), pady=2)

        opts = ttk.Frame(f_mode)
        opts.pack(fill="x", padx=8, pady=(0, 2))
        ttk.Checkbutton(opts, text="プレイリストをまとめて取得",
                        variable=self.var_playlist).pack(side="left")
        self.chk_subs = ttk.Checkbutton(opts, text="字幕ファイルも保存する（動画のみ）",
                                        variable=self.var_subs)
        self.chk_subs.pack(side="left", padx=(16, 0))

        opts2 = ttk.Frame(f_mode)
        opts2.pack(fill="x", padx=8, pady=(0, 8))
        self.chk_mp4 = ttk.Checkbutton(opts2, text="mp4 に統一する（mkv を避ける）",
                                       variable=self.var_mp4)
        self.chk_mp4.pack(side="left")
        ttk.Label(opts2, text="※ 古い機器や編集ソフト向け。画質が 1 段下がることがあります",
                  foreground="#666").pack(side="left", padx=(8, 0))

        # --- 実行 ---
        run_row = ttk.Frame(outer)
        run_row.pack(fill="x", **pad)
        self.btn_start = ttk.Button(run_row, text="ダウンロード", width=18,
                                    command=self.on_start)
        self.btn_start.pack(side="left")
        self.btn_cancel = ttk.Button(run_row, text="中止", width=10,
                                     command=self.on_cancel, state="disabled")
        self.btn_cancel.pack(side="left", padx=(8, 0))
        self.progress = ttk.Progressbar(run_row, mode="determinate", maximum=100)
        self.progress.pack(side="left", fill="x", expand=True, padx=(12, 0))

        ttk.Label(outer, textvariable=self.var_status).pack(anchor="w", padx=8)

        # --- ログ ---
        f_log = ttk.LabelFrame(outer, text="ログ")
        f_log.pack(fill="both", expand=True, **pad)
        log_wrap = ttk.Frame(f_log)
        log_wrap.pack(fill="both", expand=True, padx=8, pady=8)
        self.txt_log = tk.Text(log_wrap, height=8, wrap="none", state="disabled")
        lsb = ttk.Scrollbar(log_wrap, orient="vertical", command=self.txt_log.yview)
        self.txt_log.configure(yscrollcommand=lsb.set)
        self.txt_log.grid(row=0, column=0, sticky="nsew")
        lsb.grid(row=0, column=1, sticky="ns")
        log_wrap.rowconfigure(0, weight=1)
        log_wrap.columnconfigure(0, weight=1)

        # --- 下段 ---
        bottom = ttk.Frame(outer)
        bottom.pack(fill="x", **pad)
        ttk.Label(bottom, textvariable=self.var_parts, foreground="#666").pack(side="left")
        self.btn_reinstall = ttk.Button(bottom, text="部品を入れ直す", width=14,
                                        command=self.on_reinstall)
        self.btn_reinstall.pack(side="right")
        self.btn_update = ttk.Button(bottom, text="yt-dlp を更新", width=14,
                                     command=self.on_update)
        self.btn_update.pack(side="right", padx=(0, 6))

    # ------------------------------------------------------------ 起動時

    def _first_run(self):
        if not self.settings.get("agreed"):
            messagebox.showinfo(APP_TITLE, DISCLAIMER)
            self.settings["agreed"] = True
            self._save_settings()

        self._refresh_parts()
        self.on_paste(silent=True)

        lack = binaries.missing()
        if lack:
            sizes = "\n".join("　・{}（{}）".format(n, binaries.APPROX_SIZE.get(n, ""))
                              for n in lack)
            ok = messagebox.askyesno(
                APP_TITLE,
                "動作に必要な部品がまだありません。\n"
                "いま取得しますか？（初回だけです）\n\n{}\n\n"
                "保存先: {}".format(sizes, binaries.bin_dir()))
            if ok:
                self._start_setup()
            else:
                self.var_status.set("部品が未取得です。ダウンロード時に改めて確認します。")
        else:
            self.var_status.set("URL を貼り付けて「ダウンロード」を押してください。")

    def _refresh_parts(self):
        version = binaries.ytdlp_version()
        parts = "yt-dlp {}".format(version or "未取得")
        parts += "　/　FFmpeg {}".format("あり" if binaries.ffmpeg_ok() else "未取得")
        self.var_parts.set(parts)

    # ------------------------------------------------------------ 操作

    def on_paste(self, silent=False):
        """クリップボードの中身が URL なら URL 欄の末尾に足す。"""
        try:
            text = self.root.clipboard_get()
        except Exception:
            text = ""
        if not looks_like_url(text):
            if not silent:
                messagebox.showinfo(APP_TITLE, "クリップボードに URL が入っていません。")
            return
        current = self.txt_url.get("1.0", "end").strip()
        if text.strip() in current.splitlines():
            return
        if current:
            self.txt_url.insert("end", "\n" + text.strip())
        else:
            self.txt_url.insert("1.0", text.strip())

    def on_clear_url(self):
        self.txt_url.delete("1.0", "end")

    def on_browse(self):
        path = filedialog.askdirectory(initialdir=self.var_outdir.get() or default_outdir())
        if path:
            self.var_outdir.set(os.path.normpath(path))

    def on_open_outdir(self):
        path = self.var_outdir.get()
        if not os.path.isdir(path):
            messagebox.showinfo(APP_TITLE, "保存先フォルダが見つかりません。")
            return
        try:
            os.startfile(path)
        except OSError as e:
            messagebox.showerror(APP_TITLE, "フォルダを開けませんでした。\n\n{}".format(e))

    def on_start(self):
        if self.worker and self.worker.is_alive():
            return

        urls = [u.strip() for u in self.txt_url.get("1.0", "end").splitlines() if u.strip()]
        bad = [u for u in urls if not looks_like_url(u)]
        if not urls:
            messagebox.showinfo(APP_TITLE, "URL を貼り付けてください。")
            return
        if bad:
            messagebox.showwarning(
                APP_TITLE,
                "URL として読めない行があります。\n"
                "http:// または https:// で始まる行だけにしてください。\n\n"
                + "\n".join(bad[:3]))
            return

        outdir = self.var_outdir.get().strip()
        if not os.path.isdir(outdir):
            messagebox.showwarning(APP_TITLE, "保存先フォルダが見つかりません。")
            return

        if binaries.missing():
            if not messagebox.askyesno(
                    APP_TITLE,
                    "先に必要な部品を取得します。よろしいですか？"):
                return
            self._start_setup(then_download=urls)
            return

        self._start_download(urls)

    def on_cancel(self):
        self.stop_all = True
        if self.job:
            self.job.cancel()
        self.var_status.set("中止しています...")

    def on_update(self):
        if self._busy():
            return
        self._set_busy(True, cancellable=False)
        self.var_status.set("yt-dlp を更新しています...")
        self._log("── yt-dlp の更新 ──")
        self._run_in_thread(self._work_update)

    def on_reinstall(self):
        if self._busy():
            return
        if not messagebox.askyesno(
                APP_TITLE,
                "yt-dlp と FFmpeg を取得し直します。\n"
                "（{}）\n\nよろしいですか？".format(binaries.bin_dir())):
            return
        for path in (binaries.ytdlp_path(), binaries.ffmpeg_path(), binaries.ffprobe_path()):
            try:
                os.remove(path)
            except OSError:
                pass
        self._start_setup()

    def on_close(self):
        if self._busy():
            if not messagebox.askyesno(APP_TITLE, "処理中です。終了しますか？"):
                return
            self.stop_all = True
            if self.job:
                self.job.cancel()
        self._save_settings()
        self.root.destroy()

    # ------------------------------------------------------------ 実行

    def _busy(self):
        return bool(self.worker and self.worker.is_alive())

    def _run_in_thread(self, func):
        self.worker = threading.Thread(target=self._guard, args=(func,), daemon=True)
        self.worker.start()

    def _guard(self, func):
        """作業スレッドの例外をログに落として、ボタンを戻す。"""
        try:
            func()
        except binaries.SetupError as e:
            self.queue.put(("log", str(e)))
            self.queue.put(("done", False, str(e)))
        except Exception:
            self.queue.put(("log", traceback.format_exc()))
            self.queue.put(("done", False, "予期しないエラーが起きました。ログを確認してください。"))

    def _start_setup(self, then_download=None):
        self._set_busy(True, cancellable=False)
        self.var_status.set("必要な部品を取得しています...")
        self._log("── 部品の取得 ──")
        self._log("保存先: {}".format(binaries.bin_dir()))
        self._run_in_thread(lambda: self._work_setup(then_download))

    def _work_setup(self, then_download):
        def on_progress(done, total, label):
            if done < 0:
                self.queue.put(("status", label))
            elif total > 0:
                self.queue.put(("prog", done * 100.0 / total,
                                "{} を取得中".format(label), ""))
            else:
                self.queue.put(("prog", None, "{} を取得中".format(label), ""))

        binaries.ensure_all(on_progress)
        self.queue.put(("log", "部品の取得が完了しました。"))
        self.queue.put(("parts", None))
        if then_download:
            self.queue.put(("chain", then_download))
        else:
            self.queue.put(("done", True, "部品の準備ができました。"))

    def _work_update(self):
        ok, message = binaries.update_ytdlp()
        for line in message.splitlines():
            self.queue.put(("log", line))
        self.queue.put(("parts", None))
        self.queue.put(("done", ok,
                        "yt-dlp を更新しました。" if ok else "更新に失敗しました。"))

    def _start_download(self, urls):
        self.stop_all = False
        self._set_busy(True, cancellable=True)
        self._save_settings()
        mode = self.var_mode.get()
        outdir = self.var_outdir.get().strip()
        playlist = self.var_playlist.get()
        subs = self.var_subs.get() and mode.startswith("video")
        mp4_only = self.var_mp4.get() and mode.startswith("video")
        label = runner.MODE_NAMES.get(mode, mode)
        if mp4_only:
            label += " / mp4 に統一"
        self._log("── ダウンロード開始（{}）──".format(label))
        self._run_in_thread(
            lambda: self._work_download(urls, mode, outdir, playlist, subs, mp4_only))

    def _work_download(self, urls, mode, outdir, playlist, subs, mp4_only):
        done = 0
        failed = []
        for index, url in enumerate(urls, 1):
            if self.stop_all:
                break
            head = "（{}/{}）".format(index, len(urls)) if len(urls) > 1 else ""
            self.queue.put(("status", "{}取得しています...".format(head)))
            self.queue.put(("log", "{} {}".format(head, url).strip()))

            self.job = runner.Job(url, mode, outdir, playlist, subs, mp4_only)
            code = self.job.run(
                on_log=lambda line: self.queue.put(("log", line)),
                on_progress=lambda p, s, e: self.queue.put(
                    ("prog", p, "{}ダウンロード中".format(head), "{}  残り {}".format(s, e))),
            )

            if code is None:
                break
            if code == 0:
                done += 1
                for path in self.job.produced():
                    self.queue.put(("log", "保存: {}".format(path)))
            else:
                failed.append(url)
                text = self.job.error_text()
                hint = runner.hint_for(text)
                if hint:
                    self.queue.put(("log", hint))

        self.job = None
        if self.stop_all:
            summary = "中止しました。（完了 {} 件）".format(done)
            self.queue.put(("done", False, summary))
        elif failed:
            summary = "完了 {} 件 / 失敗 {} 件。ログを確認してください。".format(done, len(failed))
            self.queue.put(("done", False, summary))
        else:
            summary = "完了しました。（{} 件）".format(done)
            self.queue.put(("done", True, summary))

    # ------------------------------------------------------------ 表示の更新

    def _pump(self):
        """作業スレッドからの連絡をまとめて画面に反映する。"""
        try:
            while True:
                msg = self.queue.get_nowait()
                kind = msg[0]
                if kind == "log":
                    self._log(msg[1])
                elif kind == "status":
                    self.var_status.set(msg[1])
                elif kind == "prog":
                    percent, label, tail = msg[1], msg[2], msg[3]
                    if percent is None:
                        self.progress.configure(mode="indeterminate")
                        self.progress.start(30)
                    else:
                        self.progress.stop()
                        self.progress.configure(mode="determinate")
                        self.progress["value"] = percent
                    text = label
                    if percent is not None:
                        text += "  {:.1f}%".format(percent)
                    if tail.strip():
                        text += "　{}".format(tail)
                    self.var_status.set(text)
                elif kind == "parts":
                    self._refresh_parts()
                elif kind == "chain":
                    self._refresh_parts()
                    self._start_download(msg[1])
                elif kind == "done":
                    self.progress.stop()
                    self.progress.configure(mode="determinate")
                    self.progress["value"] = 0
                    self.var_status.set(msg[2])
                    self._log(msg[2])
                    self._refresh_parts()
                    self._set_busy(False)
        except queue.Empty:
            pass
        self.root.after(100, self._pump)

    def _log(self, text):
        self.txt_log.configure(state="normal")
        self.txt_log.insert("end", text + "\n")
        self.txt_log.see("end")
        self.txt_log.configure(state="disabled")

    def _set_busy(self, busy, cancellable=True):
        state = "disabled" if busy else "normal"
        for widget in (self.btn_start, self.btn_browse,
                       self.btn_update, self.btn_reinstall):
            widget.configure(state=state)
        self.btn_cancel.configure(state="normal" if (busy and cancellable) else "disabled")
        self._update_states()

    def _update_states(self):
        """動画モードのときだけ、動画向けのオプションを触れるようにする。"""
        is_video = self.var_mode.get().startswith("video")
        state = "normal" if is_video else "disabled"
        self.chk_subs.configure(state=state)
        self.chk_mp4.configure(state=state)

    # ------------------------------------------------------------ 設定

    def _load_settings(self):
        try:
            with open(SETTINGS_PATH, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_settings(self):
        self.settings.update({
            "outdir": self.var_outdir.get(),
            "mode": self.var_mode.get(),
            "playlist": self.var_playlist.get(),
            "subs": self.var_subs.get(),
            "mp4_only": self.var_mp4.get(),
            "width": self.root.winfo_width(),
            "height": self.root.winfo_height(),
        })
        try:
            with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
                json.dump(self.settings, f, ensure_ascii=False, indent=2)
        except OSError:
            pass


def main():
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista")
    except tk.TclError:
        pass
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
