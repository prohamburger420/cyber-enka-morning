# -*- coding: utf-8 -*-
"""読みの二重チェック（2026-09-10 nordw「こういうのの辞書、すでにあって組み込めないかね？」）。

★きっかけ: 「急がば回れ」が **キューガバマワレ** と読まれて放送に乗った。
  固有名詞ではないので、こちらの置換表や辞書に載せる筋の話ではない——
  **一般語の読み間違い**をどう捕まえるか、という問題。

★実測（2026-09-10）:
    pyopenjtalk（GPT-SoVITSが使う辞書）… 急がば → キューガバ ✕
    UniDic（fugashi + unidic-lite）    … 急が[イソガ] ば[バ] ○
  ＝**より新しい辞書なら正しく読める**。ならば「両方で読んで、食い違ったところを疑う」。

★★なぜ自動で置換しないか（ここが肝）
  食い違いをカタカナで上書きするのは簡単だが、**カタカナ置換はアクセント句を壊す**
  （2026-09-08 実測: 歌手名の句割れの主犯がこれだった。13件→7件に減らすのに
    ユーザー辞書へ移す必要があった）。読みは直っても抑揚が壊れたら意味がない。
  → **ここは検出だけ**。直し方は2つあり、どちらも人の目を1回通す:
     (a) ユーザー辞書に入れる（アクセントを指定できる＝推奨）
     (b) 置換表に入れる（読みだけ運ぶ。アクセントは運べない）

使い方:
    python v2/yomi_diff.py <台本.md ...>     # 食い違いを一覧
    python v2/yomi_diff.py --self            # 動作確認（急がば回れ）
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

_TAGGER = None
# ★ここは「食い違って当然」なので黙らせる。数える価値がない。
#   ・記号や空白（読みが無い）
#   ・数字と助数詞（pyopenjtalk側が得意。UniDicは単独の読みしか持たない）
_SKIP = re.compile(r"^[\W\d々〜ー]+$")

# ★★既知の無害な食い違い（2026-09-10 実測で確認）。**どちらも誤読ではない。**
#   UniDicは口語の発音を返すことがある（言う→ユー）。どちらで喋っても自然なので、
#   ここで黙らせる。⚠ 毎日出る警告は読まれなくなる＝本物の誤読を隠す。
#   （表記, pyopenjtalkの読み, UniDicの読み）
_ALLOW = {
    ("いい", "イイ", "イー"),
    ("言う", "イウ", "ユー"),
    ("良い", "ヨイ", "イー"),
}


def _tagger():
    global _TAGGER
    if _TAGGER is None:
        import fugashi
        _TAGGER = fugashi.Tagger()
    return _TAGGER


def _kata(s: str) -> str:
    """ひらがな→カタカナ。比較の土俵を揃えるためだけに使う。"""
    return "".join(chr(ord(c) + 0x60) if "ぁ" <= c <= "ゖ" else c for c in s)


def _norm(kana: str) -> str:
    """比較の土俵を揃える。**表記の流儀の差を消す**。

    ⚠ ここを入れないと使い物にならない（2026-09-10 実測）。素朴に比べたら
      台本2本で113語も出たが、中身は「ドーロ vs ドウロ」のような**同じ読み**ばかりだった。
      長音の書き方が pyopenjtalk は「ー」、UniDic は「ウ/イ」というだけの差。
    """
    s = _kata(kana)
    out = []
    for c in s:
        if out:
            p = out[-1]
            # オ段/ウ段 + ウ → 長音、エ段 + イ → 長音
            if c == "ウ" and p in "オコソトノホモヨロヲゴゾドボポョウクスツヌフムユルグズヅブプュ":
                out.append("ー")
                continue
            # エ段+イ、イ段+イ（キイ＝キー、イイ＝イー）も長音として扱う。
            # ⚠ ここを入れないと「効い キイ / キー」のような**発音の流儀の差**が毎日出て、
            #   警告が形骸化する（個別に許可リストへ足していくとキリがない）。
            if c == "イ" and p in "エケセテネヘメレゲゼデベペイキシチニヒミリギジビピヰ":
                out.append("ー")
                continue
        out.append(c)
    return "".join(out).replace("ヲ", "オ").replace("ヅ", "ズ").replace("ヂ", "ジ")


def _oj_nodes(text: str) -> list[tuple[str, str]]:
    """pyopenjtalk の解析結果を (表記, 読み) で返す。★文脈つきの読み。"""
    import pyopenjtalk
    out = []
    for n in pyopenjtalk.run_frontend(text):
        s = n.get("string", "")
        p = n.get("pron") or n.get("read") or ""
        out.append((s, p.replace("’", "").replace("'", "")))
    return out


def _ud_nodes(text: str) -> list[tuple[str, str]]:
    out = []
    for w in _tagger()(text):
        f = w.feature
        p = getattr(f, "pron", None) or getattr(f, "kana", None) or ""
        out.append((w.surface, "" if p == "*" else p))
    return out


def diffs(text: str) -> list[tuple[str, str, str]]:
    """(表記, pyopenjtalkの読み, UniDicの読み) を、食い違ったぶんだけ返す。

    ★★**同じ範囲どうしで**比べる（2026-09-10 修正）。
      語の切り方が両者で違うので（「急が|ば」と「急|が|ば」）、表記の長さが揃うところまで
      まとめてから比べる。ここが揃っていないと、切り方の差を読みの差として数えてしまう。
    """
    a, b = _oj_nodes(text), _ud_nodes(text)
    out, i, j = [], 0, 0
    while i < len(a) and j < len(b):
        sa, pa = a[i]
        sb, pb = b[j]
        i, j = i + 1, j + 1
        # 表記が揃うまで、短いほうに足していく
        while sa != sb:
            if len(sa) < len(sb) and i < len(a):
                sa += a[i][0]
                pa += a[i][1]
                i += 1
            elif j < len(b):
                sb += b[j][0]
                pb += b[j][1]
                j += 1
            else:
                break
        if sa != sb or _SKIP.match(sa) or not pb:
            continue
        if _norm(pa) != _norm(pb) and (sa, pa, pb) not in _ALLOW:
            out.append((sa, pa, pb))
    return out


def report(paths: list[str]) -> int:
    total = 0
    for p in paths:
        txt = Path(p).read_text(encoding="utf-8")
        # 台本のセグメント見出しは読まない
        txt = re.sub(r"===SEGMENT: \w+===", "", txt)
        d = diffs(txt)
        seen = set()
        rows = [x for x in d if not (x[0] in seen or seen.add(x[0]))]
        print(f"\n=== {Path(p).name}: 食い違い {len(rows)}語 ===")
        for s, o, u in rows:
            print(f"  {s:12s} pyopenjtalk={o:14s} UniDic={u}")
        total += len(rows)
    print(f"\n合計 {total}語")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    if "--self" in sys.argv:
        d = diffs("急がば回れ、って言うでしょ。")
        print(d)
        assert any(s == "急が" and "キュー" in o and u == "イソガ" for s, o, u in d), \
            "★急がば の食い違いを検出できていない"
        print("★yomi_diff: 通った")
        raise SystemExit(0)
    raise SystemExit(report(sys.argv[1:]))
