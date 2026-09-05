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
MAX_TOKENS = 8000


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

    dt = time.time() - t0
    info = (f"台本生成 {dt:.0f}秒 "
            f"入力{msg.usage.input_tokens} 出力{msg.usage.output_tokens}トークン")
    print(info, flush=True)
    if log:
        log.info(info)

    if "===SEGMENT:" not in text:
        raise RuntimeError(f"台本の形式が違う（先頭300字）: {text[:300]!r}")
    return text


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    print(generate(sys.stdin.read())[:500])
