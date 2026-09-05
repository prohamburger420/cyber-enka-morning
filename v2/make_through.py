# -*- coding: utf-8 -*-
"""トーク＋曲を通しで聴けるデモ1本に組む（2026-09-04 nordw「曲も差し込んで通しで聴きたい」）。

放送順: talk1 → 曲1 → traffic → sa → news → mail → 曲2 → uranai → ending
- 曲はデモ用に EXCERPT 秒だけ切り出し（フル尺だと確認に時間がかかりすぎるため）
- トークと曲のラウドネスを loudnorm で揃える（生のままだと曲だけ爆音になる）
- 曲にはフェードイン/アウトを付ける
"""
import json
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
EP = BASE / "episodes_v2"
REFS = BASE / "voice" / "refs"
# ★曲はフル尺で通す（2026-09-04 nordw「本番さながらで通して番組感を感じたい」）。
#   抜粋にすると尺の実感も曲明けの余韻も分からないので、確認の目的を外す。
FULL_SONG = True
TMP = Path(sys.argv[2] if len(sys.argv) > 2 else "C:/Users/nordw/AppData/Local/Temp/kozue_mix")


def run(args):
    r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg失敗: {' '.join(map(str,args))}\n{r.stderr[-500:]}")


# ★ラウドネスは2パスで当てる（2026-09-04 実測で判明）
#   1パスのloudnormは目標に届かない。実測: 同じ I=-16 を指定したのに
#   トーク -16.2 LUFS に対し **曲は -18.6 LUFS**（2.5 LU も小さい）。
#   音楽は声よりピークが尖るため、真のピーク制限(TP)に先に当たって全体が押し下げられる。
#   → 先に測ってから当てる2パス方式にする。
#   さらに曲は声より 1 LU 上に置く（nordw「トークに比べて曲が小さい」）。
# ★トークは曲より7dB下（2026-09-04 nordw耳判定で -16→-20→-22 の二段階）
TALK_I, SONG_I = -22.0, -15.0
# ★ジングルは曲と別レベル（2026-09-04 nordw「ジングルがでかい。4dBさげてみよ」）
#   当初は曲と同じ-15にしていたが、ジングルは短く密度が高いぶん実際より大きく聞こえる。
JINGLE_I = -19.0
TP, LRA = -1.5, 11.0


def _measure(src: Path, target_i: float) -> dict:
    r = subprocess.run(
        ["ffmpeg", "-i", str(src), "-af",
         f"loudnorm=I={target_i}:TP={TP}:LRA={LRA}:print_format=json", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    s = r.stderr
    return json.loads(s[s.rindex("{"):s.rindex("}") + 1])


def _norm(src: Path, dst: Path, target_i: float):
    m = _measure(src, target_i)
    af = (f"loudnorm=I={target_i}:TP={TP}:LRA={LRA}"
          f":measured_I={m['input_i']}:measured_TP={m['input_tp']}"
          f":measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}"
          f":offset={m['target_offset']}:linear=true:print_format=summary")
    # ★出力はステレオ（2026-09-04 実測でバグ判明）
    #   -ac 1 だと ffmpeg は loudnorm の**後に**ステレオ→モノ合成するため、
    #   左右の位相差ぶん音圧が落ちる。実測で曲だけ -3.4 dB 損していた
    #   （元音源は -10.9/-7.9 LUFS と十分大きいのに出力が -18.4 になっていた）。
    #   トークは元からモノなので無損失＝「曲だけ小さい」の正体。
    #   本番のRadioDJもステレオで鳴らすので、ステレオのまま通すのが実態にも近い。
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
         "-af", af, "-ar", "44100", "-ac", "2", str(dst)])


def norm_talk(src: Path, dst: Path, seg_name: str | None = None):
    """トーク。seg_name があればコーナー別BGMを敷く（bgm.py が最後に-16へ揃える）。"""
    if seg_name:
        import bgm as _bgm
        _bgm.mix(src, seg_name, dst)
    else:
        _norm(src, dst, TALK_I)


def norm_song(src: Path, dst: Path):
    """曲はフル尺。トークより1LU上に置いて、番組内で埋もれないようにする"""
    _norm(src, dst, SONG_I)


def norm_jingle(src: Path, dst: Path):
    """ジングル: 曲より控えめに（短く密度が高いので、同じ数値だと大きく聞こえる）"""
    _norm(src, dst, JINGLE_I)


