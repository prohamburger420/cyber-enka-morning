# -*- coding: utf-8 -*-
"""できあがった台本を本番のg2pに通して、カタカナの読みを書き出す（2026-09-05）。

★なぜ要るか（ニュースコーナーを足したことで必要になった）
  曲名・歌手名・SA施設名は**有限集合**なので、一度全部読ませて目視すれば潰し切れた
  （806曲の読み検査、106組の歌手名検査）。
  ニュースは違う。**毎朝あたらしい固有名詞が降ってくる無限集合**で、事前検査ができない。
  実測: 「灘崎小迫川分校」→「ナダサキショーハザカワブンコー」（2026-09-05）。

  一次の防御はプロンプト（[news] の (3-a)「固有名詞は一般化して言え」）。
  これはその**二次の防御**で、抜けた時に**気づける**ようにするためのもの。直すためではない。
  ⚠ 事後にしか効かない。だから一次の防御を薄めない。

出力: episodes_v2/<日付>/yomi_check.txt （ブロックごとに 台詞 → カタカナ）

★合成と同じ経路で読む（GPT-SoVITSの辞書込み）。素のpyopenjtalkは別経路で、
  一度これで誤診している（2026-09-04）。os.chdir が要るのはそのため。
"""
import os
import sys
from pathlib import Path

GSV = Path(os.environ.get("GSV_DIR", r"C:\tts\GPT-SoVITS"))


def _kana_fn():
    """本番経路のカナ変換を返す。用意できなければ None（呼び元は黙って飛ばす）。"""
    try:
        os.chdir(GSV)
        sys.path.insert(0, str(GSV))
        sys.path.insert(0, str(GSV / "GPT_SoVITS"))
        from GPT_SoVITS.text import japanese as J   # noqa: F401  辞書を本番と同じ状態に
        import pyopenjtalk
        # ★★置換表(yomi.fix)を必ず通す（2026-09-05 実際にバグっていた）。
        #   本番の合成は yomi.fix() を通してから g2p に渡す。ここを飛ばすと
        #   **本番と違う経路を見ていることになり、検査の意味がなくなる。**
        #   実害: 置換表で直してある語が「誤読」に見え、逆に置換表の穴は見えない。
        #   ⚠ 今日すでに同じ間違いを1度やっている（素のg2pで測って誤読と報告しかけた）。
        #     二次防御の実装でまた同じことをやった。**経路の確認は毎回する。**
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import yomi
        return lambda t: pyopenjtalk.g2p(yomi.fix(t), kana=True)
    except Exception:
        return None


def write(segs, outdir: Path, log) -> Path | None:
    """segs = [(名前, 台詞), ...]。失敗しても番組は止めない。"""
    cwd = os.getcwd()
    try:
        kana = _kana_fn()
        if kana is None:
            log.info("読み検査: g2pが使えないので飛ばす（番組には影響しない）")
            return None
        lines = []
        for name, body in segs:
            lines.append(f"===== {name} =====")
            for s in [x.strip() for x in body.replace("。", "。\n").splitlines() if x.strip()]:
                try:
                    lines.append(f"{s}\n    {kana(s)}")
                except Exception as e:
                    lines.append(f"{s}\n    (読めなかった: {e})")
        p = Path(cwd) / outdir / "yomi_check.txt" if not Path(outdir).is_absolute() \
            else Path(outdir) / "yomi_check.txt"
        p.write_text("\n".join(lines), encoding="utf-8")
        log.info("読み検査を書き出した: %s", p.name)
        return p
    except Exception as e:
        log.warning("読み検査に失敗（番組は続ける）: %s", e)
        return None
    finally:
        os.chdir(cwd)      # ★g2pの読み込みで chdir するので必ず戻す
