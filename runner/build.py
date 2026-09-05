# -*- coding: utf-8 -*-
"""本番の一本道（GitHub Actions版）。2026-09-05

  パスA（放送前）: 本編を作る   talk1 / traffic / sa / mail_fb / uranai / ending
  パスB（放送中）: おたよりを作る mail  ← 曲1が流れている間に、その回のチャットを見て作る

★ローカル(generate_v2.py)との違いは3つだけ。ロジックは共有する:
  1. 台本生成が `claude -p` → **Anthropic API**（ランナーにCLIが無い）
  2. 音声合成が サブプロセス+jobs.json → **runner/tts.py を直接呼ぶ**（venvが1つなので）
  3. キャラクター設定を**コードから読まず assets/character.md から読む**
     （公開リポジトリにキャラ資産を置かないため）

★落ちても番組を止めない。台本が作れなければフォールバック台本、
  合成が落ちればそのブロックを飛ばす。無音の放送より、欠けた放送のほうがまし。
"""
import argparse
import datetime
import json
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "v2"))
sys.path.insert(0, str(ROOT / "data"))

import generate_kozue_asa as v1          # noqa: E402  天気・交通・SA・台本の分解を再利用
import generate_v2 as v2                 # noqa: E402  構成・選曲・チャット選別
from runner import script_api, tts       # noqa: E402

ASSETS = ROOT / "assets"
OUT = ROOT / "out"


def character() -> str:
    """★キャラクター設定は assets（R2から取得）から読む。コードには置かない。"""
    p = ASSETS / "character.md"
    if not p.exists():
        raise SystemExit(f"キャラクター設定が無い: {p}（R2からassetsを取得したか確認）")
    return p.read_text(encoding="utf-8").rstrip()


def synth_segments(segs, outdir: Path, log) -> list[Path]:
    """ブロックごとに合成。★1つ落ちても他を巻き込まない。"""
    made = []
    for idx, (name, body) in enumerate(segs, 1):
        wav = outdir / f"seg_{idx:02d}_{name}.wav"
        t0 = time.time()
        try:
            tts.synth(body, str(wav), corner=name)
        except Exception as e:
            log.error("合成に失敗（このブロックは飛ばす） %s: %s", name, e)
            continue
        import soundfile as sf
        d = sf.info(str(wav)).duration
        cps = len(body) / d if d else 0
        log.info("%s %.1f秒 %.1f字/秒", name, d, cps)
        # ★字/秒の番人。8を超えたら台本の一部が音から落ちている疑い
        #   （2026-09-04 実測: 参照音声の書き起こし不足で塊の頭が消え7.9字/秒になった）
        if cps > 8.0:
            log.warning("★%s は速すぎる（%.1f字/秒）。台本の脱落を疑う", name, cps)
        made.append(wav)
    return made


def run(pass_name: str, day: datetime.date, no_audio: bool, traffic_live: bool) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    log = logging.getLogger("build")
    t0 = time.time()
    outdir = OUT / day.isoformat()
    outdir.mkdir(parents=True, exist_ok=True)

    if pass_name == "a":
        pack = v2.build_pack(day, log, traffic_live)
        (outdir / "datapack.json").write_text(
            json.dumps(pack, ensure_ascii=False, indent=2), encoding="utf-8")
        log.info("テーマ: %s", pack["theme_of_today"])
        log.info("曲1: %s / %s (%d秒)", pack["song1"]["title"],
                 pack["song1"]["artist"], pack["song1"]["sec"])
        prompt = v2.PROMPT_A.format(
            character=character(), rules=v2.COMMON_RULES,
            datapack=json.dumps(pack, ensure_ascii=False, indent=2))
    else:
        pack = json.loads((outdir / "datapack.json").read_text(encoding="utf-8"))
        chat = v2.load_chat(outdir, log)
        kept, dropped = v2.filter_chat(chat["messages"])
        for d in dropped:
            log.warning("チャット除外[%s]: %s", d["_why"], d["text"][:40])
        log.info("チャット %d件中 %d件を採用", len(chat["messages"]), len(kept))
        mails = "\n".join(f"- {m['author']}さん: {m['text']}" for m in kept)
        prompt = v2.PROMPT_B.format(
            character=character(), rules=v2.COMMON_RULES,
            theme=pack["theme_of_today"], mails=mails,
            song2=f'{pack["song2"]["title"]}（歌: {pack["song2"]["artist"]}）')

    (outdir / f"prompt_{pass_name}.txt").write_text(prompt, encoding="utf-8")
    try:
        script = script_api.generate(prompt, log)
    except Exception as e:
        # ★v1の fallback_script は使わない。v1は曲1曲の構成で、v2のpackには
        #   `song_of_today` が無く KeyError で落ちる（2026-09-05 実測）。
        #   最後の砦が落ちたら意味がないので、v2構成の専用フォールバックを持つ。
        log.error("台本生成に失敗: %s", e)
        if pass_name == "b":
            # ★パスBは落ちてよい。パスAで作ったフォールバック版おたよりが既にあるので、
            #   下手なものを上書きせず、何も作らずに正常終了する。
            log.warning("おたよりは作らない。パスAのフォールバック版が流れる")
            return 0
        from runner import fallback
        script = fallback.build(pack)
    (outdir / f"script_{pass_name}.md").write_text(script, encoding="utf-8")

    segs = v1.parse_segments(script)
    log.info("台本OK: %s", [s[0] for s in segs])

    if not no_audio:
        made = synth_segments(segs, outdir, log)
        log.info("音声 %d本", len(made))
        if not made:
            log.error("音声が1本も作れなかった")
            return 1

    log.info("=== パス%s 完了 %.1f秒 ===", pass_name.upper(), time.time() - t0)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pass", dest="pass_name", choices=["a", "b"], required=True)
    ap.add_argument("--date", default=None, help="YYYY-MM-DD（既定は今日）")
    ap.add_argument("--no-audio", action="store_true")
    ap.add_argument("--traffic-live", action="store_true")
    a = ap.parse_args()
    day = (datetime.date.fromisoformat(a.date) if a.date
           else datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).date())
    return run(a.pass_name, day, a.no_audio, a.traffic_live)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
