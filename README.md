# 売上・収益リスク分析 自律AIエージェント — リソース一式

経費不正リスク分析ツールと同じコンセプト・同じ Claude Code ベストプラクティス構成で、**売上・収益の固有リスク**に最適化したプロジェクトリソースです。内部監査・会計監査・コンプライアンス・監査役という4つの専門家視点で実務レベルの機能を検討し、財務諸表アサーション・収益認識基準・循環取引ネットワーク分析まで組み込んでいます。

構成の要点は「Claude の文脈ウィンドウはすぐ埋まり、埋まるほど精度が落ちる」という制約への対処で、**常に効く原則だけを薄い `CLAUDE.md` に置き、深い仕様は作業内容に応じて自動ロードされるスキルへ分離**しています。

## なぜ売上は経費と設計が違うのか

- 経費不正 = **個人が会社を欺く**（統制は概ね有効な前提）。
- 売上不正 = **経営者が外部に対し財務数値を歪める**財務諸表不正で、**統制を無効化できる立場**の者による不正を含む。

監査基準委員会報告書240 が収益認識を不正リスクと推定し、仕訳テストと統制無効化への対応を求めるのはこのため。本ツールは統制の逸脱に加え、統制をすり抜ける取引の実在性・期間帰属・仕訳を重点評価します。

## 監査実務グレードの差別化要素

1. **アサーション紐付け** — 全ルール・全所見が財務諸表アサーション（発生・網羅性・正確性・期間帰属・分類・評価・権利と義務・表示）に紐づく。会計監査の調書にそのまま接続。
2. **カットオフ（期間帰属）主軸** — 受注・出荷・検収・請求・収益認識・入金・返品の各日付を分離保持し三角照合。未出荷計上・締め遅延・期末計上→翌期取消を検出。
3. **エンティティ・ネットワーク分析** — 取引・資金フローをグラフ化し循環取引（A→B→C→A）・買戻し・関連当事者の環流を検出。
4. **仕訳テスト（JE）** — 手動・トップサイドの売上仕訳、決算直近の調整、経営者起票を厚く検知（統制無効化対応）。
5. **収益認識5ステップ対応** — 前倒し計上・総額純額（本人代理人）・進捗操作・変動対価・委託販売。
6. **4つの専門家視点を単一エンジンで** — 内部監査・会計監査・コンプライアンス・監査役に、それぞれの言語で価値を返す。

## このリポジトリの構成

| 層 | 役割 | 読み込まれ方 |
|---|---|---|
| `CLAUDE.md` | 常に効く原則・進め方・対話スタイル | 毎セッション自動 |
| `.claude/skills/*/SKILL.md` | タスク別の深い指示（8本） | 作業内容が説明文に合致した時だけ自動ロード |
| `.claude/settings.json` | 権限スコープ（allow/ask/deny）の雛形 | Claude Code が参照 |
| `docs/*.md` | 深い仕様の一次資料（人間向け） | スキルから参照 |
| `config/**` | 機械可読の定義（ルール・シナリオ・スキーマ） | 実装が読む／編集する |

## スキル（`.claude/skills/`）

| スキル | 発火する作業 |
|---|---|
| `revenue-recognition` | 収益認識・カットオフ・アサーション紐付け・仕訳テストの設計 |
| `analysis-pipeline` | ETL・分析エンジン・ネットワーク分析・コストファネルの実装/変更 |
| `agent-orchestration` | 5フェーズ自律ループ・read-onlyツール・HITLゲートの実装 |
| `rule-authoring` | ルール/不正シナリオの追加・調整（`config/rules/`） |
| `data-contracts` | モジュール間I/O・レポート・監査ログのスキーマ準拠 |
| `security-and-privacy` | 外部証憑の取込・コネクタ入力・個人/取引先データの取扱い |
| `governance-independence` | 独立性・配備レイヤ・4つの専門家視点・モデルリスク・監査ログ |
| `onboarding-explainer` | 非技術者への説明・オンボーディング・用語の平易な解説 |

## 対話スタイル ── 読んでいるだけで理解が深まる

このプロジェクトの Claude は、相手が技術に不慣れでも理解が深まるように話すよう設定してあります（`CLAUDE.md` の「対話と説明のしかた」＋ `onboarding-explainer` スキル）。専門用語は初出でやさしく言い換え、抽象概念は日常のたとえを添えてから正確な定義に進み、「なぜここで必要か」を短く付けます。わかりやすさは正確さへの"足し算"で、厳密さは削りません。

## Claude Code での使い方

1. このフォルダをリポジトリのルートに置く。
2. Claude Code を起動する（`CLAUDE.md` は自動で読まれます）。
3. 実装させたい層を指定する。関連スキルが自動ロードされます。例:
   - 「`config/rules/rule_catalog.yaml` を使ってルールベース評価器を実装して」→ `rule-authoring` / `analysis-pipeline`
   - 「`docs/agent-design.md` の5フェーズループをオーケストレータとして実装して」→ `agent-orchestration`
   - 「循環取引のネットワーク検出を実装して」→ `analysis-pipeline` / `agent-orchestration`
   - 「この仕組みを監査役会に説明する資料を作って」→ `onboarding-explainer`
