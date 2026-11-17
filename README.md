# 水産・漁業ニュース（岩手・三陸版）

岩手県・三陸を中心とした水産業・漁業のニュースを自動で集め、1ページにまとめて表示するツールです。
**毎朝7時ごろ自動更新**され、**「今すぐ更新」ボタンで手動更新**もできます。

## できること

| 機能 | 内容 |
|---|---|
| 自動収集 | 岩手日報・朝日新聞・Yahoo!ニュース・水産経済新聞・みなと新聞ほかから、水産関連の記事見出しを収集 |
| 今朝の注目 | 「岩手・三陸 × 水産」で重要度の高い記事を上位5件表示 |
| 重要度判定 | 地域（岩手の沿岸市町村名など）、漁連・漁協、魚種、テーマのキーワードで点数化し「重要」「注目」を表示 |
| 分野で絞り込み | 漁連・漁協／サケ・マス／サンマ・イカ・回遊魚／養殖／磯根（アワビ・ウニ）／市況・価格／行政・政策／海洋環境・資源／事故・密漁・災害／処理水・風評／担い手・経営 |
| その他の絞り込み | キーワード検索、期間（今日〜30日）、媒体別、岩手・三陸のみ、主要媒体のみ、新着のみ、既読を隠す |
| クリップ | 記事を選んで「選択記事をコピー」→ 朝の回覧メールや報告に貼り付けられる形式でコピー |
| 既読管理 | 開いた記事はグレー表示（ブラウザごとに記憶） |
| 履歴 | 30日分を保持。日ごとの収集結果は `docs/data/archive/` に保存 |

## 初回セットアップ（1回だけ）

1. **このブランチを `main` に取り込む**
   定時実行（schedule）は既定ブランチ（main）でのみ動きます。
2. **GitHub Pages を有効にする**
   リポジトリの Settings → Pages → 「Build and deployment」で
   Source: `Deploy from a branch`、Branch: `main` / フォルダ: `/docs` → Save
   数分後に `https://naokiman-8636.github.io/news/` で見られるようになります。
   ※ 非公開（Private）リポジトリで Pages を使うには GitHub の有料プランが必要です。
3. **Actions の書き込み権限を確認**
   Settings → Actions → General → Workflow permissions が「Read and write permissions」になっていること
   （ワークフロー側でも `contents: write` を指定済み）。
4. **初回の収集を実行**
   Actions タブ → 「ニュース更新」→ Run workflow。2〜5分でページに記事が表示されます。

## 更新のしくみ

- `.github/workflows/update-news.yml` が **毎朝 6:50（日本時間）** に起動し、7時ごろまでに更新を完了します
  （GitHub の定時実行は混雑時に数分〜十数分遅れることがあります）。
- 手動更新は次のどちらかで行えます。
  - ページ右上の「今すぐ更新」→ GitHub の画面で **Run workflow**
  - 「ワンクリック更新を設定する」に Fine-grained トークン（このリポジトリのみ・権限は *Actions: Read and write* だけ）を
    保存すると、ページのボタンから直接実行できます。トークンはそのブラウザ内にのみ保存されます。

## 取得元・キーワードの変更

プログラムを触らずに、設定ファイルの編集だけで調整できます（GitHub 上で直接編集してOK）。

- `config/sources.yaml` … 取得元の追加・削除。RSS がない媒体は Google ニュース検索の `site:ドメイン` で取得しています。
  例：河北新報を追加したい場合
  ```yaml
  - name: 河北新報
    type: google_news
    query: 'site:kahoku.news (岩手 OR 三陸) (漁業 OR 水産 OR 水揚げ)'
    filter: true
  ```
- `config/keywords.yaml` … 地域名・魚種・テーマのキーワードと重み（重要度）。
  例：特定の漁協名や事業名を重視したい場合は `org`（漁連・漁協）の `patterns` に追加します。

## 注意事項

- 収集するのは **見出し・リンク・公開日時** です。本文は各媒体サイトでご確認ください
  （岩手日報・水産経済新聞・みなと新聞などは会員限定の記事があります）。
- Google ニュース経由のリンクは、クリックすると各媒体の記事ページへ転送されます。
- 記事の重要度はキーワードによる自動判定のため、目安としてご利用ください。

## ファイル構成

```
config/sources.yaml          取得元の設定
config/keywords.yaml         分類・重要度のキーワード
scripts/collect.py           収集プログラム（Python）
docs/                        表示ページ（GitHub Pages で公開）
  index.html / app.js / style.css / config.js
  data/news.json             最新データ（自動更新）
  data/archive/              日ごとの収集結果
.github/workflows/update-news.yml   毎朝の自動実行・手動実行
tests/fixtures/              動作確認用のサンプルRSS
```

## 手元での動作確認

```bash
pip install -r requirements.txt
python scripts/collect.py                                   # 実際に収集
python scripts/collect.py --offline tests/fixtures --out /tmp/out   # サンプルRSSで動作確認
cd docs && python -m http.server 8000                       # http://localhost:8000 で表示
```