def main(date: str):
    d = EP / date
    TMP.mkdir(parents=True, exist_ok=True)
    pack = json.loads((d / "datapack.json").read_text(encoding="utf-8"))

    songs = sorted(REFS.glob("*.webm"))
    def find_song(title: str):
        """★先頭数文字での部分一致は危険（2026-09-04 実際に誤マッチ）:
        「サイバー演歌伝説」の先頭6字が「こずえのサイバー演歌道」にヒットし、
        別の曲を『現物』として使ってしまった。
        → 最長共通部分列の長さでスコアし、曲名の8割以上一致した時だけ現物とみなす。"""
        import difflib
        t = title.replace("　", "").replace(" ", "")
        best, best_score = None, 0.0
        for s in songs:
            n = s.name.replace(" ", "")
            m = difflib.SequenceMatcher(None, t, n).find_longest_match(0, len(t), 0, len(n))
            score = m.size / len(t)
            if score > best_score:
                best, best_score = s, score
        return (best, True) if best_score >= 0.8 else (None, False)

    # ★pack["song1"] は 2026-09-05 から {title, artist, sec, file} の辞書。
    #   同名別歌手の曲が39件あるため、曲名だけでは曲を一意に指せない。
    def title_of(v):
        return v["title"] if isinstance(v, dict) else v
    s1, ok1 = find_song(title_of(pack["song1"]))
    s2, ok2 = find_song(title_of(pack["song2"]))
    # 現物が無い曲は代役を立てる（デモは流れの確認が目的）
    pool = [s for s in songs if s not in (s1, s2)]
    if s1 is None:
        s1 = pool.pop(0)
    if s2 is None:
        s2 = pool.pop(0)
    print(f"曲1 {title_of(pack['song1'])}: {'現物' if ok1 else '★代役 ' + s1.name[:30]}")
    print(f"曲2 {title_of(pack['song2'])}: {'現物' if ok2 else '★代役 ' + s2.name[:30]}")

    # ★ジングルは**曲明けの1回だけ**（2026-09-04 プロハン指示。当初は曲の前後2回だった）
    #   曲紹介「それでは聴いてください——」の直後にジングルが挟まると流れが切れる。
    #   曲→トークの戻りにだけ置く。
    jingle = BASE / "jingle1.wav"
    if not jingle.exists():
        raise SystemExit(f"ジングルが無い: {jingle}")

    # ★★セグメントは**番号ではなく名前で拾う**（2026-09-05）。
    #   以前は "seg_04_mail_fb.mp3" のように番号を直書きしていた。
    #   SAコーナーを足した時に番号が1つずれて壊れ、手で振り直して直した。
    #   **ニュースコーナーを足したら、まったく同じ壊れ方が2回目**（今度は
    #   ニュースが抜け、占いとエンディングが1つずつずれる）。
    #   コーナーは今後も増えるので、番号への依存をやめる。
    def seg(name: str) -> Path | None:
        hits = sorted(d.glob(f"seg_*_{name}.mp3"))
        return hits[0] if hits else None

    # ★おたよりは本番版(パスB)があればそれ、無ければフォールバック版
    mail_seg = "mail" if seg("mail") else "mail_fb"
    print(f"おたより: {'本番版' if mail_seg == 'mail' else 'フォールバック版(6時の回)'}")

    parts = []
    # (セグメント名 or Path, 種別, BGM用セグメント名)
    order = [("talk1", "talk", "talk1"),
             (s1, "song", None), (jingle, "jingle", None),
             ("traffic", "talk", "traffic"),
             ("sa", "talk", "sa"),
             ("news", "talk", "news"),
             (mail_seg, "talk", mail_seg),
             (s2, "song", None), (jingle, "jingle", None),
             ("uranai", "talk", "uranai"),
             ("ending", "talk", "ending")]
    for i, (item, kind, segname) in enumerate(order, 1):
        out = TMP / f"p{i:02d}.wav"
        src = item if isinstance(item, Path) else seg(item)
        # ★見つからないコーナーは**飛ばして通す**。番組は落とさない。
        #   ただし黙って飛ばさない（気づけないと、抜けたまま納品してしまう）。
        if src is None or not src.exists():
            print(f"  ⚠ {item} が無いので飛ばす")
            continue
        if kind == "talk":
            norm_talk(src, out, segname)      # コーナー別BGMを敷く
        elif kind == "song":
            norm_song(src, out)
        else:
            norm_jingle(src, out)             # ジングルは曲と同じ高さ
        parts.append(out)
        print(f"  {i:2d}. {kind:6s} {(segname or ''):8s} {src.name[:40]}")

    lst = TMP / "concat.txt"
    lst.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts), encoding="utf-8")
    final = d / f"TOOSHI_{date}.mp3"
    run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
         "-i", str(lst), "-codec:a", "libmp3lame", "-q:a", "3", str(final)])
    print("完成:", final)


if __name__ == "__main__":
    # ★★標準出力を utf-8 に固定（2026-09-05 実際に落ちた・今日3回目の同じ事故）。
    #   曲のファイル名に「⧸」(U+29F8 BIG SOLIDUS。ファイル名に / が使えないので代用される)
    #   が入っていて、cp932 で print できずに通し作成ごと落ちた。
    #   ⚠ 文字を消す直し方をしない。cp932 に無い文字は他にいくらでもある。
    #     曲名・歌手名・台本には記号や絵文字が普通に入ってくる。**出力側を直す。**
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    main(sys.argv[1] if len(sys.argv) > 1 else "2026-09-04")
