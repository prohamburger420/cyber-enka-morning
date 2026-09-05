# -*- coding: utf-8 -*-
"""
雷音こずえ「朝のミニ番組」全自動生成パイプライン（CEBDR24向けデモ）
=====================================================================
ワンコマンドで「今日の実データ収集 → 台本自動生成 → 音声化 → 1本のmp3」まで通す。

    python generate_kozue_asa.py            # フル実行（claude -p で台本生成 + 音声化）
    python generate_kozue_asa.py --no-audio # 台本まで（音声化スキップ）
    python generate_kozue_asa.py --template # claude を呼ばずテンプレ台本（接続テスト用）

出力: episodes/YYYY-MM-DD/
    datapack.json       … その日の実データ（天気・交通・暦・今日の一曲）
    prompt.txt          … 台本生成に使ったプロンプト（全ログ方針）
    script.md           … 生成された台本（人間が読む用）
    seg_NN_名前.mp3     … コーナーごとの音声
    kozue_YYYY-MM-DD.mp3 … 完成エピソード
    run.log             … 実行ログ全部

必要環境: Python 3.10+ / edge-tts / ffmpeg / claude CLI（--template なら不要）
"""

import argparse
import asyncio
import datetime
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE / "data"))   # data/sapa.py（実在SAのDB）を読む
VOICE = "ja-JP-NanamiNeural"   # デモ用。本番はこずえ公式ボイスに差し替え
TTS_RATE = "+8%"               # 朝番組なので少しだけ元気に
# 全国天気: 地方ごとの代表観測地点（Open-Meteoは1リクエストで複数地点を返せる）
REGIONS = [
    ("北海道", "札幌", 43.06, 141.35),
    ("東北", "仙台", 38.27, 140.87),
    ("関東", "東京", 35.69, 139.69),
    ("北陸", "金沢", 36.59, 136.63),
    ("東海", "名古屋", 35.18, 136.91),
    ("近畿", "大阪", 34.69, 135.50),
    ("中国", "広島", 34.39, 132.46),
    ("四国", "高松", 34.34, 134.05),
    ("九州", "福岡", 33.59, 130.40),
    ("沖縄", "那覇", 26.21, 127.68),
]

# WMO weather code → 日本語
WMO = {
    0: "快晴", 1: "おおむね晴れ", 2: "晴れ時々くもり", 3: "くもり",
    45: "霧", 48: "着氷性の霧",
    51: "弱い霧雨", 53: "霧雨", 55: "強い霧雨",
    61: "小雨", 63: "雨", 65: "強い雨",
    71: "小雪", 73: "雪", 75: "大雪",
    80: "にわか雨", 81: "にわか雨", 82: "激しいにわか雨",
    95: "雷雨", 96: "雷雨（ひょう）", 99: "激しい雷雨",
}

# 今日の一曲ローテーション（デモ用。実運用ではプロハン提供の正式リストに差し替え）
# 「今日の一曲」プール = 雷音こずえのオリジナル曲。
# 引火帝国YouTube(@cyberenka)/Apple Music で実在確認済みのものを採用（2026-07-15調べ）。
# カバー曲（氷雨・帝國華撃団・ムーンライト伝説等）は原曲権利がグレーなので番組ローテからは外す。
SONGS = [
    "サイバー演歌がとまらない",       # AM
    "DJ慕情",                         # YT（サイバー演歌の原点）
    "サイバー演歌伝説",               # YT/AM
    "おやりなさい",                   # AM
    "かみなり娘 サイバー捕り物帖",     # AM
    "マイクロフォン仁義",             # YT
    "AI音楽をなめんじゃないよ",        # YT
    "こずえのトラック野郎 弐〇弐伍",   # AM
    "聴かねば即刻地獄行き",           # AM
    "デモクラシー誓魂",               # AM
    "魁!サイバー祭り",                # AM
]
# 要照合（note設定記事のみ/現物URL未確認 → 確認できたら上へ移す）:
#   "グリッター純情"（note代表曲）, "雷音 Thunder Tone"(AM/英題でTTS要確認),
#   "CYBER ENKA REVOLUTION 2026"(AM最新/英題)
# レガシー未確認（旧プール。こずえ帰属が未検証のため回転から除外。要照合）:
#   ちょっといいアンタ / サイバー大逆転音頭2026 / 無駄唄 / ペテン師の夜〜お金・逮捕・内緒〜 /
#   蝶々 / SOREZORE / ちなみに今は三時 / こずえのマッチョドラゴン / ペルセウス /
#   丸竹サイバー京都 / 導け！ナンマイダー / サンダーキャット節

