# -*- coding: utf-8 -*-
"""語尾の偏りを測る（2026-09-12 nordw「〜ね。〜ね。としつこさを感じる」）。

★きっかけ: 9/8 に「一文は15字前後」に変えたら、**文数が増えたぶん語尾の数も増え、
  既定の「〜ね。」が連発**した。耳で気づかれるまで4日かかった。
    実測 script_a 全文: 9/5 → 「〜ね。」31%・最長2連続
                        9/12 → **42%・最長5連続**（文数 68→85本）
  ⚠ **長さの規律を足すと、語尾の単調さが副作用で出る**。長さだけ見ていると気づけない。

★ここは**検出だけ**（yomi_diff と同じ規律）。台本を機械で書き換えない——
  語尾はキャラの声そのものなので、直すのはプロンプト側（COMMON_RULES）。
"""
import re
import sys
from collections import Counter

# 語尾の型。★上から順に判定する（「のよね」は「ね」ではなく「のよね」に数えたいので順序が意味を持つ）
PATS = [
    ("〜ね", re.compile(r"ね[。！]?$")),
    ("〜のよ/わよ/わね", re.compile(r"(のよ|わよ|わね|のよね)[。！]?$")),
    ("〜よ", re.compile(r"(?<![のわ])よ[。！]?$")),
    ("〜さ/でさ", re.compile(r"さ[。！]?$")),
    ("〜わ", re.compile(r"(?<![のだ])わ[。！]?$")),
    ("〜の", re.compile(r"の[。！]?$")),
]
NE_MAX_RATIO = 0.35     # 「〜ね」がこれを超えたら警告（9/5の実測31%が健全な例）
NE_MAX_RUN = 3          # 同じ語尾がこれだけ続いたら警告


def sentences(text: str) -> list[str]:
    text = re.sub(r"===SEGMENT: \w+===", "", text)
    return [s.strip() for s in re.split(r"[。！？]", text) if s.strip()]


def check(text: str) -> tuple[dict, list[str]]:
    """(統計, 警告の一覧) を返す。警告が空なら健全。"""
    ss = sentences(text)
    if not ss:
        return {"n": 0}, []
    kinds, ne_flags = Counter(), []
    for s in ss:
        hit = None
        for name, p in PATS:
            if p.search(s):
                hit = name
                break
        kinds[hit or "その他"] += 1
        ne_flags.append(hit == "〜ね")
    run = best = 0
    for f in ne_flags:
        run = run + 1 if f else 0
        best = max(best, run)
    ratio = kinds["〜ね"] / len(ss)
    stats = {"n": len(ss), "ne": kinds["〜ね"], "ne_ratio": ratio,
             "ne_run": best, "kinds": dict(kinds),
             "avg_len": sum(len(s) for s in ss) / len(ss)}
    warns = []
    if ratio > NE_MAX_RATIO:
        warns.append(f"「〜ね」で終わる文が{ratio:.0%}（{kinds['〜ね']}/{len(ss)}本）。"
                     f"目安は{NE_MAX_RATIO:.0%}まで")
    if best >= NE_MAX_RUN:
        warns.append(f"「〜ね」が{best}文連続している（{NE_MAX_RUN}文続けない）")
    return stats, warns


def _test():
    bad = ("まだバスガイドしててね。お客さんに占いを披露してたのよね。"
           "それが原点なのよね。あの頃の自分に言いたいのよね。")
    st, w = check(bad)
    assert st["ne_run"] >= 3, st
    assert w, "連発を見逃した"
    good = ("十年前のあたしはね、まだバスガイドしててさ。"
            "バスの中で、お客さんに占いを披露してたのよ。"
            "それが今の特技の原点。あの頃の自分に、言ってやりたいわ。")
    st2, w2 = check(good)
    assert not w2, f"健全な文で警告が出た: {w2} {st2}"
    print("★gobi: 通った")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    if "--test" in sys.argv:
        _test()
        raise SystemExit(0)
    from pathlib import Path
    for p in sys.argv[1:]:
        st, w = check(Path(p).read_text(encoding="utf-8"))
        print(f"\n=== {Path(p).name} ===")
        print(f"  文{st['n']}本 平均{st['avg_len']:.1f}字 "
              f"「〜ね」{st['ne']}本({st['ne_ratio']:.0%}) 最長連続{st['ne_run']}")
        print(f"  内訳: {st['kinds']}")
        for x in w:
            print(f"  ★{x}")
