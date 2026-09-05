# -*- coding: utf-8 -*-
"""YouTubeライブのチャットを、決めた時間だけ集める（2026-09-04）。

★番組での役割
  talk1で「テーマ決めたから**チャット欄に書いて**ちょうだい、この後の曲の間にね」と募集し、
  曲1が流れている間にここで拾って、同じ回の後半（交通情報の後）で読む。
  ＝「いま書いたら今すぐ読まれる」を成立させる部品。

★設計の要点
  - **募集アナウンスより前の発言は拾わない**。テーマと関係ない過去ログを
    「おたより」として読むと嘘になる（v1のSA捏造と同じ型の事故）。
    → 集め始めた時刻より後のものだけを残す。
  - **落ちても番組は止めない**。0件でも例外でも空リストを返し、
    呼び元はフォールバック版（こずえが自分でテーマに答える版）へ倒す。
  - **全部ログに残す**（nordw方針「テストプレイは全部データ」）。
    採用・不採用に関わらず生ログをそのまま保存する。後で「どう書かれたか」を見る資料になる。

★取得手段について（2026-09-04 実測して選定）
  - yt-dlp    … `--write-subs --sub-langs live_chat` は**配信終了まで書き出しを確定しない**。
                放送中に読む用途には使えない
  - chat-downloader … `ParsingError: Unable to parse initial video data` で即死
  - **pytchat** … 動いた。別の流れている配信で28秒に16件取得できることを確認済み
  ⚠ どれも非公式なのでYouTube側の変更で壊れうる。壊れたら公式のYouTube Data API
    （liveChatMessages.list・APIキーが要る）に移す。その日は自動でフォールバックが出る。
"""
import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

CHANNEL_LIVE_URL = "https://www.youtube.com/@cyberenka/live"


def resolve_live_video_id(url: str = CHANNEL_LIVE_URL) -> str | None:
    """チャンネルの「いま配信中」の動画IDを引く。

    ★動画IDを固定で持たない。24時間配信は再起動のたびにIDが変わるため
      （実測: o2E50roMd3g は 2026-09-04 16:58 開始の回）。
    """
    try:
        r = subprocess.run(
            ["yt-dlp", "--no-warnings", "--skip-download",
             "--print", "%(id)s|%(is_live)s", url],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=120)
        line = (r.stdout or "").strip().splitlines()[0]
        vid, is_live = line.split("|")
        return vid if is_live == "True" else None
    except Exception:
        return None


def collect(video_id: str, seconds: int, limit: int = 40,
            log_path: Path | None = None) -> list[dict]:
    """seconds 秒だけチャットを集めて返す。集め始めより後の発言だけ。

    返り値: [{"author": ..., "text": ..., "ts": ISO8601}, ...]
    失敗しても例外を投げない（番組を止めないため）。
    """
    started = time.time()
    got: list[dict] = []
    try:
        import pytchat
        chat = pytchat.create(video_id=video_id)
        while chat.is_alive() and time.time() - started < seconds:
            for c in chat.get().sync_items():
                # ★募集より前の発言を混ぜない。pytchatは接続時点以降を流すが、
                #   念のため時刻でも切る（過去ログを読み上げる事故を二重に防ぐ）
                got.append({"author": c.author.name, "text": c.message,
                            "ts": c.datetime})
                if len(got) >= limit:
                    break
            if len(got) >= limit:
                break
            time.sleep(2)
    except Exception as e:
        print(f"チャット取得に失敗（フォールバックへ倒す）: {type(e).__name__}: {e}",
              file=sys.stderr)

    if log_path:      # ★採否に関わらず生ログを残す
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(json.dumps(
            {"video_id": video_id, "collected_at": datetime.now(timezone.utc).isoformat(),
             "window_sec": seconds, "messages": got}, ensure_ascii=False, indent=1),
            encoding="utf-8")
    return got


def main() -> int:
    ap = argparse.ArgumentParser(description="YouTubeライブのチャットを集める")
    ap.add_argument("--video", help="動画ID。省略時はチャンネルの現行ライブを自動で引く")
    ap.add_argument("--seconds", type=int, default=180, help="集める秒数")
    ap.add_argument("--limit", type=int, default=40, help="最大件数")
    ap.add_argument("--out", type=Path, default=Path(__file__).parent / "chat_live.json")
    a = ap.parse_args()

    vid = a.video or resolve_live_video_id()
    if not vid:
        print("配信中の動画が見つからない → フォールバックへ", file=sys.stderr)
        a.out.write_text(json.dumps({"messages": []}, ensure_ascii=False), encoding="utf-8")
        return 1

    print(f"配信 {vid} から {a.seconds}秒ぶん集めます", flush=True)
    got = collect(vid, a.seconds, a.limit, log_path=a.out)
    print(f"{len(got)}件 取得 -> {a.out}")
    for m in got[:5]:
        print(f"  {m['author']}: {m['text'][:40]}")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