WEEKDAYS = "月火水木金土日"


# ---------------------------------------------------------------- data
def collect_weather(log: logging.Logger) -> dict:
    """Open-Meteo（キー不要・無料）から全国10地方の今日の予報を一括取得する。"""
    lats = ",".join(str(r[2]) for r in REGIONS)
    lons = ",".join(str(r[3]) for r in REGIONS)
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lats}&longitude={lons}"
        "&daily=weather_code,temperature_2m_max,temperature_2m_min,"
        "precipitation_probability_max"
        "&timezone=Asia%2FTokyo&forecast_days=1"
    )
    log.info("weather GET (全国%d地点) %s", len(REGIONS), url)
    with urllib.request.urlopen(url, timeout=15) as r:
        data = json.loads(r.read().decode("utf-8"))
    if isinstance(data, dict):  # 1地点だと配列にならない
        data = [data]
    regions = []
    for (name, city, _, _), loc in zip(REGIONS, data):
        d = loc["daily"]
        code = d["weather_code"][0]
        regions.append({
            "region": name,
            "city": city,
            "text": WMO.get(code, "不明"),
            "temp_max": d["temperature_2m_max"][0],
            "temp_min": d["temperature_2m_min"][0],
            "precip_prob": d["precipitation_probability_max"][0],
        })
    out = {"source": "open-meteo.com", "regions": regions}
    log.info("weather OK: %d地方 %s", len(regions),
             " / ".join(f"{r['region']}{r['text']}" for r in regions))
    return out


UA = "kozue-asa-radio/1.0 (daily 05:00 JST; contact: abototanigami@gmail.com)"
IH_AREA = {"area01": "北海道", "area02": "東北", "area03": "北陸・信越",
           "area04": "関東", "area05": "東海", "area06": "関西",
           "area07": "中国", "area08": "四国", "area09": "九州・沖縄"}


def _get_json(url: str, must_have: tuple, timeout: int = 20):
    """★HTTP 200 を信用しない。期待するキーが本当に入っているかまで見る。

    メンテ画面・ソフト404・「更新され続けるのに永久に空」のフィードが
    いずれも 200 で返る事例が実在する（調査で3サイト確認）。
    ステータスコードだけ見ていると、毎朝のログが**偽の「異常なし」**で埋まる
    ＝無人運用で最悪の壊れ方。だからここで content-type と中身を検査する。
    """
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        ct = r.headers.get("Content-Type", "")
        if "json" not in ct.lower():
            raise ValueError(f"JSONでない: {ct!r} ({url})")
        d = json.loads(r.read())
    if isinstance(d, list):
        if not d:
            raise ValueError(f"空リスト＝ソフト404の疑い ({url})")
    elif must_have and not any(k in d for k in must_have):
        raise ValueError(f"期待キー {must_have} が無い＝ソフト404の疑い ({url})")
    return d


def collect_traffic(log: logging.Logger, live: bool = False) -> dict:
    """交通情報。既定は stub（現行方針＝特定路線の遅延を言わず公式へ誘導）。

    ★live=True は --traffic-live で明示的に入れた時だけ。既定OFFの理由は2つ:
      (1) 番組は朝5時に作る録音で、放送時刻がまだ確定していない。
          交通情報は足が速く、流れる頃には解除済みということが普通に起きる。
          **放送時刻が決まってから、時刻の明言とセットで有効化する**（nordw 2026-09-04）。
      (2) 道路ソース(iHighway/首都高)は利用規約が存在しない。使うと決めるまで
          毎朝の自動アクセスはしない。
    """
    if not live:
        log.info("traffic: stub mode（現行方針。--traffic-live で実データON）")
        return {
            "source": "stub",
            "note": "実データ未使用。台本側は特定路線の遅延を捏造せず一般口上で処理",
            "lines": [],
        }
    return collect_traffic_live(log)


