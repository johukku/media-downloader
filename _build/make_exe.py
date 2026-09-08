# -*- coding: utf-8 -*-
r"""PyInstaller で単体 exe 版（onedir）をビルドする（開発者用）

    python _build\make_exe.py

このツールは標準ライブラリと tkinter しか使わないので、出来上がりは 15MB 前後です。
yt-dlp と FFmpeg は同梱せず、利用者の PC が初回起動時に取得します。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import zipfile

BUILD_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BUILD_DIR)

EXE_NAME = "MediaDownloader"          # ビルド中は ASCII 名（後で日本語名に変更）
FINAL_EXE = "メディアダウンローダー.exe"
WORK = os.path.join(BUILD_DIR, "pyinstaller")
DIST = os.path.join(BUILD_DIR, "dist")


def get_version():
    with open(os.path.join(ROOT, "downloader_app.py"), encoding="utf-8") as f:
        for line in f:
            if line.startswith("APP_VERSION"):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return "0.0"


ARGS = [
    "--noconfirm",
    "--clean",
    "--onedir",       # onefile は毎回の展開でウイルス対策ソフトに引っかかりやすい
    "--windowed",     # 進捗はアプリ内のログ欄に出すので、コンソールは要らない
    "--name", EXE_NAME,
    "--distpath", DIST,
    "--workpath", os.path.join(WORK, "build"),
    "--specpath", WORK,

    # 同じフォルダの自作モジュール
    "--hidden-import", "binaries",
    "--hidden-import", "ytdlp_runner",

    # 使わない重いものを巻き込まないように明示的に外す
    "--exclude-module", "numpy",
    "--exclude-module", "torch",
    "--exclude-module", "PIL",
    "--exclude-module", "matplotlib",
    "--exclude-module", "test",
    "--exclude-module", "unittest",
]


def main():
    version = get_version()
    print("=" * 60)
    print(" exe ビルド開始   version {}".format(version))
    print("=" * 60)

    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("PyInstaller が入っていません。インストールします...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])

    os.makedirs(DIST, exist_ok=True)
    cmd = [sys.executable, "-m", "PyInstaller"] + ARGS + [
        os.path.join(ROOT, "downloader_app.py")
    ]
    print(" ".join(cmd))
    print()
    rc = subprocess.call(cmd, cwd=ROOT)
    if rc != 0:
        print("\n!! ビルドに失敗しました (exit {})".format(rc))
        return rc

    out_dir = os.path.join(DIST, EXE_NAME)

    # exe を日本語名にリネーム（onedir なので _internal の探索には影響しない）
    src_exe = os.path.join(out_dir, EXE_NAME + ".exe")
    dst_exe = os.path.join(out_dir, FINAL_EXE)
    if os.path.exists(src_exe):
        if os.path.exists(dst_exe):
            os.remove(dst_exe)
        os.rename(src_exe, dst_exe)

    readme = os.path.join(BUILD_DIR, "readme_exe.txt")
    if os.path.exists(readme):
        shutil.copy(readme, os.path.join(out_dir, "はじめにお読みください.txt"))

    zip_path = _make_zip(out_dir, version)

    size = _dir_size(out_dir)
    print()
    print("=" * 60)
    print("完成: {}".format(out_dir))
    print("サイズ: {:,.1f} MB".format(size / (1024 * 1024)))
    print("配布用 ZIP: {}  ({:,.1f} MB)".format(
        zip_path, os.path.getsize(zip_path) / (1024 * 1024)))
    print("=" * 60)
    print()
    print("受け取った人は「{}」をダブルクリックするだけです。".format(FINAL_EXE))
    print("yt-dlp と FFmpeg は初回起動時に自動で取得されます。")
    return 0


def _make_zip(out_dir, version):
    """配布用 ZIP を作る。ファイル名は ASCII（環境によって文字化けするため）。"""
    zip_path = os.path.join(DIST, "MediaDownloader-v{}-win64.zip".format(version))
    if os.path.exists(zip_path):
        os.remove(zip_path)
    base = os.path.basename(out_dir)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _dirs, files in os.walk(out_dir):
            for name in files:
                full = os.path.join(root, name)
                rel = os.path.join(base, os.path.relpath(full, out_dir))
                z.write(full, rel)
    return zip_path


def _dir_size(path):
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


if __name__ == "__main__":
    sys.exit(main())
