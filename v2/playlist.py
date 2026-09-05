# -*- coding: utf-8 -*-
"""放送用のM3Uプレイリストを書く（2026-09-05）。

★なぜM3Uなのか（`infra/RADIODJ_SIYOU.md` に調査の全文）
  RadioDJには **Load M3U Playlist by Date Mask** というイベント動作がある。
  日付マスク（.NETの書式）に一致する .m3u を指定フォルダから読み込む。
  これを使うと**順番も曲も、こちら（生成側）が完全に決められる**。
  - こずえが「それでは聴いてください——『南の星』」と読み上げた曲を、
    M3Uにそのパスで書けば**必ずその曲が流れる**
  - トーク→曲→ジングル→トークの並びも**M3Uの行順で決まる**
  → 「7本のブロックをRadioDJでどう並べるか」という問題が消える。

★★事故の時の挙動（nordw方針「番組が流れず、しれっと曲が流れ続けるのがベター」）
  RadioDJのイベントで **Open Positions を `Top`（先頭に差し込む）** にすること。
  - M3Uが読めた → 番組が先頭に入って流れる
  - M3Uが無い   → **何も差し込まれず、Auto DJが積んだ曲がそのまま流れ続ける**
  ⚠ `Replace`（キューを全部入れ替える）を選ぶと、失敗時に無音になるリスクがある。
  ★CEBDR24は既に24時間曲が流れている＝**Auto DJはもう動いている**。
    「曲が流れ続ける」は新しく作るものではなく、**既にある基底状態**。

★放送は6時・7時・8時の3回。**回ごとにM3Uを分ける**。
  マスクは `yyyy-MM-dd.HH` → `2026-09-05.06.m3u` / `...07.m3u` / `...08.m3u`
  パスA が3本まとめて書き、パスB が各回のおたよりを差し替えたら**その回だけ書き直す**。

⚠ 未確認（`infra/RADIODJ_SIYOU.md` 参照）:
  - M3Uに書くパスの形式（絶対パスで書いている。VPS上で通るかは実機確認）
  - ★**VPS上の曲フォルダのパス**。下の SONG_DIR は曲リストのCSVのPath列から取ったもので、
    **VPS上も同じ場所かはプロハンさんに未確認**
  - M3U経由でも再生ログ・権利処理が正しく残るか
"""
import datetime
from pathlib import Path

# ★VPS上の置き場所。**ここはプロハンさんに確認が要る。**
#   曲リスト(cyberenkaplaylist0904.csv)の Path 列が C:\CYBER_ENKA_STREAM\tracks_norm\ だった。
SONG_DIR = r"C:\CYBER_ENKA_STREAM\tracks_norm"
# こずえの音声とジングルの置き場所（rcloneが降ろす先。VPS_SETUP.md と合わせる）
VOICE_ROOT = r"C:\kozue_asa"

BROADCAST_HOURS = (6, 7, 8)     # 2026-09-05 確定。めざましテレビ方式で同じ回を3回

# 放送順。★make_through.py と同じ並びにすること（片方だけ変えると食い違う）
#   ("seg", ブロック名) … こずえの音声
#   ("song1"/"song2")   … その日の曲
#   ("jingle")          … 曲明けのジングル（★曲の前には入れない。2026-09-04 プロハン指示）
ORDER = [
    ("seg", "talk1"),
    ("song1",), ("jingle",),
    ("seg", "traffic"),
    ("seg", "sa"),
    ("seg", "news"),
    ("mail",),                   # パスBの本番版があればそれ、無ければ mail_fb
    ("song2",), ("jingle",),
    ("seg", "uranai"),
    ("seg", "ending"),
]


def _seg_path(outdir: Path, name: str) -> str | None:
    """seg_XX_<name>.(wav|mp3) を名前で探す。

    ★番号で決め打ちしない。番号はコーナーが増えるたびにずれる。
      make_through.py で同じ理由の事故を2回やっている（SAを足した時とニュースを足した時）。
    ⚠★拡張子でも決め打ちしない。**本番(runner/build.py)は .wav、ローカル(generate_v2.py)は
      .mp3** を作る。最初 .wav 決め打ちで書いて、ローカル検証が全滅した（2026-09-05）。
    """
    for ext in ("wav", "mp3"):
        hits = sorted(outdir.glob(f"seg_*_{name}.{ext}"))
        if hits:
            return str(hits[0])
    return None