def collect_traffic_live(log: logging.Logger) -> dict:
    """交通情報の実データ。**必ず取得時刻をセットで返す。**

    ★番組は朝5時に作る録音で、放送されるのは後。交通情報は足が速く、
      流れる頃には解除済みということが普通に起きる。だから台本には
      「今朝5時時点」と時刻を明言させる（時刻を言わずに断言するのが一番危ない）。
    ★渋滞は入れない。最も足が速く、朝の渋滞は日常でニュース価値が薄い。
      拾うのは通行止め（影響が大きく長く続く）と事故まで。
    ★どれかが落ちても番組は落とさない。取れたものだけ返し、
      取れなかった源は sources に "NG" として残す（黙って欠けさせない）。

    ライセンス:
      - 鉄道 = ODPT。「営利、非営利を問わず利用できる」と明文。都営は CC BY 4.0。
      - 道路 = iHighway / 首都高。**利用規約が存在しない**＝許諾も禁止も無い。
        公共の安全情報という性質で使う判断（nordw 2026-09-04）。
        本番運用前にプロハン経由でNEXCOへ一報を入れておくのが安全。
    """
    now = datetime.datetime.now()
    out = {
        "fetched_at": now.isoformat(timespec="minutes"),
        "fetched_at_ja": f"{now.hour}時{now.minute:02d}分",
        "caveat": "録音番組。放送時には状況が変わっている可能性があるため、"
                  "台本では必ず取得時刻を明言し、最新は公式で確認するよう促すこと。",
        "sources": {},
        "road_closed": [], "road_accident": [], "shutoko": [], "rail": [],
    }

    # --- 高速道路（NEXCO iHighway。2ホストは担当エリアが違うのでマージ）---
    seen, closed, acc = set(), [], []
    ok_hosts = 0
    for host in ("https://ihighway.jp", "https://www.c-ihighway.jp"):
        try:
            d = _get_json(f"{host}/datas/json/traffic.json", ("area01", "area04"))
        except Exception as e:
            log.warning("traffic: %s NG %s", host, e)
            continue
        ok_hosts += 1
        for area, v in d.items():
            for kind, roads in (v.get("trafficInfo") or {}).items():
                if kind not in ("closed", "accident"):
                    continue
                for road in roads:
                    for inf in road.get("info", []):
                        key = (kind, road.get("roadName"), inf.get("title"),
                               inf.get("direction"))
                        if key in seen:
                            continue
                        seen.add(key)
                        row = {"area": IH_AREA.get(area, area),
                               "road": road.get("roadName"),
                               "section": inf.get("title"),
                               "direction": inf.get("direction"),
                               "reason": inf.get("reason")}
                        (closed if kind == "closed" else acc).append(row)
    out["sources"]["highway"] = f"iHighway({ok_hosts}/2ホスト)" if ok_hosts else "NG"
    out["road_closed"], out["road_accident"] = closed[:8], acc[:8]

    # --- 首都高（入口閉鎖など。関東の聴取者に効く）---
    try:
        d = _get_json("https://search.shutoko-eng.jp/traffic/kisei.json",
                      ("item", "update"))
        out["sources"]["shutoko"] = f"首都高({d['update']})"
        out["shutoko"] = [{"route": r[0], "direction": r[2], "point": r[3],
                           "kind": r[6]} for r in d["item"]][:6]
    except Exception as e:
        log.warning("traffic: 首都高 NG %s", e)
        out["sources"]["shutoko"] = "NG"

    # --- 鉄道（ODPT。ライセンスが明確に営利可なものだけ）---
    try:
        d = _get_json("https://api-public.odpt.org/api/v4/odpt:TrainInformation", ())
        bad = [{"railway": x["odpt:railway"].split(".")[-1],
                "text": x["odpt:trainInformationText"]["ja"]}
               for x in d if "平常" not in x["odpt:trainInformationText"]["ja"]
               and "ありません" not in x["odpt:trainInformationText"]["ja"]]
        out["sources"]["rail"] = f"ODPT({len(d)}路線)"
        out["rail"] = bad[:6]
    except Exception as e:
        log.warning("traffic: ODPT NG %s", e)
        out["sources"]["rail"] = "NG"

    live = [k for k, v in out["sources"].items() if v != "NG"]
    out["source"] = "live" if live else "stub"
    if not live:
        out["note"] = "全ソース取得失敗。特定路線の遅延を捏造せず一般口上で処理"
    log.info("traffic: %s 通行止め%d 事故%d 首都高%d 鉄道異常%d %s",
             out["source"], len(out["road_closed"]), len(out["road_accident"]),
             len(out["shutoko"]), len(out["rail"]), out["sources"])
    return out


