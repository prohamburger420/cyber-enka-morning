# -*- coding: utf-8 -*-
"""材料が空のとき、歯止めがプロンプトに載っているかの実測（2026-09-10）。

★おたよりの事故と同じ形＝「材料が空 → モデルが埋める」を、コーナーごとに潰したか見る。
  ⚠ 「書いたつもり」で終わらせない。**組み上がったプロンプトの中身**を検査する。
"""
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
D = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(D))
sys.path.insert(0, str(D / "v2"))
sys.path.insert(0, str(D / "data"))
import generate_v2 as v2  # noqa: E402

import datetime  # noqa: E402
# ★実データで組む（作り物のdatapackで検査すると、本番と違う形を見てしまう）
pack = v2.build_pack(datetime.date.today(), __import__("logging").getLogger("audit"),
                     traffic_live=False)


def prompt_for(p):
    return v2.PROMPT_A.format(character="（略）", rules=v2.COMMON_RULES,
                              datapack=json.dumps(p, ensure_ascii=False, indent=2))


ok = True


def check(name, cond, msg):
    global ok
    ok &= bool(cond)
    print(f"{'OK ' if cond else '★NG'} {name}: {msg}")


# 1) geinou が null でも、禁止リストがプロンプトに残るか
from v2 import geinou  # noqa: E402
banned = geinou.banned_names()
check("禁止リストが取れる", banned, f"{len(banned)}件（例: {banned[:2]}）")

p = dict(pack)
p["geinou"] = None
p["出してはいけない名前"] = banned          # build_pack が常に入れる形を再現
pr = prompt_for(p)
check("geinou=null でも禁止リストがプロンプトにある",
      banned and all(b in pr for b in banned),
      "全員ぶんの名前が載っている" if banned else "禁止リストが空")
check("材料なしモードの指示がある", "材料が無い日は、出来事を一切書かない" in pr,
      "ニュースの一般口上への倒し方が書いてある")

# 2) 天気が空のとき
p2 = dict(pack)
p2["weather"] = {"source": "open-meteo.com", "regions": []}
check("天気0件の歯止め", "空の日は天気に触れない" in prompt_for(p2),
      "それらしい天気を作らない指示がある")

# 3) SAが null のとき（既存の指示が生きているか）
p3 = dict(pack)
p3["sa_of_today"] = None
check("SA null の歯止め", "sa_of_today が null の日は" in prompt_for(p3),
      "施設名を出さない指示がある")

# 4) おたより（パスB）: 0通なら呼ばれない＋プロンプトにも禁止が書いてある
prb = v2.PROMPT_B.format(character="（略）", rules=v2.COMMON_RULES,
                         theme="テーマ", mails="- Aさん: あああ")
check("おたよりの捏造禁止", "1文字も創作しない" in prb, "実在の人が書いたもの、と明記")

print("\n" + ("★全部通った" if ok else "★通っていないものがある"))
sys.exit(0 if ok else 1)
