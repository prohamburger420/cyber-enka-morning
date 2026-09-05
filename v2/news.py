# -*- coding: utf-8 -*-
"""「今日のニュース」コーナーの材料を集める（2026-09-05 nordw依頼）。

★ジャンルはプロハンさん指定（2026-09-05 LINE）:
    「サイバー演歌周りの動きとか、AI関連のニュースが良さそう」
    「あまり深刻な時事ネタ拾っても困るし」
    「あと、地方のほっこりニュース系」
  nordw「地方のほっこりニュース最高　毒にも薬にもならないやつ　猫駅長とかそういうやつ」
  ∴ 3レーン構成。**1レーン1本ずつ**拾って、こずえが順に触れる。

★設計の原則は collect_sa / collect_traffic と同じ。**LLMに選ばせない・足させない。**
  拾った見出しをそのまま datapack に入れ、プロンプト側は「これ以外の事実を足すな」と言う。

★★NGフィルタは飾りではない（2026-09-05 実測）。
  この日のNHK主要トップは「鹿児島 屋久島町にレベル5土砂災害特別警報 命が助かる行動を」。
  ITmedia AI+ のトップは「RIZAPが謝罪 顧客の個人情報」。
  **どのフィードも、放っておけば深刻な話題を先頭に出してくる。**
  こずえは軽口のキャラなので、災害・事件・訃報にコメントさせると放送事故になる。
  ⚠ フィルタは通り抜けを前提に作る。最後の砦はプロンプト側の
    「重い話題だと感じたら触れずに次へ行け」（generate_v2 の news 節）。

★NHKのカテゴリ番号は当てにならない。`cat2`＝文化・エンタメのはずが、実際に叩いたら
  インフルエンザ・大雨被害・ガソリンスタンドだった（2026-09-05 実測）。番号を信用しない。

★権利の扱い（重要）
  - **見出しをそのまま読み上げない。** こずえは1文で要約して触れ、出典名を口頭で言う。
    事実そのものに著作権はない。逐語の朗読は引用の範囲を越える恐れがある。
  - 本文は取得しない。RSSのタイトルとリンクだけ使う。
  - ⚠ Google News / ITmedia のRSS利用規約は**未確認**。放送前にプロハンさん判断が要る。
    ponytail: 規約未確認のまま実装, 放送を始める前に必ず確認する
"""
import datetime
import logging
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

UA = {"User-Agent": "Mozilla/5.0 (cyber-enka-morning/1.0)"}
TIMEOUT = 20

# ★@cyberenka の channel_id（2026-09-05 にチャンネルページから取得して確認）。
#   YouTubeは公式にRSSを出している。「サイバー演歌周りの動き」の一次ソース。
CYBERENKA_CHANNEL = "UCk0HbpDai30CJt-KJxxSAYQ"
# ★アップは月1〜2回しかない（2026-09-05 時点の最新は 08-19）。14日にすると
#   このレーンがほぼ毎日空になるので30日で拾う。こずえには「先日」と言わせる。
YT_FRESH_DAYS = 30

# ★一度読んだニュースを翌日また読まない仕組み。**これが無いと事故る。**
#   サイバー演歌レーンは月1〜2本しか動きが無いので、放っておくと
#   同じMVの話を1か月毎朝くり返す。ニュースコーナーとして成立しない。
#   台帳は state/news_used.json（R2に置いて実行間で引き継ぐ）。
USED_COOLDOWN_DAYS = 45


def _gnews(q: str) -> str:
    return ("https://news.google.com/rss/search?q="
            + urllib.parse.quote(q) + "&hl=ja&gl=JP&ceid=JP:ja")


# レーン: (表示名, [URL...])  ★上から順に1本ずつ拾う
# ★★2026-09-05 大幅縮小。当初は3レーン（サイバー演歌／AI／ほっこり）だったが、
#   AIとほっこりは**権利で外した**。実際に規約を当たった結果:
#     ITmedia … 「RSSの一部削除等の改変（配信記事・広告情報の文章のみを抜粋しての転載等を
#                含みます）」を禁止。改変利用は個別相談が必要
#                （https://corp.itmedia.co.jp/media/rss_condition/ を目視）
#     Googleニュース … 非商用限定。過去にGoogle自身が第三者のフィード提供に中止要求
#   さらに見出し自体も、著作物性は否定されたが（YOL事件・知財高裁 平成17年10月6日）
#   **同じ判決が民法709条の不法行為を認めて損害賠償を命じている**。
#   毎朝自動で反復継続して営利放送に使うのは、まさにこの判決が問題にした形に近い。
#   ∴ 残すのは**自局のYouTube（＝自分たちのもの）だけ**。
#   コーナーの中身は v2/geinou.py の「電脳芸能ニュース」（創作）が担う。
LANES = [
    ("サイバー演歌", [f"https://www.youtube.com/feeds/videos.xml?channel_id={CYBERENKA_CHANNEL}"]),
]

