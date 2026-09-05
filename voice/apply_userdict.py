# -*- coding: utf-8 -*-
"""この番組専用の辞書エントリを GPT-SoVITS のユーザー辞書へ適用する（2026-09-04）。

★なぜ必要か
  GPT-SoVITS は自前のユーザー辞書
  `GPT_SoVITS/text/ja_userdic/userdict.csv`（17MB・英語名辞書が主）を読んでいる。
  番組名のアクセントはそこに登録して初めて狙いどおりになるが、**リポジトリ外**なので
  GPT-SoVITSを更新・入れ直すと消える。エントリの正本はこちら(userdict_entries.csv)に置き、
  この道具で流し込む。

★決まったアクセント（2026-09-04 nordw耳判定・6案から「B」）
  サイバー演歌=平板(0/7) / モーニング=2型(2/5) を**別のアクセント句**に割る。
  韻律記号で `sa[ibaaeNka#mo[o]niNgu`。
  - `#` はアクセント句の切れ目で、**句を割らないと出ない**。割る唯一の手段が
    「モーニング」を **名詞,副詞可能** で登録すること（品詞を振って実測して判明）。
  - 元の状態は `sa[ibaaeNkamo]oniNgu`＝モの直後で下がる形で、nordwの
    「イントネーションがしっくりこない」の正体だった。

⚠ md5ファイルは**消さずに中身を汚す**こと。japanese.py は `open()` で読むので、
   無いと例外→**辞書なしで動く**（候補を変えても結果が変わらない事故になる）。
"""
import os
import sys
from pathlib import Path

import os
# ★本番(Linux)とローカル(Windows)で置き場所が違う。環境変数で切り替える
_GSV = Path(os.environ.get("GSV_DIR", r"C:\tts\GPT-SoVITS"))
CSV = _GSV / "GPT_SoVITS" / "text" / "ja_userdic" / "userdict.csv"
MD5 = CSV.with_name("userdict.md5")
SRC = Path(__file__).resolve().parent.parent / "assets" / "userdict_entries.csv"


def apply() -> list[str]:
    ours = [l for l in SRC.read_text(encoding="utf-8").splitlines() if l.strip()]
    # ★試験で入れた語も必ず消す（2026-09-04 実際に「サイバー」「演歌」が残っていた）。
    #   採用エントリの表記だけを掃除対象にすると、落選案の残骸が辞書に居座る。
    surfaces = {l.split(",")[0] for l in ours} | {
        "サイバー演歌モーニング", "サイバー", "演歌", "モーニング",
        "雷音こずえ", "らいおんこずえ"}   # 一語で登録していた旧エントリを必ず消す
    keep = [l for l in CSV.read_text(encoding="utf-8").splitlines()
            if l and l.split(",")[0] not in surfaces]
    CSV.write_text("\n".join(keep + ours) + "\n", encoding="utf-8", newline="\n")
    MD5.write_text("force-rebuild", encoding="utf-8")   # ★消さない
    return ours


def verify() -> bool:
    """本番の経路（GPT-SoVITSのg2p）で読みとアクセントを確かめる。"""
    os.chdir(str(_GSV))
    sys.path.insert(0, str(_GSV))
    sys.path.insert(0, str(_GSV / "GPT_SoVITS"))
    from GPT_SoVITS.text import japanese as J
    # 期待値はどちらも nordw の耳判定で確定した形（2026-09-04）
    #   番組名 = サイバーエンカまで高く平ら → **モで一度上がってから落ちる**
    #            （サイン波お手本 intne.wav/intne2.wav に合わせ、B→核7/12→R と動いて確定）
    #   名前   = ラ↑イオン こずえ（**「ら」だけにアクセント**。こずえ側は平板）
    #            経緯: こ(1型)→ず(2型)→アクセント無し(平板) と nordw耳判定で3回動いた
    want = {
        "サイバー演歌モーニング": "sa[ibaaeNka#mo]oniNgu",
        # ⚠ 平板の名詞に「です」が付くと**です側で下がる**のが正常（ko[zuede]su）。
        #   期待値を ko[zuedesu と書いて自分で誤検知した。辞書ではなく期待値の間違い。
        "パーソナリティの雷音こずえです": "pa[asona]ritino#ra]ioN#ko[zuede]su",
        "らいおんこずえです": "ra]ioN#ko[zuede]su",
    }
    ok = True
    for text, expect in want.items():
        got = "".join(J.g2p(J.text_normalize(text)))
        hit = got == expect
        ok &= hit
        print(f"{'OK ' if hit else 'NG '} {text} -> {got}" + ("" if hit else f"  (期待 {expect})"))
    return ok


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    for line in apply():
        print("適用:", line.split(",")[0], line.split(",")[-2])
    sys.exit(0 if verify() else 1)