def collect_sa(day: datetime.date, log: logging.Logger) -> dict | None:
    """「今日のサービスエリア」を実在DB（Wikidata/CC0）から引く。

    ★これをLLMに選ばせてはいけない。2026-07-14の台本で「刈谷ハイウェイオアシスは
      東名阪自動車道」と書いた（正しくは伊勢湾岸自動車道）。放送に乗る事実誤り。
      → 施設と路線名はここで確定させ、プロンプト側は「変えるな」とだけ言う。
    DBが無くても番組は落とさない（None を返し、台本は一般口上に寄せる）。
    """
    try:
        import sapa
        f = sapa.pick_for_date(day)
        log.info("SA: %s（%s / %s%s）", f["name"], f["route"],
                 f.get("pref") or "", f.get("city") or "")
        return f
    except Exception as e:
        log.warning("SA DBが読めない → 今日のサービスエリアは省略: %s", e)
        return None


def build_datapack(log: logging.Logger, traffic_live: bool = False) -> dict:
    today = datetime.date.today()
    song = SONGS[today.timetuple().tm_yday % len(SONGS)]
    pack = {
        "date": today.isoformat(),
        "date_ja": f"{today.year}年{today.month}月{today.day}日",
        "weekday_ja": WEEKDAYS[today.weekday()] + "曜日",
        "weather": collect_weather(log),
        "traffic": collect_traffic(log, live=traffic_live),
        "sa_of_today": collect_sa(today, log),
        "song_of_today": song,
        "program": {
            "channel": "CEBDR24（サイバー演歌ブレイクダウンラジオ24）",
            "title": "こずえの朝",
        },
    }
    log.info("datapack: %s", json.dumps(pack, ensure_ascii=False))
    return pack


# ---------------------------------------------------------------- script
SEGMENTS = ["opening", "weather", "fortune", "traffic", "song", "ending"]

