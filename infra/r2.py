# -*- coding: utf-8 -*-
"""Cloudflare R2 との受け渡し（2026-09-05）。

用途は3つ:
  1. 資産(assets/)をR2へ置く      … 一度きり。nordwが実行
  2. 資産をR2から取る              … GitHub Actions が毎回実行
  3. 出来た音声をR2へ置く          … GitHub Actions が毎回実行 → VPSのrcloneが拾う

★なぜR2か（2026-09-05 調査して決定）
  - VPSは**外へ出ていくだけ**で済む。ポート開放もファイアウォール変更も不要
  - ⚠ Dropboxは公式にWindows Server非対応と明記されている（使えない）
  - ⚠ Dropbox/Google Driveは短命トークン方式で、期限切れで止まる事故が起きる。
    R2は**固定キー2つ**で完結し期限が無い
  - **転送量が完全無料**。278MBを毎回取りに行っても課金されない

認証は環境変数から取る（GitHub Actions の Secrets がそのまま入る）:
  R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY / R2_ENDPOINT / R2_BUCKET
"""
import argparse
import hashlib
import os
import sys
from pathlib import Path


def client():
    import boto3
    from botocore.config import Config
    missing = [k for k in ("R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY",
                           "R2_ENDPOINT", "R2_BUCKET") if not os.environ.get(k)]
    if missing:
        raise SystemExit(f"環境変数が足りない: {', '.join(missing)}")
    return boto3.client(
        "s3",
        endpoint_url=os.environ["R2_ENDPOINT"],
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
        config=Config(signature_version="s3v4", retries={"max_attempts": 5})), \
        os.environ["R2_BUCKET"]


def _md5(p: Path) -> str:
    h = hashlib.md5()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def push(local: Path, prefix: str) -> int:
    """localの中身を prefix/ 以下へ。中身が同じものは送らない。"""
    s3, bucket = client()
    n = skipped = 0
    for p in sorted(local.rglob("*")):
        if not p.is_file():
            continue
        key = f"{prefix}/{p.relative_to(local).as_posix()}"
        try:                      # ★同じ中身なら送らない（ETagはMD5と一致する・単一PUT時）
            head = s3.head_object(Bucket=bucket, Key=key)
            if head["ETag"].strip('"') == _md5(p):
                skipped += 1
                continue
        except Exception:
            pass
        s3.upload_file(str(p), bucket, key)
        print(f"  上げた {key} ({p.stat().st_size/1048576:.1f}MB)", flush=True)
        n += 1
    print(f"送信 {n}件 / 変化なし {skipped}件")
    return 0


def pull(prefix: str, local: Path) -> int:
    """prefix/ 以下を local へ。既にあって中身が同じものは落とさない。"""
    s3, bucket = client()
    local.mkdir(parents=True, exist_ok=True)
    n = skipped = 0
    token = None
    while True:
        kw = {"Bucket": bucket, "Prefix": f"{prefix}/"}
        if token:
            kw["ContinuationToken"] = token
        r = s3.list_objects_v2(**kw)
        for o in r.get("Contents", []):
            key = o["Key"]
            dst = local / key[len(prefix) + 1:]
            if dst.exists() and o["ETag"].strip('"') == _md5(dst):
                skipped += 1
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            s3.download_file(bucket, key, str(dst))
            print(f"  取った {key} ({o['Size']/1048576:.1f}MB)", flush=True)
            n += 1
        if not r.get("IsTruncated"):
            break
        token = r.get("NextContinuationToken")
    print(f"受信 {n}件 / 変化なし {skipped}件")
    return 0


def prune(prefix: str, days: int) -> int:
    """古い出力を消す。★これをやらないと無料枠10GBが埋まる。"""
    import datetime
    s3, bucket = client()
    limit = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
    r = s3.list_objects_v2(Bucket=bucket, Prefix=f"{prefix}/")
    old = [o["Key"] for o in r.get("Contents", []) if o["LastModified"] < limit]
    for k in old:
        s3.delete_object(Bucket=bucket, Key=k)
        print(f"  消した {k}")
    print(f"{days}日より古い {len(old)}件を削除")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="R2との受け渡し")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p1 = sub.add_parser("push"); p1.add_argument("local", type=Path); p1.add_argument("prefix")
    p2 = sub.add_parser("pull"); p2.add_argument("prefix"); p2.add_argument("local", type=Path)
    p3 = sub.add_parser("prune"); p3.add_argument("prefix"); p3.add_argument("--days", type=int, default=7)
    a = ap.parse_args()
    if a.cmd == "push":
        return push(a.local, a.prefix)
    if a.cmd == "pull":
        return pull(a.prefix, a.local)
    return prune(a.prefix, a.days)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
