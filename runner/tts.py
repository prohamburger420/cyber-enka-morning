# -*- coding: utf-8 -*-
"""こずえ固有声の合成（GitHub Actions版）。2026-09-05

★ローカルの C:\\tts\\kozue_tts.py の移植。**設定値は必ず一致させる**。
  ここがズレると、聴き比べて決めた音（速度・間・参照音声）が本番で再現されない。

  ★値は v2/voice_config.py にある（ここには写さない）。
    ★cut5（読点ごとに切って貼る）には戻さないこと。読点のたびに間が空き、
      断片ごとに抑揚がリセットされて「流暢でない」音になる（2026-09-04 nordw耳判定）。

★参照音声はコーナーごとに変える。GPT-SoVITSでは**喋り方は参照音声が決める**
  （音色は重みに焼いてある）。OPだけ元気にするための仕組み。

⚠⚠ 参照音声とその書き起こしは**必ず対で、全文**であること。
   書き起こしが足りないと、モデルが辻褄合わせに**合成文の冒頭を食う**。
   実測: 「私の親友なの。」を書き落としていたせいで
   「おはようございます！」が5回中5回消えた（2026-09-04）。
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GSV = ROOT / "gsv"
ASSETS = ROOT / "assets"

# ★★設定は v2/voice_config.py が正本（2026-09-05）。ここに数値を書かない。
#   以前はここに独立した写しがあり、**実際に食い違った**:
#   ニュース用の参照音声をローカルにだけ足していて、本番は既定の朗読のままだった。
#   ＝nordwの「ニュースのテンションを1段階上げよう」が本番で効いていなかった。
sys.path.insert(0, str(ROOT))
from v2 import voice_config as VC          # noqa: E402

SPEED = VC.SPEED
FRAGMENT_INTERVAL = VC.FRAGMENT_INTERVAL
SPLIT_METHOD = VC.SPLIT_METHOD
SEED = VC.SEED
PARALLEL_INFER = VC.PARALLEL_INFER
# コーナー別の参照音声（＝喋り方）。ファイル名だけ正本から取り、置き場所はこちらで結合する
_REFS = VC.resolve(ASSETS / "ref")
REF_DEFAULT = _REFS[""]
REF_BY_CORNER = {k: v for k, v in _REFS.items() if k}

_tts = None


def get_tts():
    global _tts
    if _tts is None:
        os.chdir(GSV)
        sys.path.insert(0, str(GSV))
        sys.path.insert(0, str(GSV / "GPT_SoVITS"))
        from GPT_SoVITS.TTS_infer_pack.TTS import TTS, TTS_Config
        cfg = TTS_Config({"custom": {
            "device": "cpu", "is_half": False, "version": "v2Pro",
            "t2s_weights_path": str(ASSETS / "models" / "kozue-e15.ckpt"),
            "vits_weights_path": str(ASSETS / "models" / "kozue_e10_s480.pth"),
            "bert_base_path": "GPT_SoVITS/pretrained_models/chinese-roberta-wwm-ext-large",
            "cnhuhbert_base_path": "GPT_SoVITS/pretrained_models/chinese-hubert-base",
        }})
        _tts = TTS(cfg)
    return _tts


def _fix_yomi(text: str) -> str:
    """読み替え層。無くても合成は止めない（番組を落とさない）。"""
    try:
        sys.path.insert(0, str(ROOT / "v2"))
        import yomi
        return yomi.fix(text)
    except Exception:
        return text


def synth(text: str, out_path: str, corner: str | None = None,
          speed: float = SPEED) -> str:
    import soundfile as sf
    # ★★出力先は get_tts() の**前に**絶対パスへ直すこと（2026-09-05 実測でバグ）。
    #   get_tts() は GPT-SoVITS のフォルダへ chdir する。その後に相対パスを解決すると
    #   gsv/out/ に書かれ、合成は成功しているのに**納品先には何も無い**状態になる。
    #   しかも合成側は同じ相対パスで読めてしまうので、検査もすり抜ける。
    out_path = str(Path(out_path).resolve())
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    ref_wav, ref_text = REF_BY_CORNER.get(corner or "", REF_DEFAULT)
    tts = get_tts()
    sr, audio = next(tts.run({
        "text": _fix_yomi(text), "text_lang": "ja",
        "ref_audio_path": str(ref_wav), "prompt_text": ref_text, "prompt_lang": "ja",
        "top_k": 5, "top_p": 1, "temperature": 1,
        "text_split_method": SPLIT_METHOD, "speed_factor": speed,
        "fragment_interval": FRAGMENT_INTERVAL,
        "return_fragment": False, "parallel_infer": PARALLEL_INFER,
        # ★seedを固定しないと同じ台本でも毎回違う音が出る（既定 -1 ＝ランダム）。
        #   ローカルと同じ値にすること。voice_config.py が正本。
        "seed": SEED,
    }))
    sf.write(out_path, audio, sr)
    return out_path
