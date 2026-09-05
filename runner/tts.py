# -*- coding: utf-8 -*-
"""こずえ固有声の合成（GitHub Actions版）。2026-09-05

★ローカルの C:\\tts\\kozue_tts.py の移植。**設定値は必ず一致させる**。
  ここがズレると、聴き比べて決めた音（速度・間・参照音声）が本番で再現されない。

  SPEED=1.10 / FRAGMENT_INTERVAL=0.5 / SPLIT_METHOD="cut2"
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

SPEED = 1.10
FRAGMENT_INTERVAL = 0.5
SPLIT_METHOD = "cut2"

# コーナー別の参照音声（＝喋り方）。BGMの割り当てと対になる設定。
REF_DEFAULT = (ASSETS / "ref" / "RECITATION324_004.wav",
               "ハイチ共和国でツーサンルーベルテュールが勝利を収められたのは"
               "実際大熱病のおかげだった")
REF_BY_CORNER = {
    # OPだけ元気に（2026-09-04 nordw耳判定で5案から014に決定）
    "talk1": (ASSETS / "ref" / "EMOTION100_014.wav",
              "スミスさん、ピエール・デュボワをご紹介しますわ。私の親友なの。"),
}

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
        "return_fragment": False, "parallel_infer": True,
    }))
    sf.write(out_path, audio, sr)
    return out_path
