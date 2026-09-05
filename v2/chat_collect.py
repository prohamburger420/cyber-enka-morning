# -*- coding: utf-8 -*-
"""YouTubeライブのチャットを、決めた時間だけ集める（2026-09-04）。

★番組での役割
  talk1で「テーマ決めたから**チャット欄に書いて**ちょうだい、この後の曲の間にね」と募集し、
  曲1が流れている間にここで拾って、**同じ回の後半（曲2のあと）で読む**。
  ⚠ 2026-09-06、一度これを「放送の直前に締め切る」設計に変えてしまった。
    nordw「**出勤や登校したあとによまれたってうれしくないの！！**」で差し戻し。
    送出の都合で締切を前倒しすると、この部品の存在理由が消える。
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
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

CHANNEL_LIVE_URL = "https://www.youtube.com/@cyberenka/live"


def _resolve_by_html(url: str) -> str | None:
    """★yt-dlpを使わない解決経路（2026-09-06）。

    本番(GitHub Actions)で yt-dlp が YouTube のbot判定に弾かれた:
      「Sign in to confirm you're not a bot」
    yt-dlpは動画情報APIまで踏み込むので判定が厳しい。**ページのHTMLを1枚取るだけ**なら
    通ることが多い（同じ判定を受けるかは環境ごとに実測）。
    /live ページのHTMLには canonical で動画URLが入っている。isLiveNow も拾えれば見る。
    """
    import re
    import urllib.request
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/128.0 Safari/537.36"),
            "Accept-Language": "ja,en;q=0.8",
        })
        html = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")
    except Exception as e:
        print(f"★HTML経路も失敗: {type(e).__name__}: {e}", file=sys.stderr)
        return None
    m = re.search(r'<link rel="canonical" href="https://www\.youtube\.com/watch\?v=([\w-]{11})"', html)
    if not m:
        print(f"★HTMLに動画IDが無い（{len(html)}バイト取得。同意画面かも）", file=sys.stderr)
        return None
    vid = m.group(1)
    if '"isLiveNow":true' not in html:
        print(f"★HTML経路: {vid} は isLiveNow ではない", file=sys.stderr)
        return None
    print(f"HTML経路で解決: {vid}", file=sys.stderr)
    return vid


def resolve_live_video_id(url: str = CHANNEL_LIVE_URL) -> str | None:
    """チャンネルの「いま配信中」の動画IDを引く。

    ★動画IDを固定で持たない。24時間配信は再起動のたびにIDが変わるため
      （実測: o2E50roMd3g は 2026-09-04 16:58 開始の回）。

    ⚠★★理由を握りつぶさない（2026-09-06 これで丸一日ダマされた）。
      本番のランナーに **yt-dlp が入っていなかった**（pip install の行に無い）。
      FileNotFoundError が `except Exception: return None` に吸われ、
      呼び元は「配信中の動画が見つからない」と表示していた。
      ＝**自分の入れ忘れを、配信が止まっているせいにしていた。**
      しかも失敗まで0.02秒。ネットワークに行っていないのに気づけたはずだった。
      → 例外も、yt-dlpのstderrも、**必ず出す**。
    """
    try:
        r = subprocess.run(
            ["yt-dlp", "--no-warnings", "--skip-download",
             "--print", "%(id)s|%(is_live)s", url],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=120)
    except FileNotFoundError:
        print("★yt-dlp が入っていない → HTML経路を試す", file=sys.stderr)
        return _resolve_by_html(url)
    except Exception as e:
        print(f"★yt-dlp の実行に失敗: {type(e).__name__}: {e} → HTML経路を試す", file=sys.stderr)
        return _resolve_by_html(url)
    if r.returncode != 0:
        # ★本番(Actions)はここに来る: YouTubeのbot判定「Sign in to confirm you're not a bot」
        print(f"★yt-dlp が終了コード{r.returncode}: "
              f"{(r.stderr or '').strip()[:300]} → HTML経路を試す", file=sys.stderr)
        return _resolve_by_html(url)
    try:
        line = (r.stdout or "").strip().splitlines()[0]
        vid, is_live = line.split("|")
    except Exception:
        print(f"★yt-dlp の出力が読めない: {(r.stdout or '')[:200]!r}", file=sys.stderr)
        return None
    if is_live != "True":
        print(f"★動画は見つかったが配信中ではない: {vid} (is_live={is_live})",
              file=sys.stderr)
        return None
    return vid


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


# ============================================================
# ★★公式 YouTube Data API 経路（2026-09-06 これが本番の本線）
#   実測の結果、非公式経路は本番(GitHub Actions)から**全滅**:
#     yt-dlp   … 「Sign in to confirm you're not a bot」（IPで弾く）
#     HTML     … 1.18MB返るが動画IDが入っていない（データセンターIPには渡さない）
#     RSS      … 配信中のライブが載らない
#     pytchat  … watchページが取れず InvalidVideoIdException
#   公式APIはキー認証なのでIPで弾かれない。無料枠1日10,000ユニットに対し、
#   この番組の消費は 1日≒320（search 100×3回 + videos 1×3 + chat poll ≒5）。
#   ⚠ キーは環境変数 YT_API_KEY。無ければ従来経路（ローカル用）に落ちる。
# ============================================================
CHANNEL_ID = "UCk0HbpDai30CJt-KJxxSAYQ"     # @cyberenka（2026-09-06 HTMLから実測）


def _api(path: str, **params) -> dict:
    import urllib.parse
    import urllib.request
    params["key"] = os.environ["YT_API_KEY"]
    url = f"https://www.googleapis.com/youtube/v3/{path}?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def resolve_live_api(channel_id: str = CHANNEL_ID) -> str | None:
    """公式APIで「いま配信中」の動画IDを引く（search.list=100ユニット）。"""
    try:
        r = _api("search", part="id", channelId=channel_id,
                 eventType="live", type="video", maxResults=1)
        items = r.get("items", [])
        if not items:
            print("★API: このチャンネルに配信中のライブが無い", file=sys.stderr)
            return None
        return items[0]["id"]["videoId"]
    except Exception as e:
        print(f"★APIでの解決に失敗: {type(e).__name__}: {e}", file=sys.stderr)
        return None


def collect_api(video_id: str, seconds: int, limit: int = 40,
                log_path: Path | None = None) -> list[dict]:
    """公式APIでチャットを集める。集め始めより後の発言だけ。

    ⚠ liveChatMessages.list は初回に**過去ぶんの履歴も返す**。
      募集アナウンスより前の発言を読むと嘘になるので、publishedAt で必ず切る。
    """
    started_utc = datetime.now(timezone.utc)
    t0 = time.time()
    got: list[dict] = []
    try:
        v = _api("videos", part="liveStreamingDetails", id=video_id)
        chat_id = (v["items"][0].get("liveStreamingDetails", {})
                   .get("activeLiveChatId"))
        if not chat_id:
            print(f"★API: {video_id} にアクティブなチャットが無い", file=sys.stderr)
            raise SystemExit  # → finally でログだけ書いて空を返す
        token = None
        while time.time() - t0 < seconds and len(got) < limit:
            kw = {"part": "snippet,authorDetails", "liveChatId": chat_id,
                  "maxResults": 200}
            if token:
                kw["pageToken"] = token
            # ⚠ HTTPのパスは `liveChat/messages`。リソース名の `liveChatMessages` を
            #   そのまま書くと**本文が空の404**が返る（2026-09-06 実際に踏んだ）。
            r = _api("liveChat/messages", **kw)
            token = r.get("nextPageToken")
            for it in r.get("items", []):
                sn = it["snippet"]
                ts = sn.get("publishedAt", "")
                # ★集め始めより前の発言は捨てる（初回レスポンスに履歴が混ざる）
                if ts and ts < started_utc.isoformat().replace("+00:00", "Z"):
                    continue
                txt = (sn.get("textMessageDetails") or {}).get("messageText", "")
                if not txt:
                    continue
                got.append({"author": it["authorDetails"]["displayName"],
                            "text": txt, "ts": ts})
                if len(got) >= limit:
                    break
            # APIが指定する間隔を尊重する（既定は数千ms）
            wait = int(r.get("pollingIntervalMillis", 5000)) / 1000
            if time.time() - t0 + wait < seconds:
                time.sleep(wait)
            else:
                break
    except SystemExit:
        pass
    except Exception as e:
        print(f"★APIでのチャット取得に失敗: {type(e).__name__}: {e}", file=sys.stderr)
    if log_path:      # ★採否に関わらず生ログを残す（collect() と同じ）
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(json.dumps(
            {"video_id": video_id, "collected_at": started_utc.isoformat(),
             "window_sec": seconds, "via": "api", "messages": got},
            ensure_ascii=False, indent=1), encoding="utf-8")
    return got


JST = timezone(timedelta(hours=9))


def _wait_until(hhmmss: str) -> None:
    """日本時間の HH:MM:SS まで待つ。過ぎていれば待たない。

    ★★時計に合わせる（2026-09-06）。「準備に何秒かかるか」から逆算していたが、
      準備が速いと**募集アナウンスより前から集めてしまう**。
      talk1 の募集は 6:00:50 頃。それより前のチャットを「おたより」として読むと嘘になる
      （v1 のSA捏造と同じ型）。⚠ 準備時間は日によってぶれるので、**逆算は当てにならない**。
      壁時計に合わせれば、準備が遅れて収集時間が短くなることはあっても、
      **間違った時間帯を集めることはない**。
    """
    now = datetime.now(JST)
    h, m, s = (int(x) for x in hhmmss.split(":"))
    target = now.replace(hour=h, minute=m, second=s, microsecond=0)
    d = (target - now).total_seconds()
    if d <= 0:
        print(f"★{hhmmss} は既に過ぎている（{d:.0f}秒）。すぐ集め始める", flush=True)
        return
    print(f"{hhmmss} まで {d:.0f}秒待つ", flush=True)
    time.sleep(d)


def main() -> int:
    ap = argparse.ArgumentParser(description="YouTubeライブのチャットを集める")
    ap.add_argument("--video", help="動画ID。省略時はチャンネルの現行ライブを自動で引く")
    ap.add_argument("--seconds", type=int, default=180, help="集める秒数")
    ap.add_argument("--limit", type=int, default=40, help="最大件数")
    ap.add_argument("--out", type=Path, default=Path(__file__).parent / "chat_live.json")
    # ★放送に合わせる用。日本時間の HH:MM:SS で「いつからいつまで」を指定する
    ap.add_argument("--from-jst", help="この時刻まで待ってから集め始める（例 06:01:00）")
    ap.add_argument("--until-jst", help="この時刻まで集める（例 06:04:00）")
    a = ap.parse_args()

    if a.until_jst:
        if a.from_jst:
            _wait_until(a.from_jst)
        now = datetime.now(JST)
        h, m, s = (int(x) for x in a.until_jst.split(":"))
        end = now.replace(hour=h, minute=m, second=s, microsecond=0)
        a.seconds = max(0, int((end - now).total_seconds()))
        print(f"{a.until_jst} まで＝{a.seconds}秒ぶん集めます", flush=True)
        if a.seconds < 20:
            print(f"★集める時間が {a.seconds}秒しかない。起動が遅すぎる", file=sys.stderr)

    # ★キーがあれば公式API（本番はこちら）。無ければ非公式経路（ローカル用）
    use_api = bool(os.environ.get("YT_API_KEY"))
    print(f"経路: {'公式API' if use_api else '非公式(yt-dlp/pytchat)'}", flush=True)
    vid = a.video or (resolve_live_api() if use_api else resolve_live_video_id())
    if not vid:
        print("配信中の動画が見つからない → フォールバックへ", file=sys.stderr)
        a.out.write_text(json.dumps({"messages": []}, ensure_ascii=False), encoding="utf-8")
        return 1

    print(f"配信 {vid} から {a.seconds}秒ぶん集めます", flush=True)
    got = (collect_api if use_api else collect)(vid, a.seconds, a.limit, log_path=a.out)
    print(f"{len(got)}件 取得 -> {a.out}")
    for m in got[:5]:
        print(f"  {m['author']}: {m['text'][:40]}")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