PROMPT_TEMPLATE = """あなたはネットラジオ局 CEBDR24（サイバー演歌ブレイクダウンラジオ24）の放送作家であり、\
同時にパーソナリティ「雷音こずえ」本人として台本を書く。

{__CHARACTER_FROM_ASSETS__}

# 番組
「こずえの朝」— 毎朝の録音ミニ番組。コーナー: オープニング→全国の天気予報→こずえの朝占い→交通情報→今日の一曲→エンディング。
全体で読み上げ4〜6分。

# 今日の実データ（datapack）
{datapack}

# 執筆ルール
1. 天気は全国天気。datapack の weather.regions（北から南へ10地方）を順に読む。数値の捏造・改変は禁止。\
全地方を同じ調子で読むと単調なので、気温・天気にメリハリのある地方は厚めに、似た地方はまとめてテンポよく。\
そして★ここが番組の肝★: 地方に触れるとき、こずえの元バスガイドの経歴が生きるひとことを挟む（その土地を走った思い出、名所・名物の実在の知識、運転手さんとの思い出など）。\
ただし全地方でやるとくどいので、今日は2〜3地方だけ。日替わりで違う土地に思い出が出るのが理想。
2. 占いは「今日の運勢ベスト3の星座 + 最下位1つ + 最下位への救い + 今日のラッキーアイテム」方式。星座は12星座から選ぶ。\
サイバー演歌の世界観として、スピリチュアル寄りの語彙を"隠し味"程度に散らしてよい。使うなら本家プロハンのnoteに実在する語彙\
（「ワンネス」「アカシックレコード＝宇宙の記録」「AIは"あい"、神が仕組んだシナリオ」など）を基軸にする。\
※「風の時代」「波動」「光まかせ」等の一般スピ語は本家の語彙ではないので避ける。\
ただしガチのスピリチュアルではなく、「都合のいいところだけ使う明るい遊び」＝スピを笑いながら本気で運用するトーンが正解。占いを重くしすぎず、こずえの下町の軽口でカラッと締める。
3. 交通情報コーナー: こずえの元バスガイド経験がいちばん出るコーナー。traffic.source が "stub" の場合、特定路線の遅延・事故を絶対に捏造しない。「最新の運行情報は各社の公式でご確認くださいね」系の一般口上で締める。\
そのかわり「今日のサービスエリア」を紹介する。\
★取り上げる施設は datapack.sa_of_today で既に決まっている。**自分で施設を選んではいけない。**\
name（名称）・route（所属路線／沿線）・pref+city（所在地）は**実在DBの確定値**なので、一字一句そのまま使い、絶対に言い換えたり別の路線名に書き換えたりしないこと。\
（過去に台本AIが「刈谷ハイウェイオアシスは東名阪自動車道」と書く事故があった。正しくは伊勢湾岸自動車道。路線名は推測で書くと必ず間違える。）\
sa_of_today が null の日は、施設名を出さず一般的な道路の思い出話で処理する。\
語ってよいのは、バスガイド時代の思い出・その土地の雰囲気・季節感といった**検証を要しない語り**。\
逆に、その施設の名物・店名・施設の詳細は datapack に無いので**確証がなければ書かない**。地理の一般知識（県名・地方）は可。
4. 今日の一曲コーナーは datapack.song_of_today を紹介し、「それでは聴いてください——『曲名』」で終える。
5. 台詞は音声合成がそのまま読む。ト書き・括弧書き・記号装飾・改行内の注釈を一切入れない。
6. エンディングは「また明日の朝もここで」系の締め。

# 出力形式（厳守）
次の6セグメントを、この順で、下のマーカー形式だけで出力する。マーカー行と台詞以外の文字（前置き・解説・コードブロック）を一切出力しない。

===SEGMENT: opening===
（台詞）
===SEGMENT: weather===
（台詞）
===SEGMENT: fortune===
（台詞）
===SEGMENT: traffic===
（台詞）
===SEGMENT: song===
（台詞）
===SEGMENT: ending===
（台詞）
"""


def build_prompt(pack: dict) -> str:
    return PROMPT_TEMPLATE.format(
        datapack=json.dumps(pack, ensure_ascii=False, indent=2)
    )


def generate_script_claude(prompt: str, model: str, log: logging.Logger) -> str:
    """claude CLI（headless）で台本生成。本番の全自動運用ではここが心臓部。"""
    exe = shutil.which("claude")
    if not exe:
        raise RuntimeError("claude CLI が見つからない")
    cmd = [exe, "-p", "--model", model]
    log.info("claude -p 起動 (model=%s)", model)
    t0 = datetime.datetime.now()
    r = subprocess.run(
        cmd, input=prompt, capture_output=True, text=True,
        encoding="utf-8", timeout=600,
    )
    dt = (datetime.datetime.now() - t0).total_seconds()
    log.info("claude -p 終了 exit=%s %.1fs stderr=%s", r.returncode, dt, r.stderr[:500])
    if r.returncode != 0 or "===SEGMENT:" not in (r.stdout or ""):
        raise RuntimeError(f"claude -p 失敗: exit={r.returncode} out={r.stdout[:300]!r}")
    return r.stdout