4. `docs/` が仕様、`config/` が機械可読の定義。コードは未実装（＝自由に生成させる前提のスケルトン）。
5. **定石**: 不確実な設計や複数ファイルにまたがる変更は plan mode で計画してからコードへ。変更後はテスト/スキーマ検証で確認。無関係なタスクに移るときは `/clear`。

## 実装（コード）— `src/revenue_risk/`

`docs/` と `config/` が仕様、`src/revenue_risk/` がその実装です。仕様の各レイヤ（L1〜L7）に対応する
モジュール構成と設計判断は **`docs/implementation.md`** にあります。必須依存は PyYAML / jsonschema / numpy のみ
（scikit-learn・shap・networkx は任意。無ければフォールバックで全機能が動きます）。

```bash
pip install -r requirements.txt

python run.py demo                    # 合成不正18シナリオを混入→検出力(recall=18/18)を実演
python run.py demo --out out/         # 統合レポート(JSON)・経営者/監査役サマリ(MD)・明細(CSV)・監査ログを出力
python run.py run --input data.csv --approve G0 G1
python run.py check-config            # config/ の整合性チェック
python run.py verify-audit out/audit_log.json   # 監査ログのハッシュチェーン検証（checkpoint 照合）

python -m unittest discover -t . -s tests       # テスト（95件）
```

実装が絶対原則（アサーション紐付け・HITL・改ざん不能ログ・インジェクション耐性・コスト・ファネル）を
満たすことは、`tests/` の検出力(recall)・監査ログ整合性・HITL不変条件・インジェクション検出の各テストが自動確認します。

### 実装リポジトリ構成

```
src/revenue_risk/
├── contracts/      # データ契約（4型＋JSON Schema検証）
├── etl/            # L1 取込（GL突合・連番/欠番・データ品質）
├── engines/        # L2 ルール / L3 探索 / L4 ML / L5 ネットワーク
├── agent/          # L6 5フェーズループ・read-onlyコネクタ・HITL・インジェクション防御
├── audit/          # WORM＋ハッシュチェーン監査ログ
├── reporting/      # L7 レポート（JSON/MD/CSV）
├── synthetic/      # 合成不正データ生成（検出力評価）
├── scoring.py / funnel.py / findings.py / pipeline.py / cli.py
config/engagement.yaml            # 独立性・配備レイヤ・強制アクション・HITL・上限
config/rules/detection_params.yaml # 調整可能な検知しきい値
tests/                             # 95テスト（契約・各エンジン・監査・インジェクション・HITL・e2e・回帰）
```

## ファイル一覧

| パス | 役割 |
|---|---|
| `CLAUDE.md` | 常に効く原則・進め方・対話スタイル。**最初に読まれる** |
| `.claude/settings.json` | 権限スコープの雛形（秘密情報の読取は既定で拒否） |
| `.claude/skills/` | タスク別スキル8本 |
| `docs/architecture.md` | システム全体像、分析レイヤ（ネットワーク分析含む）、データフロー、コストファネル |
| `docs/agent-design.md` | 自律ループ、read-onlyツール、HITLゲート、インジェクション対策 |
| `docs/revenue-recognition.md` | 収益認識5ステップ、アサーション枠組み、カットオフ、仕訳テスト |
| `docs/governance.md` | 4つの専門家視点、独立性（Track A/B＋配備レイヤ）、モデルリスク、規制マッピング、RACI |
| `docs/security-privacy.md` | プロンプトインジェクション対策、個人/取引先データの法的基盤 |
| `docs/design-rationale.md` | 設計思想（なぜ監査実務グレードか）|
| `config/rules/rule_catalog.yaml` | 65 ルール × 12 カテゴリ。アサーション・スコア重み・誤検知注記つき |
| `config/rules/fraud_scenarios.yaml` | 18 の売上不正シナリオ（手口・着眼点・検証データ・合成テスト） |
| `config/schemas/data_contracts.json` | 入出力データ契約（JSON Schema） |

## ルールカタログの構成（12カテゴリ）

CUTOFF 期間帰属・カットオフ / FICT 架空売上・実在性 / CIRC 循環取引・関連当事者 / RECOG 収益認識 / RETURN 返品・赤伝・変動対価 / PRICE 価格・数量・粗利 / CREDIT 与信・回収可能性 / CUST 得意先マスタ・スクリーニング / CTRL 統制・承認・職務分掌 / JE 仕訳・トップサイド調整（統制無効化）/ PATT パターン・集中・行動 / COMPL コンプライアンス（贈収賄・独禁・制裁）。
