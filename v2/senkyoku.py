# -*- coding: utf-8 -*-
r"""選曲プール（2026-09-07深夜 プロハンさん要望で新設）。

プロハンさんの要望（LINE 23:35）:
  「全曲リストから完全ランダムではなく、朝番組専用の選曲プールにしたい。
    POWER PLAY / NEW SONGS / CYBER ENKA CLASSICS / MORNING ROTATION を作り、
    名曲・新曲・現在推したい曲を中心に放送したい。
    MORNING NG で朝に合わない曲は外したい。
    比率も曲リストも、こちらで随時変更できる仕組みが理想」
  ＋「曲数を大幅に追加したのでライブラリも増えている」（23:36）

仕組み:
  - VPSの C:\CYBER_ENKA_STREAM\kozue_asa\config\ にテキストを置いてもらう
    （メモ帳で編集→保存するだけ。同期がクラウドへ吸い上げ、翌朝の選曲に効く）
  - 本番はワークフローが R2 の config/ を引いて、ここが読む
  - ★config が無い・空・壊れている → **従来の選曲にそのまま倒れる**（番組は止めない）

ファイル（1行1曲、ファイル名をそのまま。# で始まる行は無視）:
  POWER_PLAY.txt / NEW_SONGS.txt / CLASSICS.txt / MORNING_ROTATION.txt
      … プール。行の形式:  ファイル名.mp3[|曲名のよみ[|歌手のよみ]]
  MORNING_NG.txt … ここに書いた曲は**絶対に選ばれない**（プール外の従来選曲にも効く）
  RATIO.txt      … 「POWER_PLAY 40」のように空白区切りで比率
  library.txt    … VPSが自動で書く tracks_norm の一覧（プロハンさんは触らない）

★選曲は決定論（日付から決まる）。LLMにも乱数にも選ばせない
  （実在しない曲を語らせない・作り直しても同じ結果、の既存方針のまま）。
★1曲目は sec>=280 の制約を維持する（この曲の間におたよりを生成するため）。
  **secが分からない新曲は1曲目に置かない**（安全側）。2曲目にはなれる。
"""
from pathlib import Path

POOL_FILES = ["POWER_PLAY.txt", "NEW_SONGS.txt", "CLASSICS.txt", "MORNING_ROTATION.txt"]
NG_FILE = "MORNING_NG.txt"
RATIO_FILE = "RATIO.txt"
DEFAULT_RATIO = 25          # RATIO.txt に無いプールの比率


def _lines(p: Path) -> list[str]:
    """テキストの実質行。BOM・空行・#コメントを除く。壊れていても落とさない。"""
    try:
        raw = p.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return []
    out = []
    for ln in raw.splitlines():
        ln = ln.strip()
        if ln and not ln.startswith("#"):
            out.append(ln)
    return out


def _hira_to_kata(s: str) -> str:
    return "".join(chr(ord(c) + 0x60) if "ぁ" <= c <= "ゖ" else c for c in s)


def _entry(line: str, by_file: dict) -> tuple[dict, list[tuple[str, str]]]:
    """プールの1行 → (曲dict, 読みペア)。songs.json に無い新曲はファイル名から起こす。

    ★曲は曲名だけで扱わない（同名別歌手が39件ある既存知見）。fileで引く。
    ★新曲は sec=0 で返す＝1曲目の sec>=280 を通らない＝自動で2曲目専用になる。
    """
    parts = [x.strip() for x in line.split("|")]
    fname = parts[0]
    song = by_file.get(fname)
    if song is None:
        stem = fname[:-4] if fname.lower().endswith(".mp3") else fname
        # 局の命名は「曲名_歌手.mp3」。右端の _ で割る（曲名側に _ があっても歌手は守る）
        title, _, artist = stem.rpartition("_")
        if not title:
            title, artist = stem, ""
        song = {"title": title, "artist": artist, "sec": 0, "file": fname}
    yomi = []
    if len(parts) >= 2 and parts[1]:
        yomi.append((song["title"], _hira_to_kata(parts[1])))
    if len(parts) >= 3 and parts[2]:
        yomi.append((song["artist"], _hira_to_kata(parts[2])))
    return song, yomi


def load(base: Path, songs: list[dict], log=print) -> dict:
    """config を読む。返り値:
    {pools: [(名前, 比率, [曲…])…], ng: {ファイル名…}, yomi: [(表記, カタカナ)…],
     new: [songs.jsonに無い曲…]}
    config が無ければ pools は空 → 呼び元は従来選曲に倒れること。
    """
    cfg = base / "config"
    by_file = {s["file"]: s for s in songs if s.get("file")}
    ratio = {}
    for ln in _lines(cfg / RATIO_FILE):
        bits = ln.split()
        if len(bits) >= 2 and bits[1].isdigit():
            ratio[bits[0].removesuffix(".txt")] = int(bits[1])
    ng = {ln.split("|")[0].strip() for ln in _lines(cfg / NG_FILE)}
    pools, yomi, new = [], [], []
    for pf in POOL_FILES:
        name = pf[:-4]
        members = []
        for ln in _lines(cfg / pf):
            song, y = _entry(ln, by_file)
            if song["file"] in ng:
                continue
            members.append(song)
            yomi += y
            if song["file"] not in by_file:
                new.append(song)
        if members:
            pools.append((name, ratio.get(name, DEFAULT_RATIO), members))
    if new:
        # ★読みが未確認の新曲は名指しで残す（黙って誤読させない。ログは毎朝の記録に残る）
        for s in new:
            has_yomi = any(a == s["title"] for a, _ in yomi)
            log(f"★新曲（リスト外）: {s['file']}"
                + ("" if has_yomi else " ⚠読み未記入。曲名を誤読するおそれ"))
    return {"pools": pools, "ng": ng, "yomi": yomi, "new": new}


