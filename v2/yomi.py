# -*- coding: utf-8 -*-
"""TTSに渡す直前の読み替え層＋読み検査ツール。

★なぜ辞書(userdict.csv)でなくテキスト置換なのか（2026-09-04 の判断）
  pyopenjtalk のユーザー辞書は「その表記を常にこう読む」という**全文一律**の登録。
  - 固有名詞には最適: 「らいおんこずえ＝ライオンコズエ(平板)」は辞書で正しく直った
  - ★文脈で読みが変わる語には使えない: 「分」を辞書で「ブン」に固定したら
    「五分(ゴフン)」「半分(ハンブン)」「大分(オオイタ)」まで壊れる
  → **周囲の文字を含めた文字列単位で置換**する。台本(人が読む用)は漢字のまま、
    TTSに渡す文字列だけ仮名にする。

実測で見つかった誤読（2026-09-04 script_a.md を音素化して目視）:
  「道路ってのはね」→ ドーロッテノ**ハ**ネ   助詞「は」がハのまま
  「あたしの分」    → アタシノ**ワケ**       ブンが正しい
"""
import re
import sys

# (TTSに渡す前に置換する正規表現, 置換後)
# ★必ず「周囲を含めた形」で書く。単語単体で置換すると別の語を壊す
RULES: list[tuple[str, str]] = [
    # --- 助詞「は」がハと読まれる口語形 ---
    # 「〜ってのはね」「〜ってのは」: pyopenjtalkが口語の「の＋は」を助詞と判定できない
    (r"ってのは", "ってのわ"),
    (r"ってのァ", "ってのわ"),
    # 「〜のはね」（体言止め＋は）も同系統。「のは」の直後が「ね/よ/さ」の時だけ置換して
    # 「本のはし(端)」のような語を壊さない
    (r"のは(?=[ねよさ、。])", "のわ"),

    # --- 文脈依存で誤読される漢字（辞書登録してはいけない語） ---
    (r"あたしの分", "あたしのぶん"),
    (r"(?<=の)分(?=[、。は])", "ぶん"),

    # 2026-09-04 実測で発見（script_a/b を音素化して目視）
    (r"縁", "ふち"),          # カリカリの縁 → エン と読まれた。この番組で「えん」は使わない想定
    (r"一択", "いったく"),     # ハンジュクイチヨ と読まれた
    (r"声の出", "声ので"),     # コエノダシ と読まれた

    # --- ★キャラ名（2026-09-04 番組名決定に伴い確認） ---
    # ⚠ ここの当初の理由づけは**誤りだった**ので訂正して残す。
    #   「漢字の雷音こずえはカミナリオトと読まれる＝辞書が効いていない」と書いたが、
    #   それは**素のpyopenjtalkで測った結果**。本番はGPT-SoVITSが自前のローダーで
    #   ja_userdic/userdict.csv を読んでおり、そこには 雷音こずえ→ライオンコズエ 0/7 が
    #   入っていて**正しく効いている**（本番経路のg2pで確認済み）。
    # 置換自体は残す: このファイルの回帰チェックは素のpyopenjtalk経路なので、
    # 置換が無いと検査が通らない。二重の保険にもなる（辞書が飛んでも読みは守られる）。
    (r"雷音こずえ", "らいおんこずえ"),
    (r"雷音(?!こずえ)", "らいおん"),   # 「雷音 Thunder Tone」等の曲名にも効かせる

    # --- ★高速道路名（SA DB由来の確定値。予防層が効かないので置換で潰す） ---
    # 路線名はDBから一字一句そのまま使わせているため、「ひらがなで書け」という
    # プロンプト側の予防（層2）が**原理的に効かない**。置換表で対処するしかない。
    # ただし路線名は**有限の集合**なので、93種類を全数検査して一気に潰した（2026-09-04）。
    # 検査方法: sapa.usable_pool() の route を全部 pyopenjtalk.g2p にかけて目視。
    (r"常磐", "じょうばん"),          # トキワ と読まれた（nordw指摘）
    (r"東京外環", "とうきょうがいかん"),  # トーキョーガイタマキ と読まれた
    (r"札樽", "さっそん"),            # サツダル と読まれた
    (r"三遠南信", "さんえんなんしん"),   # ★音素化が失敗し漢字のまま返っていた

    # --- ★LLMの脱字・誤記の修復（読み間違いとは別種だが、直す場所は同じTTS直前） ---
    # 2026-09-04 実際に放送素材に混入: 「いま書いて送っちょうだいね」（「て」抜け）
    # nordw「こういうのは興ざめをまねく」。音声合成は台本通りに読んだだけで無実。
    # 「〜っちょうだい」は日本語として成立しないので、機械的に「〜ってちょうだい」に直せる。
    (r"([んいちりぎしみび])っちょうだい", r"\1ってちょうだい"),
    (r"送っちょうだい", "送ってちょうだい"),

    # 追記の作法: 誤読・誤記を見つけたらここに1行足し、下の CHECK_WORDS にも入れて
    # 二度と戻らないようにする
]

