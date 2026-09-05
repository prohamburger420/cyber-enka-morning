# -*- coding: utf-8 -*-
"""台本生成（GitHub Actions版）。2026-09-05

★ローカルは `claude -p`（Claude CodeのCLI）を叩いていたが、本番のランナーには
  CLIが無い。**Anthropic APIを直接叩く**形に置き換える。出す指示（プロンプト）は同じ。

★モデルは **claude-sonnet-5**。
  ローカルの運用が `claude -p --model sonnet` だったので、そこに合わせる。
  ⚠ ここを勝手に上げ下げしない。台本の口調は耳判定で詰めてきたもので、
    モデルを変えると出力の癖が変わる。変えるなら聴き比べてから。

★長い出力になるので**ストリーミング**で受ける（タイムアウトを避けるため）。
"""
import os
import sys
import time

MODEL = "claude-sonnet-5"
# ★8000だと足りなかった（2026-09-05 本番1回目で実際に切れた）。
#   7ブロックの日本語台本で出力6600トークン超。占いが尻切れ、エンディングが丸ごと消えた。
#   ⚠ しかも「===SEGMENT: があるか」しか見ていなかったので**切れた台本がそのまま通った**。
#   日本語はトークンを食うので、余裕を持たせる。
MAX_TOKENS = 16000

# ★台本に必ず入っていないといけないブロック。1つでも欠けたら失敗扱いにする。
#   欠けたまま通すと、その日は**そのコーナーが無い番組**が納品される。
#   1回目はエンディング無しの番組がR2に上がった（ワークフローは緑のまま）。
REQUIRED = ("talk1", "traffic", "sa", "news", "mail_fb", "uranai", "ending")


def generate(prompt: str, log=None) -> str:
    """台本を返す。`===SEGMENT:` が無ければ失敗として例外を投げる
    （呼び元がフォールバック台本へ倒せるように）。"""
    import anthropic

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY が無い")

    client = anthropic.Anthropic()
    t0 = time.time()
    with client.messages.stream(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        msg = stream.get_final_message()
    text = "".join(b.text for b in msg.content if b.type == "text")

    # ★★打ち切られていないか必ず見る（2026-09-05 実際に踏んだ）。
    #   max_tokens に当たると**文の途中で終わる**のに、例外にも何にもならない。
    #   「===SEGMENT: が1つでもあるか」だけでは、切れた台本を止められない。
    if msg.stop_reason == "max_tokens":
        raise RuntimeError(
            f"台本が max_tokens({MAX_TOKENS}) で打ち切られた"
            f"（出力{msg.usage.output_tokens}トークン）。フォールバックへ倒す")

    dt = time.time() - t0
    info = (f"台本生成 {dt:.0f}秒 "
            f"入力{msg.usage.input_tokens} 出力{msg.usage.output_tokens}トークン")
    print(info, flush=True)
    if log:
        log.info(info)

    # ★ブロックが全部そろっているかも見る。「切れていない」と「全部ある」は別の条件で、
    #   モデルがコーナーを丸ごと飛ばすこともある。パスAのときだけ検査する
    #   （パスBは mail だけを作るので REQUIRED を満たさない）。
    if "===SEGMENT: talk1===" in text:
        missing = [k for k in REQUIRED if f"===SEGMENT: {k}===" not in text]
        if missing:
            raise RuntimeError(f"台本にブロックが足りない: {missing}。フォールバックへ倒す")

    if "===SEGMENT:" not in text:
        raise RuntimeError(f"台本の形式が違う（先頭300字）: {text[:300]!r}")
    return text


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    print(generate(sys.stdin.read())[:500])
