# -*- coding: utf-8 -*-
"""yt-dlp を起動して、進捗を 1 行ずつ読み取る。

GUI とは切り離してあるので、この単体でも動かせる:

    python ytdlp_runner.py <URL>
"""

from __future__ import annotations

import os
import subprocess
import sys

import binaries

# (画面の表示名, 内部の名前)。GUI のラジオボタンはこの並び順で作る
MODES = [
    ("動画（最高画質）", "video_best"),
    ("動画（1080p まで）", "video_1080"),
    ("動画（720p まで・軽い）", "video_720"),
    ("音声のみ（mp3）", "audio_mp3"),
    ("音声のみ（wav・無圧縮）", "audio_wav"),
]

MODE_NAMES = {key: label for label, key in MODES}

# 進捗行の目印。yt-dlp の通常の出力と混ざらないようにする
PROG = "@@PROG@@"

# 完成したファイルのパスが出てくる行
_DEST_MARKS = (
    "[download] Destination: ",
    "[ExtractAudio] Destination: ",
    "[FixupM3u8] Destination: ",
)

# よくある失敗と、利用者に見せる言い換え
HINTS = (
    (("sign in to confirm", "not a bot", "cookies"),
     "サイト側から自動アクセスとみなされました。\n"
     "しばらく時間をおくか、別の動画で試してください。"),
    (("unable to extract", "nsig", "player response", "unable to download api page"),
     "サイトの仕様変更に yt-dlp が追いついていない可能性があります。\n"
     "画面右下の「yt-dlp を更新」を押してから、もう一度お試しください。"),
    (("http error 403", "http error 429"),
     "アクセスが一時的に拒否されました。\n"
     "少し時間をおいてから、もう一度お試しください。"),
    (("private video", "video unavailable", "has been removed", "members-only",
      "age-restricted", "login required"),
     "その動画は公開されていないか、視聴に制限がかかっています。"),
    (("unsupported url", "is not a valid url"),
     "この URL には対応していません。\n"
     "動画ページの URL をそのまま貼り付けているか確認してください。"),
    (("ffmpeg", "postprocessing"),
     "変換処理でつまずきました。\n"
     "「部品を入れ直す」で FFmpeg を取得し直すと直ることがあります。"),
    (("winerror 5", "permission denied", "access is denied", "no space"),
     "保存先に書き込めませんでした。\n"
     "保存先フォルダを変えるか、空き容量を確認してください。"),
)


def hint_for(text):
    """エラー本文から、それらしい説明を 1 つ選ぶ。無ければ None。"""
    low = text.lower()
    for keys, message in HINTS:
        if any(k in low for k in keys):
            return message
    return None


def build_args(url, mode, outdir, playlist=False, subs=False, mp4_only=False):
    """yt-dlp に渡す引数を組み立てる。

    mp4_only は「画質より互換性」の切り替え。
    yt-dlp 公式の -t mp4 プリセットと同じ内容を明示的に並べている。
    """
    args = [
        binaries.ytdlp_path(),

        # 利用者の環境に置かれた設定ファイルに引っ張られないようにする
        "--ignore-config",
        "--no-colors",
        "--newline",
        "--progress-template",
        "download:" + PROG + "%(progress._percent_str)s"
        "|%(progress._speed_str)s|%(progress._eta_str)s",

        "--ffmpeg-location", binaries.bin_dir(),
        "--paths", outdir,
        "-o", "%(title)s.%(ext)s",

        # 日本語の長いタイトルでもパスが破綻しないように
        "--windows-filenames",
        "--trim-filenames", "120",

        "--concurrent-fragments", "4",
        "--retries", "10",
    ]

    args.append("--yes-playlist" if playlist else "--no-playlist")

    if mode.startswith("video"):
        args += ["-f", "bv*+ba/b"]
        # 「res」は解像度の上限。指定しなければサイトが持っている最高のものを選ぶ
        res = {"video_1080": "res:1080", "video_720": "res:720"}.get(mode, "res")

        if mp4_only:
            # 確実に mp4 で受け取りたいとき。h264 と aac を優先するので、
            # サイトによっては解像度が 1 段下がることがある。
            args += ["--merge-output-format", "mp4", "--remux-video", "mp4",
                     "-S", "vcodec:h264,lang,quality,{},fps,hdr:12,acodec:aac".format(res)]
        else:
            # 画質を最優先する。ここで ext を指定すると解像度より
            # 「mp4 であること」が優先されてしまうので、指定してはいけない。
            args += ["--merge-output-format", "mp4/mkv"]
            if res != "res":
                args += ["-S", res]

        if subs:
            args += ["--write-subs", "--write-auto-subs",
                     "--sub-langs", "ja.*,en.*", "--convert-subs", "srt"]
    elif mode == "audio_mp3":
        args += ["-f", "ba/b", "-x", "--audio-format", "mp3",
                 "--audio-quality", "0", "--embed-metadata"]
    elif mode == "audio_wav":
        args += ["-f", "ba/b", "-x", "--audio-format", "wav"]
    else:
        raise ValueError("未知のモード: {}".format(mode))

    args.append(url)
    return args