def pick(day, cfg: dict, legacy_first: list[dict], legacy_all: list[dict],
         excluded1: set, log=print) -> list[dict] | None:
    """プールから今日の2曲を決定論で選ぶ。プールが無ければ None（従来選曲へ）。

    ★比率はプールの選ばれやすさ（重み）。日付を種にした決定論なので、
      同じ日に何度作り直しても同じ2曲になる（既存方針）。
    ★1曲目: sec>=280 かつ song1_excluded に無い曲だけ。該当ゼロのプールは
      次に重いプールへ順に倒れ、それでも無ければ従来の1曲目プールへ。
    """
    if not cfg["pools"]:
        return None
    t = day.toordinal()

    def weighted(seed_prime: int):
        total = sum(w for _, w, _ in cfg["pools"])
        r = (t * seed_prime) % total
        for name, w, members in cfg["pools"]:
            if r < w:
                return name, members
            r -= w
        return cfg["pools"][-1][0], cfg["pools"][-1][2]

    def ok1(s):
        return s["sec"] >= 280 and (s["artist"], s["title"]) not in excluded1

    name1, m1 = weighted(6007)
    cand1 = [s for s in m1 if ok1(s)]
    if not cand1:
        # 重い順に他のプールを試す
        for name, _, members in sorted(cfg["pools"], key=lambda x: -x[1]):
            cand1 = [s for s in members if ok1(s)]
            if cand1:
                name1 = name
                break
    if not cand1:
        log("★どのプールにも1曲目候補（280秒以上）が無い → 従来の1曲目プールから選ぶ")
        # ★ここでも ok1 を通す（legacy_first は検査済みのはずだが、渡し間違いに備える）
        cand1 = ([s for s in legacy_first if s["file"] not in cfg["ng"] and ok1(s)]
                 or [s for s in legacy_first if ok1(s)] or legacy_first)
        name1 = "(従来)"
    a = cand1[(t * 7919) % len(cand1)]

    name2, m2 = weighted(9973)
    cand2 = [s for s in m2 if s["file"] != a["file"]]
    if not cand2:
        cand2 = [s for s in (x for _, _, ms in cfg["pools"] for x in ms)
                 if s["file"] != a["file"]]
        name2 = "(全プール)"
    if not cand2:
        cand2 = [s for s in legacy_all
                 if s["file"] not in cfg["ng"] and s["file"] != a["file"]]
        name2 = "(従来)"
    b = cand2[(t * 4001) % len(cand2)]
    log(f"選曲プール: 1曲目={name1} / 2曲目={name2}")
    return [a, b]


def _test():
    import datetime
    import tempfile
    base = Path(tempfile.mkdtemp())
    (base / "config").mkdir()
    songs = [
        {"title": "長い曲", "artist": "甲", "sec": 300, "file": "長い曲_甲.mp3"},
        {"title": "短い曲", "artist": "乙", "sec": 100, "file": "短い曲_乙.mp3"},
        {"title": "NGな曲", "artist": "丙", "sec": 320, "file": "NGな曲_丙.mp3"},
    ]
    day = datetime.date(2026, 9, 8)
    quiet = lambda *a: None

    # config無し → None（従来選曲へ倒れる）。★明朝の安全はこの1行が担保する
    assert pick(day, load(base, songs, quiet), songs, songs, set(), quiet) is None

    (base / "config" / "POWER_PLAY.txt").write_text(
        "長い曲_甲.mp3\nNGな曲_丙.mp3\n新曲_丁.mp3|しんきょく|てい\n", encoding="utf-8")
    (base / "config" / "MORNING_NG.txt").write_text("NGな曲_丙.mp3\n", encoding="utf-8")
    (base / "config" / "RATIO.txt").write_text("POWER_PLAY 100\n", encoding="utf-8")
    cfg = load(base, songs, quiet)
    # NGはプールから消える。新曲はファイル名から起きて sec=0
    files = {s["file"] for _, _, ms in cfg["pools"] for s in ms}
    assert files == {"長い曲_甲.mp3", "新曲_丁.mp3"}, files
    nw = cfg["new"][0]
    assert (nw["title"], nw["artist"], nw["sec"]) == ("新曲", "丁", 0), nw
    # 読みはカタカナ化される（ひらがなで書かれても）
    assert ("新曲", "シンキョク") in cfg["yomi"] and ("丁", "テイ") in cfg["yomi"], cfg["yomi"]

    for k in range(14):     # 2週間: 1曲目は常に280秒以上、NGは絶対に出ない
        d = day + datetime.timedelta(days=k)
        a, b = pick(d, cfg, songs, songs, set(), quiet)
        assert a["sec"] >= 280, a
        assert a["file"] != "NGな曲_丙.mp3" and b["file"] != "NGな曲_丙.mp3"
        assert a["file"] != b["file"]
    # 同じ日に2回呼んでも同じ（決定論）
    assert pick(day, cfg, songs, songs, set(), quiet) == \
        pick(day, cfg, songs, songs, set(), quiet)

    # 1曲目候補が全滅 → 従来プールに倒れる
    (base / "config" / "POWER_PLAY.txt").write_text("短い曲_乙.mp3\n", encoding="utf-8")
    cfg2 = load(base, songs, quiet)
    a, b = pick(day, cfg2, songs, songs, set(), quiet)
    assert a["sec"] >= 280, a
    print("★senkyoku: 全部通った")


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    _test()