# ★こずえに触らせない話題。1語でも入っていたら落とす。
#   「軽口をたたくと事故になるか」だけで選ぶ。ニュースの重要度とは無関係。
NG = re.compile("|".join([
    # 人の生死・事件
    "死去|死亡|急逝|訃報|逝去|遺体|殺害|殺人|自殺|遺族|葬儀|告別式|追悼|余命",
    "逮捕|容疑|送検|起訴|判決|裁判|有罪|実刑|詐欺|暴行|わいせつ|盗撮|窃盗|不正|流出",
    "薬物|覚醒剤|大麻|飲酒運転|ひき逃げ|虐待|いじめ",
    # 災害・事故
    "地震|津波|噴火|警報|避難|土砂|豪雨|浸水|台風|被災|災害|停電|断水",
    "事故|墜落|衝突|火災|炎上|爆発|けが|重体|重傷|感染|流行|ウイルス",
    # 政治・戦争
    "戦争|侵攻|空爆|爆撃|ミサイル|テロ|紛争|停戦|安否|制裁",
    "内閣|首相|選挙|与党|野党|国会|増税|政局|大臣|議員",
    # 企業・芸能の重い話
    "謝罪|不祥事|パワハラ|セクハラ|ハラスメント|批判|苦言|物議|賠償|リコール|倒産|破産",
    "不倫|離婚|活動休止|引退|解散|降板|病気|入院|休養|裁判",
]))

# Google News の見出しは「本文 - 媒体名」。媒体名を出典に回して本文だけ残す。
_GN_SRC = re.compile(r"\s+-\s+([^-]{2,20})$")


def _get(url: str, tries: int = 2) -> str:
    """★1回失敗しただけでレーンが丸ごと消えるのを防ぐ（2026-09-05 実際に起きた）。
    テスト中に同じフィードを数分で5回叩いたら HTTPError が返り、
    サイバー演歌レーンが番組から消えた。3秒あけて叩き直したら200。
    ＝フィードが壊れていたのではなく**こちらが叩きすぎていた**。
    本番は朝1回なので起きにくいが、リトライ1回は安いので入れる。
    """
    import time
    last = None
    for i in range(tries):
        try:
            with urllib.request.urlopen(
                    urllib.request.Request(url, headers=UA), timeout=TIMEOUT) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:
            last = e
            if i + 1 < tries:
                time.sleep(3)
    raise last


def _parse(xml: str, lane: str) -> list[dict]:
    """RSS2.0 と Atom(YouTube) の両方から title/link/date を拾う。★本文は取らない。"""
    root = ET.fromstring(xml)
    out = []
    for it in root.iter("item"):                       # RSS 2.0
        t = (it.findtext("title") or "").strip()
        if t:
            out.append({"title": t, "link": (it.findtext("link") or "").strip(),
                        "date": (it.findtext("pubDate") or "").strip(), "lane": lane})
    if not out:                                        # Atom（YouTube）
        ns = "{http://www.w3.org/2005/Atom}"
        for it in root.iter(f"{ns}entry"):
            t = (it.findtext(f"{ns}title") or "").strip()
            ln = it.find(f"{ns}link")
            out.append({"title": t, "link": (ln.get("href") if ln is not None else ""),
                        "date": (it.findtext(f"{ns}published") or "").strip(), "lane": lane})
    return out


_MONTHS = {m: i for i, m in enumerate(
    "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(), 1)}


def _date(s: str) -> str:
    """pubDate(RFC822) も published(ISO) も 'YYYY-MM-DD' に揃える。読めなければ空。"""
    s = (s or "").strip()
    if re.match(r"\d{4}-\d{2}-\d{2}", s):
        return s[:10]
    m = re.search(r"(\d{1,2})\s+([A-Z][a-z]{2})\s+(\d{4})", s)
    if m and m.group(2) in _MONTHS:
        return f"{m.group(3)}-{_MONTHS[m.group(2)]:02d}-{int(m.group(1)):02d}"
    return ""


def _clean(it: dict, feed_name: str) -> dict:
    """Google Newsの「 - 媒体名」を出典として切り出す。

    ★出典は必ず埋める。空だとこずえが出典を言えず、権利の建て付け
      （「見出しを読まず、要約して出典を言う」）が崩れる。
    """
    m = _GN_SRC.search(it["title"])
    if m:
        it = dict(it, title=it["title"][:m.start()].strip(), source=m.group(1).strip())
    if not it.get("source"):
        it["source"] = feed_name
    it["date"] = _date(it.get("date", ""))
    return it


def load_used(path) -> dict:
    """使用済み台帳を読む。無ければ空（初回・R2から降ってこなかった時）。"""
    import json
    from pathlib import Path
    p = Path(path)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}      # 壊れていても番組は止めない。作り直す


def save_used(path, used: dict, day: datetime.date) -> None:
    """★古い記録は捨てる。放っておくと台帳が永久に伸びる。"""
    import json
    from pathlib import Path
    cutoff = (day - datetime.timedelta(days=USED_COOLDOWN_DAYS * 2)).isoformat()
    keep = {k: v for k, v in used.items() if v >= cutoff}
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(keep, ensure_ascii=False, indent=1), encoding="utf-8")


