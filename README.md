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
| **Word 報告書** | 収集記事を整理した .docx を毎朝自動作成（下記）。ページの「Word 報告書」からダウンロード |

## Word 報告書

`scripts/report.py` が収集済みの記事から Word ファイルを作ります（`docs/reports/` に保存、90日分保持）。

| 種類 | 作成タイミング | 対象 |
|---|---|---|
| 日報 | 毎朝 | 直近1日に公開・新規収集された記事 |
| 週報 | 毎週月曜の朝 | 直近7日 |
| 月報 | 毎月1日の朝 | 直近30日 |

手動で作る場合は、ページの「今すぐ更新」または Actions の Run workflow で種類（日報／週報／月報／すべて）を選びます。

**報告書の構成**

1. **概況** … 要点（件数・多かった分野・頻出キーワード・主な媒体）と分野別件数表
2. **岩手・三陸の主要ニュース** … 重要度順。媒体・日時・分野・キーワード、同じ出来事の他媒体記事（関連報道）をまとめて表示
3. **分野別ニュース一覧（全件）** … 収録した全話題を分野ごとの表で掲載。各話題は1分野にだけ載せるので重複なし。★＝岩手・三陸関連
4. **収集状況・注記** … 取得元ごとの取得件数・失敗の有無

- 同じ出来事を複数媒体が報じた記事は1つの「話題」にまとめ、代表記事は岩手日報・朝日新聞・水産経済新聞などを優先します。
- 見出しはクリックで記事ページを開けます。
- 分類には報告書作成時点の `config/keywords.yaml` を使うため、キーワードを直すと過去記事の報告書にも反映されます。
- **AIによる概況まとめ（任意）**：リポジトリの Settings → Secrets and variables → Actions に
  `ANTHROPIC_API_KEY`（Claude API キー）を登録すると、見出し一覧をもとに Claude（claude-opus-5-5）が
  概況を5〜8項目の箇条書きでまとめ、報告書の冒頭に載せます（API 利用料が発生します。未登録なら省略され、それ以外は同じ報告書が作られます）。

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

### ページが 404 になるとき

- 開くURLは **`https://naokiman-8636.github.io/news/`** です（`/docs/` は付けません。`docs` フォルダの中身がサイトの最上位になります）。
- Settings → Pages の「Source」が `Deploy from a branch`、Branch が `main`、フォルダが **`/docs`** になっているか確認します。
  `/(root)` になっていると README が表示されたり、ページが見つからなかったりします。
- 設定を変えた直後や更新直後は、反映まで1〜3分かかります。Actions タブの「pages build and deployment」が緑（成功）になってから開いてください。
- 非公開（Private）リポジトリの場合、無料プランでは Pages を使えません（Settings → General の一番下で Public に変更するか、有料プランが必要です）。

## 更新のしくみ

- `.github/workflows/update-news.yml` が **毎朝 6:07（日本時間）** に起動し、収集と Word 報告書の作成を行います
  （GitHub の定時実行は混雑時に数十分遅れることがあるため、7時までに終わるよう早めに起動しています）。
- 更新結果を main に push すると、GitHub Pages（main ブランチの `/docs` から公開）が自動で再構築されます。
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
scripts/report.py            Word 報告書の作成プログラム
docs/                        表示ページ（GitHub Pages で公開）
  index.html / app.js / style.css / config.js
  data/news.json             最新データ（自動更新）
  data/archive/              日ごとの収集結果
  reports/                   Word 報告書（自動作成）
.github/workflows/update-news.yml   毎朝の自動実行・手動実行
tests/fixtures/              動作確認用のサンプルRSS
```

## 手元での動作確認

```bash
pip install -r requirements.txt
python scripts/collect.py                                   # 実際に収集
python scripts/collect.py --offline tests/fixtures --out /tmp/out   # サンプルRSSで動作確認
python scripts/report.py --days 1 7                         # 日報・週報を作成
cd docs && python -m http.server 8000                       # http://localhost:8000 で表示
```
