# -*- coding: utf-8 -*-
"""コーナー別BGM＋ダッキング（2026-09-04 nordw提供のBGM素材に対応）。

ダッキング＝こずえが喋っている間だけBGMが自動で下がり、間で持ち上がる。
ffmpeg の sidechaincompress で行う（声を鍵にしてBGMを圧縮する）。

★BGMは声より十分下（BGM_I）に置いたうえで、さらにダッキングで沈める。
  正規化だけだと喋りに被って言葉が潰れる。
"""
import json
import subprocess
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent


def find(name: str) -> Path:
    """BGM素材の実体を探す。

    ★★2026-09-05 発覚: **本番の音声にBGMが付いていなかった。**
      BGMを敷くのは `make_through.py` だけで、それは通し(TOOSHI)を作るための
      ローカル専用スクリプト。**本番ワークフローは一度も呼んでいない**
      （morning.yml にも runner/build.py にも `make_through` が無い）。
      ＝ nordwが耳で決めた「OPは5秒後から」「イントロ明け-2dB」「ニュース専用BGM」は
        **全部ローカルの通しにしか効いていなかった**。R2に納品されていたのは裸の声。
      ⚠ REF_BY_CORNER（ニュースのテンション）と**まったく同じ形の事故**。
        「ローカルで直した」と「本番で直った」は別。★毎回この2つを分けて確認する。

    素材はキャラ資産なので**公開リポジトリに置けない**。R2の `assets/bgm/` から配る。
    ローカル(kozue-asa直下)にも同じものがあるので、両方を見る。
    """
    for p in (BASE / "assets" / "bgm" / name, BASE / name):
        if p.exists():
            return p
    raise FileNotFoundError(f"BGMが無い: {name}（assets/bgm/ と {BASE} を探した）")

# セグメント名 → BGMファイル
BGM_MAP = {
    "talk1":   "op2(5秒くらいから話始めるイメージ、そのままフリートークにも）.mp3",
    "traffic": "交通情報.mp3",
    "sa":      "SA info.mp3",   # 2026-09-04 専用BGM到着。交通情報からの流用をやめた
    "mail":    "otayori.mp3",
    "mail_fb": "otayori.mp3",
    "uranai":  "uranai.mp3",
    "ending":  "ED.mp3",
    # 電脳芸能ニュース専用BGM（2026-09-05 nordw提供）。仮の交通情報BGMから差し替え済み。
    "news":    "news.mp3",
}

# ★talk1 だけ声の入りを5秒遅らせる（nordw指定「5秒くらいから話始めるイメージ」）
LEAD_IN = {"talk1": 5.0}
DEFAULT_LEAD_IN = 1.8
# ★イントロ明けにBGMを下げる量（2026-09-05 nordw指定）。0にすると段差なし。
#   LEAD_IN のあるコーナーだけに効く。**ここが直接いじるつまみ。**
OP_STEP_DB = -2.0
STEP_RAMP = 0.5     # 下げるのにかける秒数（一瞬で切り替えるとカクッと聞こえる）

TAIL = 3.5          # 声が終わってからBGMを鳴らし続ける秒数（余韻）
# ★コーナー別の余韻（2026-09-04 nordw「EDはしゃべりおわったあと30秒くらい流れててもOK」）
#   番組の締めなので、曲が余韻として残る時間があった方が終わった感じが出る。
TAIL_BY_SEG: dict[str, float] = {"ending": 30.0}

VOICE_I = -16.0     # BGMとの**内部の比率**を決めるための声の基準（BGM_I との差＝10dB）

# ★トークブロックの最終出力レベル（2026-09-04 nordw耳判定で二段階）
#   -16 →（「トークがでかい。4dB」）→ -20 →（「まだデカい。あと2dB」）→ -22
#   声とBGMの内部比率(10dB)は保ったまま、ブロック全体が曲(-15)に対して下がる。
#   結果、曲との差は7dB。
MIX_OUT_I = -22.0
# ★2026-09-04 nordw耳判定で三段階。-34/-31/-28 → -28/-26/-24 → 最終 -23。
#   トークを -16→-20→-22 と下げたぶん、BGMも相対で持ち上げた形。
#   ダッキング無しの敷きっぱなしで成立する。
BGM_I = -23.0

# ★コーナーごとの個別レベル（2026-09-04 の申し合わせ）
#   BGMは曲ごとに曲調も密度も違うので、同じ数値が全コーナーで最適とは限らない。
#   まず共通値(BGM_I)を交通情報で決めて全体に当て、通しで浮いたコーナーだけここに書く。
#   例: "ending": -30.0
BGM_LEVEL: dict[str, float] = {
    # ★2026-09-04 nordw「OPの音楽は他より少し大きくてもOK」→ 最終 -20
    #   番組の頭は掴みなので、BGMが前に出ていた方が始まった感じが出る。
    "talk1": -20.0,   # 共通値 -23 から3dB上げ
}


def level_for(seg_name: str) -> float:
    return BGM_LEVEL.get(seg_name, BGM_I)

# ★ダッキングは既定OFF（2026-09-04 nordw「ダッキングが効きすぎてがくがく」）
#   初版は threshold=0.03 / ratio=12 という強い設定で、BGMが喋りのたびに
#   激しく上下して不自然だった。適正音量で敷くだけなら不要、という判断。
#   どうしても要る時のために、ごく緩い設定を残してある（DUCK=True で有効）。
DUCK = False
DUCK_PARAMS = "threshold=0.05:ratio=2.5:attack=80:release=700:makeup=1"


