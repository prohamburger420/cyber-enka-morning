# -*- coding: utf-8 -*-
"""本番の一本道（GitHub Actions版）。2026-09-05

  パスA（放送前）: 本編を作る   talk1 / traffic / sa / mail_fb / uranai / ending
  パスB（放送中）: おたよりを作る mail  ← 曲1が流れている間に、その回のチャットを見て作る

★ローカル(generate_v2.py)との違いは3つだけ。ロジックは共有する:
  1. 台本生成が `claude -p` → **Anthropic API**（ランナーにCLIが無い）
  2. 音声合成が サブプロセス+jobs.json → **runner/tts.py を直接呼ぶ**（venvが1つなので）
  3. キャラクター設定を**コードから読まず assets/character.md から読む**
     （公開リポジトリにキャラ資産を置かないため）

★台本が作れなければフォールバック台本に倒す（番組は成立する）。
★★ただし**音声のブロックが欠けたら、その回は納品しない**（2026-09-05 方針変更）。
  以前は「無音の放送より欠けた放送のほうがまし」としていたが**逆だった**。
  CEBDR24は24時間の音楽チャンネルなので、配らなければ**通常運転で曲が流れ続ける**
  ＝誰も気づかない。欠けた番組を配ると**放送に乗って気づかれる**。
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
import bgm                               # noqa: E402  コーナー別BGM（★本番でも敷く）
import playlist                          # noqa: E402  放送用M3U（RadioDJが読む）
from runner import script_api, tts       # noqa: E402

ASSETS = ROOT / "assets"
OUT = ROOT / "out"

# ★カバーアート（2026-09-07 プロハンさん相談から）。
#   配信のオーバーレイは「いま流れているファイル」のアー写を出す仕組みで、こずえの
#   素のwavには何も入っていないため**前に流れた曲の歌手のアー写が出続けていた**。
#   → こずえの音声はmp3にして、①ID3にアー写＋アーティスト名を埋め込み、
#     ②ファイル名も「◯◯_雷音こずえ.mp3」にする（局側の名前検知にも乗せる。両対応）。
#   画像はプロハンさん支給のロゴ入りアー写（LINE 2026-09-07 08:24）。assetsはR2から来る。
COVER = ASSETS / "kozue_cover.jpg"
ALBUM = "雷音こずえのサイバー演歌モーニング"
# オーバーレイに出る想定の表示名。コーナーごとに変える
CORNER_TITLES = {
    "talk1": "オープニング", "traffic": "交通情報", "sa": "サービスエリア情報",
    "news": "ニュース", "mail": "おたより", "mail_fb": "おたより",
    "uranai": "今日の占い", "ending": "エンディング",
}


def _to_mp3(wav, mp3, corner: str, log) -> None:
    """BGM入りwavを、アー写・タグ入りのmp3にする。

    ★アー写が無い日もタグだけ埋めて続行する（番組を止めるほどの欠陥ではない。
      アーティスト名タグだけでも局側の表示が直る可能性がある）。
    """
    import subprocess
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav)]
    if COVER.exists():
        cmd += ["-i", str(COVER), "-map", "0:a", "-map", "1:0",
                "-c:v", "copy", "-disposition:v", "attached_pic",
                "-metadata:s:v", "title=Album cover",
                "-metadata:s:v", "comment=Cover (front)"]
    else:
        log.warning("★カバーアートが無い: %s（タグだけ埋めて続行）", COVER)
        cmd += ["-map", "0:a"]
    cmd += ["-c:a", "libmp3lame", "-q:a", "2", "-id3v2_version", "3",
            "-metadata", f"artist={playlist.ARTIST}",
            "-metadata", f"album={ALBUM}",
            "-metadata", f"title={CORNER_TITLES.get(corner, corner)}",
            str(mp3)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"mp3変換に失敗: {r.stderr[-400:]}")


def character() -> str:
    """★キャラクター設定は assets（R2から取得）から読む。コードには置かない。"""
    p = ASSETS / "character.md"
    if not p.exists():
        raise SystemExit(f"キャラクター設定が無い: {p}（R2からassetsを取得したか確認）")
    return p.read_text(encoding="utf-8").rstrip()


def synth_segments(segs, outdir: Path, log) -> tuple[list[Path], list[str]]:
    """ブロックごとに合成。1つ落ちても他を巻き込まないが、**落ちたことは必ず返す**。

    ★2026-09-05 方針変更: 以前は「無音の放送より欠けた放送のほうがまし」としていたが、
      **逆だった**（nordw）。CEBDR24は24時間の音楽チャンネルなので、番組を配らなければ
      **通常運転で曲が流れ続けるだけ＝誰も気づかない**。
      欠けた番組を配ると**放送に乗って気づかれる**。
      → **不完全な番組は配らない。**
    """
    made: list[Path] = []
    # ★欠けたブロックを覚えておく。呼び元が「配るかどうか」を決める
    failed: list[str] = []
    for idx, (name, body) in enumerate(segs, 1):
        wav = outdir / f"seg_{idx:02d}_{name}.wav"
        t0 = time.time()
        # ★1回だけ作り直す（2026-09-05）。実際に news が1回で落ちて欠けた。
        #   合成は外部プロセス＋モデル読み込みを伴うので、一過性の失敗がありうる。
        err = None
        for attempt in (1, 2):
            try:
                tts.synth(body, str(wav), corner=name)
                err = None
                break
            except Exception as e:
                # ★★例外の中身を必ず出す（2026-09-05）。
                #   以前は `%s` で例外を出していたが**メッセージが空の例外**だったため、
                #   ログに「合成に失敗（このブロックは飛ばす） news: 」とだけ残り、
                #   **原因がまったく分からなかった**。型とトレースバックを出す。
                import traceback
                err = e
                log.error("合成に失敗 %s (%d回目) %s: %s\n%s",
                          name, attempt, type(e).__name__, e, traceback.format_exc())
        if err is not None:
            failed.append(name)
            continue
        import soundfile as sf
        d = sf.info(str(wav)).duration
        cps = len(body) / d if d else 0
        log.info("%s %.1f秒 %.1f字/秒", name, d, cps)
        # ★字/秒の番人。8を超えたら台本の一部が音から落ちている疑い
        #   （2026-09-04 実測: 参照音声の書き起こし不足で塊の頭が消え7.9字/秒になった）
        # ⚠ 必ず**BGMを敷く前**に測る。BGMは前後に無音(lead/tail)を足すので、
        #   混ぜたあとの長さで割ると字/秒が薄まって番人が効かなくなる。
        if cps > 8.0:
            log.warning("★%s は速すぎる（%.1f字/秒）。台本の脱落を疑う", name, cps)

        # ★★BGMを敷く（2026-09-05 修正）。
        #   これが**本番に無かった**。BGMを敷くのは make_through.py だけで、
        #   それはローカル専用（通しmp3を作るためのもの）。本番ワークフローは
        #   一度も呼んでいなかった＝**納品されていたのは裸の声**。
        #   nordwが耳で決めた OP5秒待ち・イントロ明け-2dB・ニュース専用BGM は
        #   全部ローカルにしか効いていなかった。
        #   ⚠ 失敗したらブロックを欠けたものとして扱う（＝この回は配らない）。
        #     BGM無しの回を黙って配ると、**放送に乗るまで誰も気づけない**。
        try:
            mixed = bgm.mix(wav, name, outdir / f"seg_{idx:02d}_{name}_bgm.wav")
            wav.unlink(missing_ok=True)
            (outdir / f"seg_{idx:02d}_{name}_bgm.wav").rename(wav)
            log.info("  BGM %s → %.1f秒", bgm.BGM_MAP.get(name, "（無し）"), mixed)
        except Exception as e:
            import traceback
            log.error("BGMに失敗 %s %s: %s\n%s", name, type(e).__name__, e,
                      traceback.format_exc())
            failed.append(name)
            continue

        # ★★mp3化＋アー写埋め込み（2026-09-07）。ファイル名は playlist._seg_path と
        #   対になっている（`_雷音こずえ.mp3`）。**片方だけ変えると納品が全滅する。**
        #   ⚠ 失敗したら欠けたブロック扱い（wavのまま黙って配ると、局のオーバーレイに
        #     前の曲の歌手のアー写が出る＝気づかれるまで直せない）。
        mp3 = outdir / f"seg_{idx:02d}_{name}_{playlist.ARTIST}.mp3"
        try:
            _to_mp3(wav, mp3, name, log)
            wav.unlink()
        except Exception as e:
            log.error("mp3化に失敗 %s: %s", name, e)
            failed.append(name)
            continue
        made.append(mp3)
    return made, failed


def run(pass_name: str, day: datetime.date, no_audio: bool, traffic_live: bool,
        hour: int = 6) -> int:
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
        # ★song2は渡さない（2026-09-09）。曲2はおたよりより前に流れるので紹介しない。
        prompt = v2.PROMPT_B.format(
            character=character(), rules=v2.COMMON_RULES,
            theme=pack["theme_of_today"], mails=mails)

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

    # ★★読み検査（英字の検出＋カタカナの書き出し）。**本番でも必ず通す**。
    #   ⚠ これを入れるまで **ローカル(generate_v2.py)からしか呼ばれていなかった**
    #     ＝英字の検出も、毎朝の読みの記録も、本番では走っていなかった。
    #   ★2026-09-05 だけで同じ形の事故が3回:
    #     参照音声(ニュースのテンション) / BGM / この読み検査。
    #     **「ローカルで直した」と「本番で直った」は別。**
    #   ★落ちても番組は止めない（気づくための仕組みであって、直す仕組みではない）。
    from v2 import yomi_check
    yomi_check.write(segs, outdir.resolve(), log)

    if not no_audio:
        made, failed = synth_segments(segs, outdir, log)
        log.info("音声 %d本", len(made))
        if not made:
            log.error("音声が1本も作れなかった")
            return 1
        # ★★ブロックが1つでも欠けたら**配らない**（2026-09-05 方針変更）。
        #   実際に news が合成に失敗し、**6/7ブロックの番組が正常扱いで納品された**。
        #   CEBDR24は24時間の音楽チャンネル。配らなければ曲が流れ続けるだけで、
        #   誰も気づかない。欠けた番組を配ると放送に乗って気づかれる。
        #   ⚠ ここで失敗させると、後続の「R2へ納品」ステップに進まない＝配られない。
        # ★★2026-09-06: M3Uを前半・後半に割ったので、**諦める範囲も半分で済む**。
        #   前半が欠けた → 番組ごと配らない（従来どおり）
        #   後半だけ欠けた → **前半は配る**。後半のM3Uが書かれないので、
        #                    曲2のあとはそのまま通常の曲へ落ちる＝方針は同じ
        #   ⚠ どちらの半分かは playlist.half_of() に聞く。ここに写経すると、
        #     並びを変えたとき片方だけ直る。
        if failed:
            fatal = [n for n in failed if playlist.half_of(n) != "b"]
            if fatal:
                log.error("★前半のブロックが欠けたので**この回は納品しない**: %s", failed)
                log.error("  → 番組は流れず、通常の曲が流れ続ける（意図した挙動）")
                return 1
            log.error("★後半のブロックが欠けた: %s → **前半だけ配る**", failed)
            log.error("  → 曲2のあとは通常の曲へ落ちる（番組の前半は放送される）")

        # ★★放送用のM3Uを書く（2026-09-05）。RadioDJの
        #   「Load M3U Playlist by Date Mask」がこれを読む。
        #   曲もブロックも**並び順ごとこちらが決める**ので、こずえが読み上げた曲と
        #   流れる曲が必ず一致する（`infra/RADIODJ_SIYOU.md` に調査の全文）。
        #   ⚠ M3Uが無ければRadioDJは何も差し込まず、Auto DJの曲が流れ続ける
        #     ＝事故の時に無音にならない（イベントの Open Positions を `Top` にすること）。
        # ★★ジングルは複数ありうる（2026-09-06 nordw指摘）。assets/bgm/jingle*.wav を全部配る。
        #   どれを使うかは playlist.py が**日付から**決める（ランダムにしない＝再現できる）。
        jins = sorted((ASSETS / "bgm").glob("jingle*.wav"))
        if not jins:
            # ★ここは警告で済ませない。M3Uに書いた行のファイルが無い状態を作ると、
            #   放送中にRadioDJがそこで詰まる。**欠けた番組は配らない**の方針どおり止める。
            log.error("★ジングルが1本も無い: %s → この回は納品しない", ASSETS / "bgm")
            return 1
        import shutil
        for j in jins:
            shutil.copy2(j, outdir / j.name)
        log.info("ジングル %d本: %s", len(jins), [j.name for j in jins])
        # ★★パスBは**後半だけ**書き直す（2026-09-06）。
        #   前半は放送開始時にRadioDJが既に読み込んでいるので、書き換えても読み直されない。
        #   触ると「直したつもりで直っていない」を作るだけなので、halves で明示的に外す。
        made_m3u = playlist.build(
            day, outdir, pack, log,
            hours=playlist.BROADCAST_HOURS if pass_name == "a" else [hour],
            halves=("a", "b") if pass_name == "a" else ("b",))
        if not made_m3u:
            log.error("★M3Uが1本も書けなかった → この回は納品しない")
            return 1

        # ★★アーカイブ（2026-09-09 nordw依頼）。トーク通しを1本のmp3にまとめる。
        #   ⚠ **回ごとに作る**のが肝。おたよりは回ごとに違うのに、パスBは
        #     `seg_01_mail_*.mp3` を同じ名前で上書きするので、あとから作ると
        #     **最後の回（8時）のおたよりしか残らない**。だからパスBの直後に作る。
        #   ★曲とジングルは入らない（この機械に無い＝VPS上にしかある）。
        #     曲込みの完全版は infra/vps/kozue_archive.ps1 がVPS側で作る。
        #   ★落ちても番組は止めない。アーカイブは放送の後工程。
        # ⚠ 置き場所は **out/ の外**（ROOT/archive）。out/ はVPSが丸ごと降ろすので、
        #   中に置くと放送機に毎日20MB積もり、しかも prune --days 7 で消える。
        from v2 import archive
        try:
            archive.from_segments(
                day, outdir, ROOT / "archive", log.info,
                hour=hour if pass_name == "b" else None, cover=COVER)
        except Exception as e:
            log.error("★アーカイブに失敗（番組は続行）: %s", e)

    log.info("=== パス%s 完了 %.1f秒 ===", pass_name.upper(), time.time() - t0)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pass", dest="pass_name", choices=["a", "b"], required=True)
    ap.add_argument("--date", default=None, help="YYYY-MM-DD（既定は今日）")
    ap.add_argument("--no-audio", action="store_true")
    ap.add_argument("--traffic-live", action="store_true")
    # ★パスBは「何時の回か」を知る必要がある。おたよりは回ごとに作り直すので、
    #   その回のM3Uだけを書き直す。パスAは6/7/8の3本まとめて書くので使わない。
    ap.add_argument("--hour", type=int, default=6, help="パスB専用。何時の回か（6/7/8）")
    a = ap.parse_args()
    day = (datetime.date.fromisoformat(a.date) if a.date
           else datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).date())
    return run(a.pass_name, day, a.no_audio, a.traffic_live, a.hour)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
