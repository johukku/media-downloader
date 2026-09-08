# -*- coding: utf-8 -*-
r"""yt-dlp と FFmpeg を自動で取得して共有フォルダに置く。

    %LOCALAPPDATA%\johukku\bin\

配布物にバイナリを同梱しない理由:
  - FFmpeg は GPL なので、同梱して配ると対応するソースの提供義務が生じる。
    利用者の PC が公式ビルドを直接取得する形なら、こちらは何も再配布しない。
  - yt-dlp は動画サイト側の仕様変更で頻繁に壊れるため、本体と切り離して
    単独で更新できるほうが都合がよい。
  - 他の johukku 製ツールと同じ場所を使うので、2 本目以降は取得を省ける。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile

# yt-dlp 公式のリリース。/latest/download/ なので API を叩かずに最新が取れる
YTDLP_URL = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"

# FFmpeg は yt-dlp 公式ビルドを使う（yt-dlp 向けのパッチが当たっている）。
# shared 版は DLL が分かれる代わりに、静的版の半分以下で済む
# （取得 73MB / 展開 178MB に対して、静的版は 163MB / 278MB）。
FFMPEG_URL = ("https://github.com/yt-dlp/FFmpeg-Builds/releases/latest/download/"
              "ffmpeg-master-latest-win64-gpl-shared.zip")

USER_AGENT = "johukku-media-downloader/1.0 (+https://johukku.pages.dev/)"

# 取得前の確認ダイアログに出すおおよそのサイズ
APPROX_SIZE = {
    "yt-dlp": "約 17 MB",
    "FFmpeg": "約 73 MB（展開後 約 180 MB）",
}

# GUI から起動したときにコンソール窓を出さないためのフラグ
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class SetupError(Exception):
    """部品の取得に失敗した。メッセージはそのまま利用者に見せる。"""


def bin_dir():
    """バイナリの共有置き場。作れなければ例外にせず temp を返す。"""
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    folder = os.path.join(base, "johukku", "bin")
    try:
        os.makedirs(folder, exist_ok=True)
    except OSError:
        folder = os.path.join(tempfile.gettempdir(), "johukku-bin")
        os.makedirs(folder, exist_ok=True)
    return folder


def ytdlp_path():
    return os.path.join(bin_dir(), "yt-dlp.exe")


def ffmpeg_path():
    return os.path.join(bin_dir(), "ffmpeg.exe")


def ffprobe_path():
    return os.path.join(bin_dir(), "ffprobe.exe")


def _usable(path, min_size=100 * 1024):
    """置いてあるだけでなく、途中で切れていないかも軽く見る。"""
    try:
        return os.path.isfile(path) and os.path.getsize(path) > min_size
    except OSError:
        return False


def missing(need_ffmpeg=True):
    """まだ無い部品の名前を並べて返す。空リストならすぐ使える。"""
    lack = []
    if not _usable(ytdlp_path()):
        lack.append("yt-dlp")
    if need_ffmpeg and not ffmpeg_ok():
        lack.append("FFmpeg")
    return lack


def ffmpeg_ok():
    """shared 版なので、exe だけでなく DLL が揃っているかも見る。"""
    if not (_usable(ffmpeg_path(), 64 * 1024) and _usable(ffprobe_path(), 64 * 1024)):
        return False
    try:
        names = os.listdir(bin_dir())
    except OSError:
        return False
    return any(n.startswith("avcodec-") and n.endswith(".dll") for n in names)


# ---------------------------------------------------------------- 取得


def _download(url, dest, on_progress=None, label=""):
    """url を dest に保存する。途中経過は on_progress(受信バイト, 全体バイト, 名前)。

    書きかけを残さないよう、同じフォルダに .part で受けてから差し替える。
    """
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    tmp = dest + ".part"
    try:
        with urllib.request.urlopen(req, timeout=60) as res:
            total = int(res.headers.get("Content-Length") or 0)
            done = 0
            with open(tmp, "wb") as f:
                while True:
                    chunk = res.read(256 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
                    done += len(chunk)
                    if on_progress:
                        on_progress(done, total, label)
    except urllib.error.HTTPError as e:
        _remove(tmp)
        raise SetupError(
            "{} の取得に失敗しました（HTTP {}）。\n"
            "時間をおいて試すか、ネットワークの設定を確認してください。".format(label, e.code))
    except urllib.error.URLError as e:
        _remove(tmp)
        raise SetupError(
            "{} の取得に失敗しました。\n"
            "インターネットに接続できていないか、\n"
            "ウイルス対策ソフトや社内プロキシに遮断された可能性があります。\n\n"
            "詳細: {}".format(label, e.reason))
    except OSError as e:
        _remove(tmp)
        raise SetupError("{} の保存に失敗しました。\n\n詳細: {}".format(label, e))

    try:
        os.replace(tmp, dest)
    except OSError as e:
        _remove(tmp)
        raise SetupError(
            "{} を置き換えられませんでした。\n"
            "ツールを二重に起動していないか確認してください。\n\n詳細: {}".format(label, e))


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def ensure_ytdlp(on_progress=None):
    """yt-dlp.exe が無ければ取得する。"""
    if _usable(ytdlp_path()):
        return ytdlp_path()
    _download(YTDLP_URL, ytdlp_path(), on_progress, "yt-dlp")
    return ytdlp_path()


def ensure_ffmpeg(on_progress=None):
    """ffmpeg.exe / ffprobe.exe が無ければ ZIP を取得して 2 つだけ取り出す。"""
    if ffmpeg_ok():
        return ffmpeg_path()

    tmpdir = tempfile.mkdtemp(prefix="johukku-ffmpeg-")
    zip_path = os.path.join(tmpdir, "ffmpeg.zip")
    try:
        _download(FFMPEG_URL, zip_path, on_progress, "FFmpeg")
        if on_progress:
            on_progress(-1, -1, "FFmpeg を展開しています")
        _extract_ffmpeg(zip_path)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    return ffmpeg_path()


def _extract_ffmpeg(zip_path):
    """ZIP の bin/ の中身（exe と DLL）を平らに置く。

    shared 版なので exe だけ取り出しても動かない。DLL も同じ場所に要る。
    """
    target = bin_dir()
    try:
        with zipfile.ZipFile(zip_path) as z:
            members = []
            for name in z.namelist():
                parts = name.replace("\\", "/").split("/")
                if len(parts) >= 2 and parts[-2] == "bin" and parts[-1]:
                    members.append((name, parts[-1]))
            names = {base for _, base in members}
            if not {"ffmpeg.exe", "ffprobe.exe"} <= names:
                raise SetupError(
                    "FFmpeg の ZIP に想定したファイルが入っていませんでした。\n"
                    "配布元の構成が変わった可能性があります。")
            for member, base in members:
                dest = os.path.join(target, base)
                tmp = dest + ".part"
                with z.open(member) as src, open(tmp, "wb") as dst:
                    shutil.copyfileobj(src, dst, 1024 * 1024)
                os.replace(tmp, dest)
    except zipfile.BadZipFile:
        raise SetupError(
            "FFmpeg のダウンロードが途中で壊れていました。\n"
            "もう一度お試しください。")
    except OSError as e:
        raise SetupError("FFmpeg の展開に失敗しました。\n\n詳細: {}".format(e))


def ensure_all(on_progress=None, need_ffmpeg=True):
    """足りない部品をまとめて取得する。"""
    ensure_ytdlp(on_progress)
    if need_ffmpeg:
        ensure_ffmpeg(on_progress)


# ---------------------------------------------------------------- 実行・更新


def run_hidden(args, timeout=None):
    """コンソール窓を出さずに実行し、(終了コード, 出力) を返す。"""
    try:
        p = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            creationflags=NO_WINDOW,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as e:
        return -1, str(e)
    out = (p.stdout or b"").decode("utf-8", "replace").strip()
    return p.returncode, out


def ytdlp_version():
    """yt-dlp のバージョン文字列。取れなければ None。"""
    if not _usable(ytdlp_path()):
        return None
    code, out = run_hidden([ytdlp_path(), "--version"], timeout=30)
    if code == 0 and out:
        return out.splitlines()[-1].strip()
    return None


def update_ytdlp():
    """yt-dlp を自己更新する。戻り値は (成功したか, 表示するメッセージ)。"""
    if not _usable(ytdlp_path()):
        return False, "yt-dlp がまだ入っていません。"
    code, out = run_hidden([ytdlp_path(), "-U"], timeout=300)
    return code == 0, out or "（出力なし）"


if __name__ == "__main__":
    # 動作確認用: python binaries.py で取得まで一通り試せる
    def _show(done, total, label):
        if done < 0:
            print("\n  {}".format(label))
        elif total > 0:
            print("\r  {} {:5.1f}%".format(label, done * 100.0 / total), end="")
        else:
            print("\r  {} {:,} バイト".format(label, done), end="")

    print("置き場所: {}".format(bin_dir()))
    lack = missing()
    print("不足: {}".format("、".join(lack) if lack else "なし"))
    try:
        ensure_all(_show)
    except SetupError as e:
        print("\n失敗: {}".format(e))
        sys.exit(1)
    print("\nyt-dlp {}".format(ytdlp_version()))
    print("FFmpeg {}".format("あり" if ffmpeg_ok() else "なし"))
