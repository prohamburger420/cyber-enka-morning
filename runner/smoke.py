# -*- coding: utf-8 -*-
"""環境が組めたかの確認: こずえの声で1文だけ合成する（2026-09-05）。

★ローカルの `C:\\tts\\kozue_tts.py` と同じ設定でなければ意味がない。
  設定値はここに直書きせず、**同じ数字を1か所で持つ**ため runner/tts.py に置く。
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from runner import tts

TEXT = ("おはようございます！九月五日金曜日、サイバー演歌モーニング。"
        "パーソナリティの雷音こずえです！")


def main() -> int:
    # ★納品先は絶対パスで持つ。合成側が chdir するので、相対パスのままだと
    #   別の場所に書かれても気づけない（2026-09-05 実際に gsv/out/ へ落ちていた）。
    out = Path("out").resolve()
    out.mkdir(exist_ok=True)
    t0 = time.time()
    p = out / "smoke.wav"
    tts.synth(TEXT, str(p), corner="talk1")

    # ★番人: 納品先に本当にファイルがあるか。合成の成功と納品の成功は別物。
    if not p.exists():
        print(f"SMOKE_NG 合成は通ったが納品先に無い: {p}")
        return 1
    import soundfile as sf
    d = sf.info(str(p)).duration
    print(f"SMOKE_OK 合成{time.time()-t0:.0f}秒 尺{d:.2f}秒 -> {p}")

    # ★尺の番人。参照音声と書き起こしがズレると**冒頭を食う**事故が起きる
    #   （2026-09-04 実測。5.7秒に縮んで「おはようございます」が消えた）。
    #   正常なら7秒前後。外れたら失敗にして気づけるようにする。
    if not 6.0 <= d <= 9.5:
        print(f"SMOKE_NG 尺が異常（{d:.2f}秒）。参照音声と書き起こしのズレを疑う")
        return 1
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
