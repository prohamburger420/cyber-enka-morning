# -*- coding: utf-8 -*-
r"""放送回を1本のmp3にまとめる（2026-09-09 nordw依頼「アーカイブ目的」）。

★入力は2通り。**同じ関数で作る**（片方だけ直る事故を避ける）:
  (a) その日のブロック群（Actions/ローカル）… こずえのトークだけ。曲とジングルは
      **この機械に無い**（曲は C:\CYBER_ENKA_STREAM\tracks_norm＝VPS上）
  (b) 放送用M3U（VPS側）… 曲もジングルも入った**完全版**。VPSにffmpegがあれば作れる

★ID3を必ず入れる（アー写つき）。アーカイブは後から人が触るものなので、
  ファイル単体で「何の何日の回か」が分かる状態にしておく。

⚠ おたよりは回ごとに違う。パスBが `seg_01_mail_*.mp3` を**同じ名前で上書きする**ので、
  日付フォルダに残るのは**最後に作られた回（8時）のもの**だけ。
  回ごとのアーカイブが要るなら、パスBの直後に作ること（build.py がそうしている）。
"""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import playlist  # noqa: E402

# アーカイブに入れる並び。放送の ORDER から**この機械に無いもの**を落とした形。
# ★ORDERを写経しない。playlist.ORDER から機械的に作る（並びを変えたとき片方だけ直らない）
def _talk_order() -> list[str]:
    out = []
    for item in playlist.ORDER:
        if item[0] == "seg":
            out.append(item[1])
        elif item[0] == "mail":
            out.append("mail")      # 本番版が無ければ mail_fb に落ちる
    return out


def _concat(inputs: list[str], out_path: Path, log) -> None:
    """ffmpegのconcat demuxerで繋ぐ。★再エンコードする（mp3の繋ぎ目のギャップを避ける）。"""
    lst = out_path.with_suffix(".txt")
    lst.write_text(
        "".join("file '{}'\n".format(str(p).replace("'", r"'\''")) for p in inputs),
        encoding="utf-8")
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
           "-i", str(lst), "-c:a", "libmp3lame", "-q:a", "3", str(out_path)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    lst.unlink(missing_ok=True)
    if r.returncode != 0:
        raise RuntimeError(f"アーカイブの結合に失敗: {r.stderr[-400:]}")


def _tag(src: Path, dst: Path, day, hour, cover: Path | None) -> None:
    """曲情報を埋める。★アー写があれば入れる（本番のブロックと同じ扱い）。"""
    title = f"サイバー演歌モーニング {day.isoformat()}"
    if hour is not None:
        title += f" {hour}時の回"
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src)]
    if cover and cover.exists():
        cmd += ["-i", str(cover), "-map", "0:a", "-map", "1:0", "-c:v", "copy",
                "-disposition:v", "attached_pic",
                "-metadata:s:v", "title=Album cover",
                "-metadata:s:v", "comment=Cover (front)"]
    else:
        cmd += ["-map", "0:a"]
    cmd += ["-c:a", "copy", "-id3v2_version", "3",
            "-metadata", f"title={title}",
            "-metadata", "artist=雷音こずえ",
            "-metadata", "album=雷音こずえのサイバー演歌モーニング",
            "-metadata", f"date={day.isoformat()}",
            str(dst)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"アーカイブのタグ付けに失敗: {r.stderr[-400:]}")


def from_segments(day, outdir: Path, dest_dir: Path, log,
                  hour: int | None = None, cover: Path | None = None) -> Path | None:
    """(a) その日のブロックから作る。曲とジングルは入らない（この機械に無い）。

    返り値は作ったファイル。1本も無ければ None（アーカイブが無くても番組は止めない）。
    """
    files, missing = [], []
    for name in _talk_order():
        p = (playlist._seg_path(outdir, name)
             or (playlist._seg_path(outdir, "mail_fb") if name == "mail" else None))
        (files if p else missing).append(p or name)
    if not files:
        log("★アーカイブ: ブロックが1本も無いので作らない")
        return None
    if missing:
        log(f"★アーカイブ: 欠けたブロックは飛ばす: {missing}")
    dest_dir.mkdir(parents=True, exist_ok=True)
    stem = day.isoformat() + (f".{hour:02d}" if hour is not None else "")
    tmp = dest_dir / f"_tmp_{stem}.mp3"
    out = dest_dir / f"kozue_asa_{stem}.mp3"
    try:
        _concat(files, tmp, log)
        _tag(tmp, out, day, hour, cover)
    finally:
        tmp.unlink(missing_ok=True)
    import os
    log(f"アーカイブ {out.name} ({os.path.getsize(out)/1048576:.1f}MB / {len(files)}ブロック)")
    return out


def from_m3u(day, m3us: list[Path], dest_dir: Path, log,
             hour: int | None = None, cover: Path | None = None) -> Path | None:
    """(b) 放送用M3Uから作る。**曲もジングルも入った完全版**（VPS側で使う）。

    ★M3Uは前半・後半の2本を順に渡すこと。行の順＝放送の順なので、
      並びをここで組み直さない（M3Uが正本）。
    """
    files = []
    for m in m3us:
        if not Path(m).exists():
            log(f"★アーカイブ: M3Uが無い: {m}")
            continue
        for ln in Path(m).read_text(encoding="utf-8-sig").splitlines():
            ln = ln.strip()
            if ln and not ln.startswith("#"):
                if Path(ln).exists():
                    files.append(ln)
                else:
                    log(f"★アーカイブ: ファイルが無いので飛ばす: {ln}")
    if not files:
        return None
    dest_dir.mkdir(parents=True, exist_ok=True)
    stem = day.isoformat() + (f".{hour:02d}" if hour is not None else "")
    tmp = dest_dir / f"_tmp_{stem}.mp3"
    out = dest_dir / f"kozue_asa_full_{stem}.mp3"
    try:
        _concat(files, tmp, log)
        _tag(tmp, out, day, hour, cover)
    finally:
        tmp.unlink(missing_ok=True)
    log(f"アーカイブ(完全版) {out.name} / {len(files)}本")
    return out