def fallback_script(pack: dict) -> str:
    """claude が使えない時でも番組を落とさないための最低限テンプレ台本。"""
    lines = "。".join(
        f"{r['region']}は{r['text']}、最高{r['temp_max']}度"
        for r in pack["weather"]["regions"]
    )
    return f"""===SEGMENT: opening===
おはようございます。こずえです。{pack['date_ja']}、{pack['weekday_ja']}の朝でございます。今朝もサイバー演歌ブレイクダウンラジオ24、こずえの朝、始めてまいります。
===SEGMENT: weather===
全国のお天気です。{lines}。どうぞお気をつけてお出かけくださいね。
===SEGMENT: fortune===
続いて、こずえの朝占い。今日のところは、十二星座のみなさま、どなたもまずまずの運勢でございます。ラッキーアイテムは、あたたかい飲み物。
===SEGMENT: traffic===
交通情報です。バスガイド時代の癖で、つい道路のことが気になるこずえです。主要路線の最新の運行情報は、お出かけ前に各社の公式でご確認くださいね。
===SEGMENT: song===
それでは今日の一曲です。聴いてください——『{pack['song_of_today']}』。
===SEGMENT: ending===
こずえの朝、そろそろお別れの時間です。また明日の朝も、ここでお会いしましょう。お相手は、こずえでした。
"""


def parse_segments(text: str) -> list[tuple[str, str]]:
    parts = re.split(r"===SEGMENT:\s*(\w+)\s*===", text)
    # parts = [前置き, name1, body1, name2, body2, ...]
    segs = []
    for i in range(1, len(parts) - 1, 2):
        name, body = parts[i], parts[i + 1].strip()
        if body:
            segs.append((name, body))
    if not segs:
        raise RuntimeError("台本にセグメントマーカーが見つからない")
    return segs


# ---------------------------------------------------------------- audio
# こずえ固有声（GPT-SoVITS・ローカルCPU推論）。別venv(Python3.11)なのでサブプロセスで呼ぶ。
KOZUE_PY = Path(r"C:\tts\.venv\Scripts\python.exe")
KOZUE_BATCH = Path(r"C:\tts\kozue_batch.py")