# ★層3: 危険な漢字のリント（誤読を"耳で見つける"のをやめるための仕組み）
#   複数の読みを持ち、pyopenjtalkが文脈で外しやすい漢字。台本に出たら報告する。
#   ここに載っていても正しく読めていることは多いので、**自動置換はしない**。
#   「要注意箇所を人に見せる」ためのもの。
RISKY = ["縁", "分", "出", "生", "行", "角", "表", "方", "間", "下", "上",
         "後", "目", "気", "一日", "一人", "二人", "何", "大分", "汁", "熟",
         "一択", "重", "空", "開", "続", "省", "強", "弱", "細", "描"]


def lint(text: str) -> list[tuple[str, str]]:
    """危険な漢字を含む行を返す。放送前チェック／誤読の早期発見用。"""
    hits = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("==="):
            continue
        found = [k for k in RISKY if k in line]
        if found:
            hits.append(("・".join(found), line))
    return hits

# 検査で必ず読みを確認する語（回帰チェック）
CHECK_WORDS: list[tuple[str, str]] = [
    ("道路ってのはね", "ノワネ"),
    ("あたしの分、話すわね", "ノブン"),
    ("らいおんこずえです", "ライオンコズエ"),
    ("カリカリの縁がうまい", "フチ"),
    ("半熟一択", "イッタク"),
    ("声の出が違う", "コエノデガ"),
    ("いま書いて送っちょうだいね", "オクッテチョーダイ"),   # 脱字修復の回帰
    ("常磐自動車道", "ジョーバン"),
    ("東京外環自動車道", "ガイカン"),
    ("札樽自動車道", "サッソン"),
    ("三遠南信自動車道", "サンエンナンシン"),
    ("パーソナリティの雷音こずえです", "ライオンコズエ"),   # 漢字表記でも名乗りが崩れないこと
    ("サイバー演歌モーニング", "サイバーエンカモーニング"),
]


def _song_rules() -> list[tuple[str, str]]:
    """曲名・歌手名の読み（プロハンさん記入分）。長い順に並んでいる前提。

    ★曲名は**DB由来の固有名詞**なので、プロンプト側の予防（ひらがなで書け）が
      原理的に効かない。有限集合を全数検査して置換で潰す＝路線名と同じ手当て。
    無ければ空。ファイルが壊れていても合成は止めない。
    """
    global _SONG_CACHE
    if _SONG_CACHE is None:
        try:
            import json
            from pathlib import Path
            # ローカルは data/、本番(GitHub Actions)は R2から取った assets/ に置かれる。
            # 両方見る。見つからなければ空で通す（合成は止めない）。
            root = Path(__file__).resolve().parent.parent
            cands = [root / "data" / "songs_yomi_fixed.json",
                     root / "assets" / "songs_yomi_fixed.json"]
            p = next(q for q in cands if q.exists())
            _SONG_CACHE = [(re.escape(a), b) for a, b in
                           json.loads(p.read_text(encoding="utf-8"))]
        except Exception:
            _SONG_CACHE = []
    return _SONG_CACHE


_SONG_CACHE: list | None = None


def fix(text: str) -> str:
    """TTSに渡す直前に呼ぶ。台本そのものは書き換えない。"""
    # ★曲名・歌手名を先に処理する。長い固有名詞を、一般規則が食う前に確定させる
    for pat, rep in _song_rules():
        text = re.sub(pat, rep, text)
    for pat, rep in RULES:
        text = re.sub(pat, rep, text)
    return text


def readings(text: str) -> str:
    """pyopenjtalkで読み（カタカナ）を返す。放送前の目視チェック用。"""
    import pyopenjtalk
    return pyopenjtalk.g2p(text, kana=True)


def check(verbose: bool = True) -> bool:
    """回帰チェック。過去に直した誤読が戻っていないかを見る。"""
    ok = True
    for src, expect in CHECK_WORDS:
        got = readings(fix(src))
        hit = expect in got
        ok &= hit
        if verbose:
            print(f"{'OK ' if hit else 'NG '} {src}  ->  {got}  (期待: {expect})")
    return ok


def audit(path: str) -> None:
    """台本ファイルを1行ずつ、置換後の読みと並べて出す。放送前の目視監査用。"""
    txt = open(path, encoding="utf-8").read()
    for line in txt.splitlines():
        line = line.strip()
        if not line or line.startswith("==="):
            continue
        fixed = fix(line)
        mark = " *置換あり*" if fixed != line else ""
        print(f"原{mark}: {line}")
        print(f"読    : {readings(fixed)}")
        print()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) > 1:
        audit(sys.argv[1])
    else:
        print("OK" if check() else "NG: 誤読が戻っている")
