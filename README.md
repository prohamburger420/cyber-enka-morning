# cyber-enka-morning（公開リポジトリに置く中身）

CEBDR24の朝番組「サイバー演歌モーニング」を毎朝自動生成する仕組み。

★このフォルダの中身だけが公開リポジトリへ行く。
  番組固有の中身（声のモデル・曲リスト・キャラ設定）は**一切含まない**。
  それらはCloudflare R2に置き、実行時に取りに行く。
  → 分離の理由と一覧は `../assets/README.md`

## なぜ公開リポジトリなのか
GitHub Actionsは**公開リポジトリなら実行時間が無制限で無料**
（確認: https://docs.github.com/en/billing/.../about-billing-for-github-actions ）。
非公開だと月2,000分で、試算で月900分＝半分近く埋まる。
放送を増やしたりやり直したりする余地が無くなるので公開を選んだ。

## 動きかた

```
6:00 の少し前  Actions起動
   ├ GPT-SoVITS を固定コミットで取得（キャッシュ）
   ├ R2から runtime/（共通モデル1.0GB）と assets/（声・曲リスト等278MB）を取得（キャッシュ）
   ├ パスA: 本編の台本と音声を作る
   └ R2の out/ へ置く  ──→ VPSのrcloneが1分おきに拾う ──→ RadioDJが再生

放送中（曲1が流れている間）
   ├ パスB: YouTubeのチャットを拾っておたよりを作る
   └ R2の out/ へ置く
```

## 必要なSecrets（リポジトリの Settings → Secrets and variables → Actions）

| 名前 | 中身 |
|---|---|
| `R2_ACCESS_KEY_ID` | R2のアクセスキーID（読み書き可のもの） |
| `R2_SECRET_ACCESS_KEY` | 同シークレット |
| `R2_ENDPOINT` | `https://<アカウントID>.r2.cloudflarestorage.com` |
| `R2_BUCKET` | `cyber-enka-morning` |
| `ANTHROPIC_API_KEY` | 台本生成に使う |

⚠ 公開リポジトリでもSecretsは公開されない。かつフォークからの変更では読めない仕組みなので、
   鍵が漏れる経路はない。

## 手元での確認

```
python -m runner.smoke      # 1文だけ合成してR2へ置く（環境が組めたかの確認）
```