def render_kozue(segs, outdir: Path, log: logging.Logger,
                 refs: dict | None = None) -> list[Path]:
    """こずえ固有声で合成。失敗したら例外を投げて呼び元がedge-ttsへ退避する。

    refs = {セグメント名: (参照wav, その書き起こし)}。★喋り方はGPT-SoVITSでは
    参照音声が決めるので、コーナーごとに差し替えられる（OPだけ元気に等）。
    省略したセグメントは既定の中立的な朗読のまま。"""
    if not (KOZUE_PY.exists() and KOZUE_BATCH.exists()):
        raise RuntimeError("こずえ声の環境が無い")

    wavdir = outdir / "kozue_wav"
    wavdir.mkdir(exist_ok=True)
    jobs, targets = [], []
    for idx, (name, body) in enumerate(segs, 1):
        wav = wavdir / f"seg_{idx:02d}_{name}.wav"
        job = {"text": body, "out": str(wav)}
        ref = (refs or {}).get(name)
        if ref:
            job["ref_wav"], job["ref_text"] = str(ref[0]), ref[1]
            log.info("%s は参照音声を差し替え: %s", name, Path(ref[0]).name)
        jobs.append(job)
        targets.append((wav, outdir / f"seg_{idx:02d}_{name}.mp3"))

    jf = wavdir / "jobs.json"
    jf.write_text(json.dumps(jobs, ensure_ascii=False), encoding="utf-8")

    log.info("こずえ声で合成 (%d本)", len(jobs))
    # ★★子プロセスの標準出力を utf-8 に固定する（2026-09-05 実際に落ちた）。
    #   GPT-SoVITS の TextPreprocessor.pre_seg_text が合成前に `print(text)` していて、
    #   Windowsの既定(cp932)では **cp932 に無い文字が1つでもあると合成ごと落ちる**。
    #   実例: 曲紹介の「それでは聴いてください——『曲名』」の em dash (U+2014)。
    #     UnicodeEncodeError: 'cp932' codec can't encode character '—'
    #   ⚠ em dash を台本から消す直し方ではダメ。cp932 に無い文字なら何でも同じ事故になる
    #     （— … ～ ♪ 絵文字 …）。**入口ではなく出力側を直す。**
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    r = subprocess.run([str(KOZUE_PY), str(KOZUE_BATCH), str(jf)],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=1800, env=env)
    if r.returncode != 0:
        raise RuntimeError(f"こずえ声の合成に失敗: {r.stderr[-800:]}")

    files = []
    for wav, mp3 in targets:
        if not wav.exists() or wav.stat().st_size < 10000:
            raise RuntimeError(f"こずえ声の出力が無い: {wav.name}")
        c = subprocess.run(["ffmpeg", "-y", "-loglevel", "error",
                            "-i", str(wav), "-codec:a", "libmp3lame",
                            "-q:a", "2", str(mp3)],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace")
        if c.returncode != 0:
            raise RuntimeError(f"mp3変換に失敗: {c.stderr[-400:]}")
        files.append(mp3)
    return files


async def render_edge(segs, outdir: Path, log: logging.Logger) -> list[Path]:
    """フォールバック。こずえ声が落ちても番組は落とさない。"""
    import edge_tts
    files = []
    for idx, (name, body) in enumerate(segs, 1):
        path = outdir / f"seg_{idx:02d}_{name}.mp3"
        log.info("tts(edge) %s (%d文字)", path.name, len(body))
        await edge_tts.Communicate(body, VOICE, rate=TTS_RATE).save(str(path))
        files.append(path)
    return files


async def render_audio(segs, outdir: Path, log: logging.Logger) -> list[Path]:
    try:
        return render_kozue(segs, outdir, log)
    except Exception as e:
        log.warning("こずえ声NG → edge-ttsへ退避: %s", e)
        return await render_edge(segs, outdir, log)


def concat_mp3(files: list[Path], out: Path, log: logging.Logger) -> None:
    lst = out.parent / "concat.txt"
    lst.write_text(
        "".join(f"file '{f.as_posix()}'\n" for f in files), encoding="utf-8"
    )
    cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
           "-i", str(lst), "-c", "copy", str(out)]
    log.info("ffmpeg concat -> %s", out.name)
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg 失敗: {r.stderr[-800:]}")