def parse_progress(line):
    """進捗行を (割合, 速度, 残り) に分解する。割合が取れなければ None。"""
    body = line[len(PROG):]
    parts = body.split("|")
    while len(parts) < 3:
        parts.append("")
    percent = None
    try:
        percent = float(parts[0].strip().rstrip("%"))
    except ValueError:
        percent = None
    return percent, parts[1].strip(), parts[2].strip()


def _destination_of(line):
    """完成したファイルのパスを含む行なら、そのパスを返す。"""
    for mark in _DEST_MARKS:
        if line.startswith(mark):
            return line[len(mark):].strip()
    if line.startswith("[Merger] Merging formats into "):
        return line.split("into", 1)[1].strip().strip('"')
    if line.startswith("[download] ") and " has already been downloaded" in line:
        return line[len("[download] "):].split(" has already been")[0].strip()
    return None


class Job:
    """1 本の URL のダウンロード。中止できるように process を持つ。"""

    def __init__(self, url, mode, outdir, playlist=False, subs=False, mp4_only=False):
        self.url = url
        self.mode = mode
        self.outdir = outdir
        self.playlist = playlist
        self.subs = subs
        self.mp4_only = mp4_only
        self.proc = None
        self.cancelled = False
        self.files = []          # 出来上がったファイル
        self.error_lines = []    # 失敗時に見せる行

    def cancel(self):
        """実行中なら止める。終了は run() 側で拾う。"""
        self.cancelled = True
        p = self.proc
        if p is None or p.poll() is not None:
            return
        try:
            p.terminate()
        except OSError:
            pass

    def run(self, on_log=None, on_progress=None):
        """最後まで走らせて終了コードを返す。中止したときは None。"""
        args = build_args(self.url, self.mode, self.outdir,
                          self.playlist, self.subs, self.mp4_only)
        try:
            self.proc = subprocess.Popen(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                creationflags=binaries.NO_WINDOW,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except OSError as e:
            if on_log:
                on_log("yt-dlp を起動できませんでした: {}".format(e))
            self.error_lines.append(str(e))
            return -1

        for raw in self.proc.stdout:
            line = raw.rstrip("\r\n")
            if not line:
                continue
            if line.startswith(PROG):
                if on_progress:
                    on_progress(*parse_progress(line))
                continue

            dest = _destination_of(line)
            if dest and dest not in self.files:
                self.files.append(dest)

            low = line.lower()
            if low.startswith("error") or "error:" in low or low.startswith("warning"):
                self.error_lines.append(line)
            if on_log:
                on_log(line)

        code = self.proc.wait()
        if self.cancelled:
            return None
        return code

    def produced(self):
        """実際に残ったファイルだけを返す。

        音声だけ抜き出したときは、元の動画が yt-dlp に消される。
        その分を除かないと「保存: 〜.mp4」と嘘の案内をしてしまう。
        """
        return [p for p in self.files if os.path.isfile(p)]

    def error_text(self):
        return "\n".join(self.error_lines[-6:])


def main(argv):
    """開発用の簡易実行。"""
    if len(argv) < 2:
        print("使い方: python ytdlp_runner.py <URL> [保存先]")
        return 2
    outdir = argv[2] if len(argv) > 2 else os.getcwd()
    if binaries.missing():
        print("部品を取得します...")
        binaries.ensure_all(lambda d, t, n: None)
    job = Job(argv[1], "video_best", outdir)
    code = job.run(on_log=print,
                   on_progress=lambda p, s, e: print("  {} {} {}".format(p, s, e)))
    print("終了コード: {}".format(code))
    for f in job.produced():
        print("出力: {}".format(f))
    return code or 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
