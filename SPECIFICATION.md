# kabutan_search 再構成仕様書

> **ベース**: 旧 KabutanStockAnalyzer (PySide6デスクトップアプリ) の全機能
> **目的**: サーバー負荷を抑えつつ、同等の分析機能をCLIベースで提供する

---

## 1. 再構成の狙い

| 項目 | 旧構成 | 新構成 |
|---|---|---|
| アプリ形態 | PySide6デスクトップGUI (常駐、埋め込みChromiumブラウザ) | CLIコマンド + レポート出力 (実行時のみ動作) |
| データ収集 | 全銘柄を一律に巡回・自動同期 (日次上限1000件など) | **2段階収集**: 軽量スクリーニング→候補のみ詳細取得 |
| 機能範囲 | 信用需給分析・セクター分析・JPX/EDINET・Gemini連携・お気に入り管理 等 | **全機能維持**、内部構成のみ整理 |
| 出力 | GUIテーブル・チャートダイアログ | HTML/Markdownレポート、画像チャート(PNG) |

**サーバー負荷軽減の核心**: 従来は全追跡銘柄に対して信用残・決算などの重いデータを毎回取得していた。新構成では、まず「値上がり優位性」を軽量指標(株価騰落率・出来高変化など、一覧ページから取れる範囲)だけで判定し、有望と判断された銘柄に絞ってから、初めて信用残・決算などの詳細ページを取得する。これによりkabutan.jpへのリクエスト数を大幅に削減する。

---

## 2. 2段階データ収集フロー

```
Stage 1: スクリーニング (軽量・低頻度アクセス)
  ソース: kabutan.jp/warning/?mode=9_1 (33業種ランキング)、値上がり率ランキングページ等
  取得項目: 銘柄コード・株価・騰落率・出来高変化率 のみ
  判定: 「値上がり優位性」フィルタ (騰落率・出来高急増・セクターモメンタム等の閾値)
  出力: candidate_stocks (当日の候補銘柄リスト)

        ↓ 候補のみ

Stage 2: 詳細取得 (候補銘柄のみ・重いアクセス)
  対象: Stage 1で選ばれた候補銘柄のみ
  取得項目: 信用残・PER/PBR/ROE・決算進捗・年高安 等 (旧parser.pyの全項目)
  → ここで初めて 25列信用需給分析・スクイーズスコア・クオンツフラグ 等を算出
```

- 候補に入らなかった銘柄は詳細取得をスキップ → リクエスト数を「全追跡銘柄数」から「候補数」まで削減
- お気に入り・保有ポジション銘柄は候補選定に関わらず常に詳細取得対象(従来通り監視継続)
- アクセス間隔のランダムディレイ・日次上限などのレート制御ロジックは維持

---

## 3. モジュール構成 (旧→新 対応表)

CLIベースに合わせて `app.py` の Mixin構成(QMainWindow依存)を廃止し、責務ごとの独立モジュール + CLIコマンドに分割する。

| 新モジュール | 旧ファイル | 役割 |
|---|---|---|
| `screening.py` | (新規) | Stage 1: 値上がり優位性スクリーニング |
| `parser.py` | `parser.py` | 株探HTML解析 (維持) |
| `database.py` | `database.py` | `kabutan_stock.db` 管理 (維持) |
| `nikkei_database.py` / `nikkei_fetcher.py` | 同名 | 日経PER・騰落レシオ (維持) |
| `margin_analysis.py` | `margin_analysis.py` | 需給分析ロジック + スコア算出 (GUI依存部分を除去) |
| `stock_quant_metrics.py` | 同名 | クオンツ指標算出 (維持) |
| `sector_data_manager.py` / `sector_divergence_analyzer.py` | 同名 | セクター分析 (維持) |
| `jpx_auto_syncer.py` / `jpx_short_position_syncer.py` / `edinet_holdings_fetcher.py` | 同名 | 外部データ取得 (維持、Stage1/2どちらで呼ぶか要整理) |
| `gemini_prompt.py` | `gemini_prompt.py` + `gemini_dialog.py` の一部 | プロンプト生成 + 応答パース (クリップボード操作はCLI用に置換) |
| `tracker.py` | `tracker_manager.py` | お気に入り・ポジション管理 (CLIコマンド化) |
| `report.py` | `ui_builder.py`, 各`*_dialog.py` | 分析結果をHTML/Markdownレポートに整形して出力 |
| `chart.py` | `chart_dialog.py`, `scatter_dashboard.py` | Matplotlib/Plotlyでチャートを画像/HTMLファイル出力 |
| `cli.py` | `app.py` | エントリーポイント。サブコマンド群を束ねる |

**廃止するもの**: `ui_builder.py` のQSS/GUIレイアウト、`QWebEngineView`埋め込みブラウザ、`bg_sync_engine.py` の常駐自動巡回(→Stage1/Stage2の明示実行に置換)、`QThread`ベースの非同期ワーカー(→CLI実行なので同期処理でよい)。

---

## 4. CLIコマンド構成 (案)

```
kabutan screen                  # Stage1: 当日の値上がり優位性候補を抽出
kabutan analyze [--all|--watchlist]  # Stage2: 候補(または監視銘柄)の詳細取得+需給分析
kabutan report [--format html|md]    # 直近analyzeの結果をレポート出力
kabutan chart <code>            # 個別銘柄チャート画像を生成
kabutan gemini-prompt           # Gemini分析用プロンプトをテキスト出力
kabutan gemini-import <file>    # Geminiの応答を貼り付けたファイルを読み込み記録
kabutan watch add/remove/list <code>   # お気に入り・ポジション管理
kabutan sector                  # セクターモメンタム・需給レポート
```

各コマンドは実行して終了する(常駐しない) → cron/タスクスケジューラでの定期実行にも向く。

---

## 5. データベース

`kabutan_stock.db` / `nikkei_data.db` のテーブル構成は旧仕様をそのまま踏襲(スキーマ変更なし)。追加として:

- `candidate_stocks` テーブル(新規): Stage1の当日候補リストを保存(date, code, screening_score等)

---

## 6. 出力レポート

- `analyze`実行後、`reports/YYYY-MM-DD.html` (または `.md`) を生成
- 内容: 需給分析テーブル(旧25列相当)、スクイーズスコア、クオンツフラグ、セクターシグナル、Geminiプロンプト用テキストを1ファイルに集約
- チャートはPNGとして`reports/charts/`配下に保存し、レポートからリンク

---

## 7. 段階的な実装ステップ (提案)

1. `database.py` / `nikkei_database.py` を移植(スキーマ維持)
2. `parser.py` を移植
3. `screening.py` を新規実装(Stage1)
4. `margin_analysis.py` からGUI依存を除いたロジック部分を移植(Stage2の中核)
5. `report.py` でHTML/Markdown出力
6. `tracker.py` (お気に入り/ポジション) + `cli.py` の基本コマンド
7. セクター分析・JPX/EDINET・Gemini連携を順次移植
8. `chart.py` (画像出力チャート)

---

## 8. 未確定事項 (要確認)

- 「値上がり優位性」の具体的な閾値(騰落率何%以上、出来高何倍以上等)
- Stage1で使う一覧ページのソース(値上がり率ランキング / セクターランキング / 両方か)
- 実行言語・環境: Pythonを継続する前提で記載(旧資産の再利用を優先)