# ---------------------------------------------------------------- publish
def publish_episode(final: Path, pack: dict, engine: str,
                    publish_dir: Path, log: logging.Logger) -> None:
    """完成mp3を配信フォルダへ搬出。RadioDJ がここを取り込み先にする想定。

    - kozue_asa_<日付>.mp3 … 日付つき。★RadioDJのトラックタイプ13
      「Newest From Folder」がフォルダ内の最新を自動で拾う＝これが本線。
      日付でユニークにするのは、転送が失敗した日に前日分が黙って流れるのを防ぐため
      （固定名の上書きだと失敗が無音で通る）。RadioDJは同名の重複取り込みも拒否する。
    - kozue_asa_latest.mp3 … 固定名。トラックタイプ7(VDF)で運用する場合の受け口として残置
    - latest.json          … 監視/デバッグ用マニフェスト（当日の中身が一目で分かる）

    ★書き込みは必ず 一時名 → os.replace。RadioDJ が転送途中の半端なファイルを
      掴むのを防ぐ（os.replace は同一ボリュームならアトミック）。
    """
    publish_dir.mkdir(parents=True, exist_ok=True)
    dated = publish_dir / f"kozue_asa_{pack['date']}.mp3"
    latest = publish_dir / "kozue_asa_latest.mp3"

    def atomic_copy(src: Path, dst: Path) -> None:
        tmp = dst.with_suffix(dst.suffix + ".tmp")
        shutil.copy2(src, tmp)
        os.replace(tmp, dst)

    atomic_copy(final, dated)
    atomic_copy(final, latest)
    manifest = {
        "date": pack["date"],
        "date_ja": pack["date_ja"],
        "weekday_ja": pack["weekday_ja"],
        "song_of_today": pack["song_of_today"],
        "engine": engine,
        "weather_source": pack["weather"]["source"],
        "file_dated": dated.name,
        "file_latest": latest.name,
        "size_bytes": final.stat().st_size,
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
    }
    (publish_dir / "latest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("publish -> %s / %s", dated, latest)


# ---------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser(description="こずえの朝 自動生成")
    ap.add_argument("--no-audio", action="store_true", help="台本生成まで")
    ap.add_argument("--template", action="store_true",
                    help="claudeを呼ばずテンプレ台本を使う")
    ap.add_argument("--model", default="sonnet", help="claude -p のモデル")
    ap.add_argument("--publish", metavar="DIR",
                    help="完成mp3を配信フォルダへ搬出（RADIODJ連携用）")
    ap.add_argument("--traffic-live", action="store_true",
                    help="交通情報を実データで取得（既定OFF。放送時刻が決まり、"
                         "台本に取得時刻を明言させる用意ができてから有効化する）")
    args = ap.parse_args()

    # コンソール出力を utf-8 に固定（無人運用対策）:
    # claudeのエラー文等に cp932 で表せない文字（中黒「·」等）が混じっても
    # ログ出力で UnicodeEncodeError を出さない。daily.log の文字化けも防ぐ。
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    today = datetime.date.today().isoformat()
    outdir = BASE / "episodes" / today
    outdir.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(outdir / "run.log", encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    log = logging.getLogger("kozue")
    log.info("=== こずえの朝 生成開始 (%s) ===", today)

    # 1) 実データ収集
    pack = build_datapack(log, traffic_live=args.traffic_live)
    (outdir / "datapack.json").write_text(
        json.dumps(pack, ensure_ascii=False, indent=2), encoding="utf-8")

    # 2) 台本生成
    prompt = build_prompt(pack)
    (outdir / "prompt.txt").write_text(prompt, encoding="utf-8")
    if args.template:
        script, engine = fallback_script(pack), "template"
    else:
        try:
            script, engine = generate_script_claude(prompt, args.model, log), \
                f"claude -p ({args.model})"
        except Exception as e:  # 全自動運用では番組を落とさないことを優先
            log.error("台本生成失敗→テンプレにフォールバック: %s", e)
            script, engine = fallback_script(pack), "template(fallback)"
    segs = parse_segments(script)
    log.info("台本OK engine=%s segments=%s", engine, [s[0] for s in segs])

    md = [f"# こずえの朝 {pack['date_ja']}（{pack['weekday_ja']}）",
          f"- 生成エンジン: {engine}",
          "- 天気実データ（全国）: " + " / ".join(
              f"{r['region']}{r['text']}{r['temp_max']}°C"
              for r in pack["weather"]["regions"]) +
          f"（{pack['weather']['source']}）",
          f"- 今日の一曲: {pack['song_of_today']}", ""]
    for name, body in segs:
        md += [f"## {name}", body, ""]
    (outdir / "script.md").write_text("\n".join(md), encoding="utf-8")

    # 3) 音声化
    if args.no_audio:
        log.info("--no-audio 指定につきここまで")
        return 0
    files = asyncio.run(render_audio(segs, outdir, log))
    final = outdir / f"kozue_{today}.mp3"
    concat_mp3(files, final, log)
    size = final.stat().st_size
    log.info("=== 完成: %s (%.1f MB) ===", final, size / 1e6)

    # 4) 配信フォルダへ搬出（--publish 指定時。RADIODJ連携の受け渡し口）
    if args.publish:
        try:
            publish_episode(final, pack, engine, Path(args.publish), log)
        except Exception as e:
            log.error("publish 失敗: %s", e)
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