def build(day: datetime.date, outdir: Path, pack: dict, log,
          hours=BROADCAST_HOURS, voice_root: str = VOICE_ROOT,
          song_dir: str = SONG_DIR) -> list[Path]:
    """その日のM3Uを放送回ごとに書く。書けたファイルの一覧を返す。

    ★VPS上のパスで書く。ここ(Actions)のパスではない。
      ローカルの outdir は `out/2026-09-05/` だが、VPSでは
      `C:\\kozue_asa\\2026-09-05\\` に降りてくる。
    """
    pl_dir = outdir.parent / "playlists"
    pl_dir.mkdir(parents=True, exist_ok=True)

    def vps(p: str) -> str:
        """ここのパスを、VPS上のパスに読み替える。"""
        return str(Path(voice_root) / day.isoformat() / Path(p).name)

    made = []
    for h in hours:
        lines = ["#EXTM3U"]
        missing = []
        for item in ORDER:
            kind = item[0]
            if kind == "seg":
                p = _seg_path(outdir, item[1])
                if not p:
                    missing.append(item[1])
                    continue
                lines.append(vps(p))
            elif kind == "mail":
                # ★パスBの本番版があればそれを使う。無ければパスAのフォールバック版
                p = _seg_path(outdir, "mail") or _seg_path(outdir, "mail_fb")
                if not p:
                    missing.append("mail")
                    continue
                lines.append(vps(p))
            elif kind == "jingle":
                # ★日付フォルダの中に置く。out/直下に1回だけ置くと
                #   `r2.py prune --days 7` が更新日で消してしまう（毎日納品すれば消えない）。
                lines.append(str(Path(voice_root) / day.isoformat() / "jingle1.wav"))
            else:   # song1 / song2
                s = pack.get(kind) or {}
                f = s.get("file")
                if not f:
                    missing.append(kind)
                    continue
                lines.append(str(Path(song_dir) / f))

        if missing:
            # ★欠けたまま書かない。**欠けた番組は流さない**（build.py と同じ方針）。
            #   M3Uが無ければ RadioDJ は何も差し込まず、曲が流れ続ける。
            log.error("★%d時のM3Uは書かない。欠けている: %s", h, missing)
            continue

        # マスク `yyyy-MM-dd.HH` に合わせる
        p = pl_dir / f"{day.isoformat()}.{h:02d}.m3u"
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        made.append(p)
        log.info("M3U %s (%d行)", p.name, len(lines) - 1)
    return made


def _test(log) -> None:
    """壊れたら落ちる最小のチェック。`python v2/playlist.py --test`

    ★ここが静かに間違うと**放送の並び順が壊れる**が、聴くまで気づけない。
      本番を15分回す前に、ここで潰せるものは潰す。
    """
    import shutil, tempfile
    day = datetime.date(2026, 9, 5)
    pack = {"song1": {"file": "A.mp3"}, "song2": {"file": "B.mp3"}}
    tmp = Path(tempfile.mkdtemp()) / day.isoformat()
    tmp.mkdir(parents=True)
    try:
        # 本番と同じ 7ブロック・.wav（★ローカルは.mp3。両方拾えないと片方で全滅する）
        for i, n in enumerate(
                ["talk1", "traffic", "sa", "news", "mail_fb", "uranai", "ending"], 1):
            (tmp / f"seg_{i:02d}_{n}.wav").write_bytes(b"x")

        made = build(day, tmp, pack, log)
        assert len(made) == 3, f"パスAは6/7/8の3本書くはず: {made}"
        t = made[0].read_text(encoding="utf-8")
        assert "seg_05_mail_fb.wav" in t, "パスAはフォールバック版おたよりを指すはず"
        assert t.count("jingle1.wav") == 2, "ジングルは曲明けの2回"
        assert t.index("A.mp3") < t.index("B.mp3"), "曲の順番が入れ替わっている"

        # パスB: 本番版おたよりが来たら差し替わり、その回だけ書き直す
        (tmp / "seg_01_mail.wav").write_bytes(b"x")
        b = build(day, tmp, pack, log, hours=[7])
        assert len(b) == 1 and b[0].name.endswith(".07.m3u"), b
        tb = b[0].read_text(encoding="utf-8")
        assert "seg_01_mail.wav" in tb and "mail_fb" not in tb, "おたよりが差し替わっていない"

        # 欠けたら書かない（＝配らない＝曲が流れ続ける）
        (tmp / "seg_04_news.wav").unlink()
        assert build(day, tmp, pack, log, hours=[6]) == [], "欠けているのに書いてしまった"
    finally:
        shutil.rmtree(tmp.parent, ignore_errors=True)
    print("★playlist: 全部通った")


if __name__ == "__main__":
    import json, logging, sys
    sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if "--test" in sys.argv:
        _test(logging.getLogger("m3u"))
        raise SystemExit(0)
    base = Path(__file__).resolve().parent.parent
    d = datetime.date.fromisoformat(sys.argv[1] if len(sys.argv) > 1 else "2026-09-05")
    outdir = base / "episodes_v2" / d.isoformat()
    pack = json.loads((outdir / "datapack.json").read_text(encoding="utf-8"))
    made = build(d, outdir, pack, logging.getLogger("m3u"))
    for p in made:
        print(f"\n=== {p.name} ===")
        print(p.read_text(encoding="utf-8"))
        break