def _yt_fresh(items: list[dict], today: datetime.date) -> list[dict]:
    """★YouTubeは常に最新10件を返すので、古いアップを『今日の動き』にしてはいけない。"""
    out = []
    for i in items:
        try:
            d = datetime.date.fromisoformat(i["date"][:10])
        except Exception:
            continue
        if (today - d).days <= YT_FRESH_DAYS:
            out.append(dict(i, date=d.isoformat(), source="CEBDR24の公式チャンネル"))
    return out


def _key(title: str) -> str:
    """見出しの同一判定キー。再アップ・表記ゆれを吸収するため記号を落として頭だけ見る。"""
    return re.sub(r"\W", "", title)[:24]


def collect_news(day: datetime.date, log: logging.Logger,
                 state_path=None) -> dict | None:
    """3レーンから1本ずつ。取れなければ None（番組は落とさない）。

    state_path に台帳を渡すと、直近 USED_COOLDOWN_DAYS 日に読んだ話題を避ける。
    """
    used = load_used(state_path) if state_path else {}
    cutoff = (day - datetime.timedelta(days=USED_COOLDOWN_DAYS)).isoformat()
    recent = {k for k, v in used.items() if v >= cutoff}

    picked, dropped, repeats, sources = [], [], [], []
    seen = set()

    for lane, urls in LANES:
        got = None
        for url in urls:
            if got:
                break
            feed_name = "CEBDR24の公式チャンネル" if lane == "サイバー演歌" else (
                "ITmedia" if "itmedia" in url else "各社ニュース")
            try:
                items = _parse(_get(url), lane)
            except Exception as e:
                sources.append({"lane": lane, "status": f"NG {type(e).__name__}"})
                log.warning("news %s: 取得失敗 %s", lane, e)
                continue
            if lane == "サイバー演歌":
                items = _yt_fresh(items, day)
            sources.append({"lane": lane, "status": "OK", "count": len(items)})
            for it in items:
                it = _clean(it, feed_name)
                k = _key(it["title"])
                if not it["title"] or k in seen:
                    continue
                seen.add(k)
                if k in recent:
                    repeats.append(f"[{lane}] {it['title']}")
                    continue          # ★前に読んだ話題。同じ話を毎朝くり返さない
                if NG.search(it["title"]):
                    dropped.append(f"[{lane}] {it['title']}")
                    continue
                got = it
                break
        if got:
            picked.append(got)
            log.info("news %s: %s", lane, got["title"][:44])
        else:
            # ★黙って欠けさせない。レーンが空でも番組は続ける
            log.info("news %s: 今日は無し（コーナーはこのレーンを飛ばす）", lane)

    for t in dropped[:12]:
        log.info("news 除外（重い話題）: %s", t[:52])
    for t in repeats[:6]:
        log.info("news 除外（前に読んだ）: %s", t[:52])

    if not picked:
        log.warning("news: 使える見出しが1件も無い → コーナーごと一般口上に倒す")
        return None

    if state_path:
        for it in picked:
            used[_key(it["title"])] = day.isoformat()
        save_used(state_path, used, day)

    return {"asof": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
            "items": picked, "sources": sources,
            "dropped": len(dropped), "repeats": len(repeats)}


if __name__ == "__main__":
    import sys
    from pathlib import Path
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")     # ★これが無いとログが化ける（cp932）
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    state = Path(__file__).resolve().parent.parent / "state" / "news_used.json"
    n = collect_news(datetime.date.today(), logging.getLogger("news"),
                     state_path=None if "--no-state" in sys.argv else state)
    if n:
        print(f"\n=== 取得 {n['asof']} / 重い話題で除外 {n['dropped']}件 / "
              f"前に読んだので除外 {n['repeats']}件 ===")
        for i in n["items"]:
            print(f"[{i['lane']}] {i['title']}")
            print(f"      出典: {i['source']}  {i.get('date','')}")
    # ★壊れたら落ちる最小のチェック（ponytail方針: 非自明な分岐にだけ1個）
    assert _date("Fri, 04 Sep 2026 10:00:00 +0900") == "2026-09-04"
    assert _date("2026-08-19T12:00:00+00:00") == "2026-08-19"
    assert NG.search("巨大カボチャの収穫") is None
    assert NG.search("RIZAPが謝罪") is not None
