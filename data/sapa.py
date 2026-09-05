#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""「今日のサービスエリア」用の事実引き。路線名をLLMに書かせないための入口。

    from data.sapa import pick_for_date, fact_block
    f = pick_for_date(datetime.date.today())
    prompt += fact_block(f)      # ← 台本プロンプトに事実として差し込む

データは build_sapa_db.py が生成する sapa.json（Wikidata / CC0）。
"""
import datetime
import json
import pathlib
import re

_DB = pathlib.Path(__file__).with_name("sapa.json")
_CACHE = None

# ★Wikidataの P361（〜の一部）は「所属路線」専用ではない。道の駅は公園・鉄道駅・
#   表彰リスト（「とちぎの百様」「恋人の聖地」）・隣接施設まで返してくる。
#   そのまま「所属路線」として台本に差すと、直したかった東名阪の事故を別の形で再生産する。
#   ∴ 道路名に見えるものだけを通す。判断は非対称 ——
#   正しい道路を弾いても番組は困らないが、公園名を路線として放送したら事故。
_ROAD = re.compile(
    r"(自動車道|高速道路|有料道路|連絡道路|道路|国道|"
    r"バイパス|新道|海道|スカイライン|アクアライン|橋|線)$"
)
# 名前がSA/PA/道の駅で終わらないものは施設として対象外
# （実在した混入例: 「本輪西展望所」＝展望所、「谷稲葉インターチェンジ」＝IC）
_FACILITY = re.compile(r"(サービスエリア|パーキングエリア|道の駅)$")


def _usable(f):
    """番組で名前を出してよい施設か。route が実在の道路名であることまで確認する。"""
    name = f.get("name") or ""
    route = f.get("route") or ""
    return bool(_FACILITY.search(name) and _ROAD.search(route))


def load():
    global _CACHE
    if _CACHE is None:
        _CACHE = json.loads(_DB.read_text(encoding="utf-8"))["facilities"]
    return _CACHE


def find(name):
    """名前の部分一致で引く。台本に書く前の路線名チェックに使う。"""
    return [f for f in load() if name in (f["name"] or "")]


def usable_pool():
    """放送に出してよい施設だけの、安定した順序のプール。"""
    return sorted((f for f in load() if _usable(f)), key=lambda f: f["id"])


def pick_for_date(day):
    """日付で決まる1件を返す。同じ日なら何度呼んでも同じ、日が変われば変わる。

    ★歩幅を素数にして飛ばす。連番で歩くと Wikidata のID順が名前順と相関するため、
      「日本坂PA(静岡・東名)→翌日 日本平PA(静岡・東名)」のように
      隣県・同一路線が連日で出てしまう（実測で20日中2回発生）。
    """
    pool = usable_pool()
    return pool[(day.toordinal() * 7919) % len(pool)]


def fact_block(f):
    """LLMに渡す事実ブロック。ここに書いてあること以外は書かせない。"""
    where = "".join(x for x in (f.get("pref"), f.get("city")) if x)
    # 道の駅は高速道路の施設ではないので「所属路線」と書くと嘘になる
    label = "沿線" if f["name"].startswith("道の駅") else "所属路線"
    lines = [
        "## 今日のサービスエリア（以下は確定した事実。路線名・所在地は絶対に変えないこと）",
        f"- 名称: {f['name']}",
        f"- {label}: {f['route']}",
        f"- 所在地: {where}",
    ]
    if f.get("kana"):
        lines.append(f"- 読み: {f['kana']}")
    if "ハイウェイオアシス" in f.get("types", []):
        lines.append("- ハイウェイオアシス併設")
    lines.append("- 上記以外の施設名・名物・店名は、確証がなければ書かないこと。")
    return "\n".join(lines)


if __name__ == "__main__":
    # 実際に起きた事故の回帰テスト:
    # 台本AIが「刈谷ハイウェイオアシスは東名阪自動車道」と書いた。正しくは伊勢湾岸自動車道。
    kariya = find("刈谷パーキングエリア")
    assert len(kariya) == 1, kariya
    assert kariya[0]["route"] == "伊勢湾岸自動車道", kariya[0]
    assert "ハイウェイオアシス" in kariya[0]["types"], kariya[0]
    # 隣接するが別路線。最寄りの高速道路で推測すると間違える例。
    assert find("大府パーキングエリア")[0]["route"] == "知多半島道路"

    # 2026-09-04 発見: Wikidata P361 が道路以外を返す件の回帰テスト。
    # これらは route が公園・鉄道駅・表彰リスト・隣接施設なので、プールに入ってはいけない。
    pool_names = {f["name"] for f in usable_pool()}
    for bad in ("道の駅うつのみや ろまんちっく村",   # route=とちぎの百様
                "道の駅まつだいふるさと会館",        # route=まつだい駅
                "道の駅みのかも",                    # route=ぎふ清流里山公園
                "道の駅あきた港",                    # route=恋人の聖地
                "道の駅小松オアシス",                # route=石鎚山サービスエリア
                "本輪西展望所",                      # 展望所（PAではない）
                "谷稲葉インターチェンジ"):           # IC（PAではない）
        assert bad not in pool_names, f"汚れたデータがプールに残っている: {bad}"

    pool = usable_pool()
    assert len(pool) > 400, f"プールが痩せすぎ。フィルタが効きすぎている: {len(pool)}"
    # 全件、route が道路名として通ること
    for f in pool:
        assert _ROAD.search(f["route"]), f
    # 連日で同じ路線が続かないこと（歩幅を素数にした理由）
    d0 = datetime.date(2026, 9, 4)
    seq = [pick_for_date(d0 + datetime.timedelta(days=i)) for i in range(30)]
    dup = [(a["name"], b["name"]) for a, b in zip(seq, seq[1:]) if a["route"] == b["route"]]
    assert not dup, f"連日で同じ路線: {dup}"

    print(fact_block(pick_for_date(datetime.date.today())))
    print("\nOK:", len(load()), "facilities /", len(pool), "usable")