def _measure(src: Path, target_i: float) -> dict:
    r = subprocess.run(
        ["ffmpeg", "-i", str(src), "-af",
         f"loudnorm=I={target_i}:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    s = r.stderr
    return json.loads(s[s.rindex("{"):s.rindex("}") + 1])


def _ln(src: Path, target_i: float) -> str:
    m = _measure(src, target_i)
    return (f"loudnorm=I={target_i}:TP=-1.5:LRA=11"
            f":measured_I={m['input_i']}:measured_TP={m['input_tp']}"
            f":measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}"
            f":offset={m['target_offset']}:linear=true")


def duration(p: Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", str(p)], capture_output=True, text=True)
    return float(r.stdout.strip())


def mix(voice: Path, seg_name: str, dst: Path,
        bgm_i: float | None = None, duck: bool | None = None) -> float:
    """声にコーナーBGMを敷いて1本のwavにする（長さを返す）。
    bgm_i / duck を渡すと既定値を上書きできる（聴き比べ用）。"""
    bgm_i = level_for(seg_name) if bgm_i is None else bgm_i
    duck = DUCK if duck is None else duck
    # ★未登録のコーナーで落とさない（2026-09-05）。コーナーは今後も増える。
    #   ただし黙って通さない——BGMが抜けたまま納品すると、聴くまで気づけない。
    if seg_name not in BGM_MAP:
        print(f"  ⚠ BGM未登録のコーナー: {seg_name} → BGM無しで通す", file=__import__("sys").stderr)
        return 0.0
    bgm = find(BGM_MAP[seg_name])

    lead = LEAD_IN.get(seg_name, DEFAULT_LEAD_IN)
    tail = TAIL_BY_SEG.get(seg_name, TAIL)
    vlen = duration(voice)
    total = lead + vlen + tail
    # 余韻が長いコーナーはフェードアウトも長くとる（30秒の余韻に2.5秒フェードだと唐突）
    fade_len = 6.0 if tail >= 10 else 2.5
    fade_out_at = max(total - fade_len, 0.1)

    # BGMが足りなければループさせる（素材は2.5〜4分あるので通常は不要だが保険）
    # ★イントロだけBGMを大きいまま鳴らし、**声が入ったら一段下げる**
    #   （2026-09-05 nordw「OP、出だしだけ今の音量で、喋りがはじまったら2dbさげて」）。
    #   ダッキング（声のたびに沈む）とは別物。こちらは**声が始まったら以降ずっと**下げる。
    #   ⚠ t=lead で真っ二つに切り替えるとカクッと聞こえるので STEP_RAMP 秒かけて下げる。
    #   LEAD_IN のあるコーナー（＝いまは talk1 だけ）に効く。増えても同じ扱いでよい。
    step = ""
    if lead > 0 and OP_STEP_DB:
        g = 10 ** (OP_STEP_DB / 20)
        step = (f"volume='if(lt(t,{lead}),1,"
                f"if(lt(t,{lead + STEP_RAMP}),1-(1-{g:.4f})*(t-{lead})/{STEP_RAMP},{g:.4f}))'"
                f":eval=frame,")
    bgm_chain = (
        f"[1:a]aloop=loop=-1:size=2e9,atrim=0:{total:.2f},"
        f"{_ln(bgm, bgm_i)},{step}"
        f"afade=t=in:d=1.2,afade=t=out:st={fade_out_at:.2f}:d=2.5[bg];"
    )
    voice_chain = (
        f"[0:a]{_ln(voice, VOICE_I)},"
        f"adelay={int(lead*1000)}|{int(lead*1000)},apad=pad_dur={tail}[vo];"
    )
    if duck:
        # sidechaincompress: 第1入力(BGM)を第2入力(声)で押し下げる
        tail_chain = (f"[bg][vo]sidechaincompress={DUCK_PARAMS}[bgd];"
                      f"[bgd][vo]amix=inputs=2:duration=longest:normalize=0[out]")
    else:
        tail_chain = "[bg][vo]amix=inputs=2:duration=longest:normalize=0[out]"
    fc = bgm_chain + voice_chain + tail_chain

    # ★混ぜた結果は一度仮ファイルに出し、**測ってから**目標ラウドネスに合わせる。
    #   amix は入力数で割って出力する挙動があり（実測で -16 狙いが -23.1 になった）、
    #   係数を推測で足すと環境やffmpegの版で崩れる。測って合わせるのが確実。
    tmp = dst.with_suffix(".raw.wav")
    r = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(voice), "-i", str(bgm),
         "-filter_complex", fc, "-map", "[out]",
         "-ar", "44100", "-ac", "2", str(tmp)],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"BGMミックス失敗 {seg_name}: {r.stderr[-600:]}")

    r2 = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(tmp),
         "-af", _ln(tmp, MIX_OUT_I), "-ar", "44100", "-ac", "2", str(dst)],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r2.returncode != 0:
        raise RuntimeError(f"BGM後段の正規化失敗 {seg_name}: {r2.stderr[-600:]}")
    tmp.unlink(missing_ok=True)
    return duration(dst)


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    for name, f in BGM_MAP.items():
        try:
            p = find(f)
        except FileNotFoundError:
            print(f"NG  {name:9s} {f[:40]}"); continue
        print(f"OK  {name:9s} {f[:40]}  {duration(p):.0f}s  ({p.parent})")
