# 実装仕様 — コードの構成と設計判断

`docs/architecture.md`（仕様）に対応する実装（`src/revenue_risk/`）の地図と、実装上の設計判断を記す。
コードは Python 3.11+、必須依存は PyYAML / jsonschema / numpy のみ。scikit-learn・shap・networkx は
任意（無ければ numpy／同梱アルゴリズムにフォールバックし、全機能が動く）。

## レイヤ → モジュール対応

| 仕様レイヤ | モジュール | 役割 |
|---|---|---|
| L1 ETL | `etl/ingest.py` | 正規化・GL突合・連番/欠番・期間網羅・データ品質フラグ |
| L2 ルール | `engines/rule_engine.py` + `engines/detectors.py` | 65ルールの決定論的評価。アサーション付与 |
| L3 探索 | `engines/exploratory.py` | 得意先・製品・時系列・粗利・集中度(HHI)の要約 |
| L4 ML | `engines/ml_anomaly.py` | 異常スコア(0-100)＋SHAP寄与。IsolationForest 任意 |
| L5 ネットワーク | `engines/network.py` | 循環経路・買戻し・スルー取引・資金還流（CIRC系）|
| スコア/ファネル | `scoring.py` + `funnel.py` | 重み付き統合・critical override・assertion gate |
| L6 エージェント | `agent/orchestrator.py` | 5フェーズループ・read-onlyツール・HITL・インジェクション耐性 |
| 監査ログ | `audit/audit_log.py` | WORM＋ハッシュチェーン・改ざん検知 |
| L7 レポート | `reporting/report.py` | 統合JSON・経営者/監査役サマリ(MD)・明細CSV |
| 契約 | `contracts/models.py` + `contracts/validation.py` | 4型 + JSON Schema 検証 |
| 設定 | `config_loader.py` | rule_catalog / fraud_scenarios / detection_params / engagement |
| 合成不正 | `synthetic/generator.py` | 検出力(recall)評価用のラベル付き模擬不正 |
| 束ね | `pipeline.py` + `cli.py` | 全体オーケストレーションと CLI |

## 設計判断

### 1. ルールは「メタデータ＋しきい値＝config／述語＝コード」に分離
rule-authoring スキルの原則「検知ロジックをコードに直書きしない」を、実務的に次のように解釈した:
- **config**: ルールの存在・`assertion`・`severity`・`base_weight`・誤検知注記（`rule_catalog.yaml`）と、
  調整可能な数値しきい値（`detection_params.yaml`）。**スコア・アサーション・較正はコードを触らず変更できる。**
- **コード**: 65個の異種な検知述語（日付三角照合・ネットワーク探索等）は `detectors.py` に rule_id 紐付けで実装。
これにより監査人/モデルリスク管理は severity・重み・しきい値を宣言的に保守でき、変更履歴も追える。
`test_config_integrity.py` が「全ルールに detector か network 実装が存在する」ことを保証する。

### 2. エージェントは決定論（LLM 非依存）で動く
5フェーズループ・ツール起動判断・HITL 不変条件・インジェクション耐性を、外部LLMなしに検証可能な形で実装した。
実運用で LLM を差す場合も、**ツールは計画側の判断でのみ起動**し、**証憑コンテンツからは決して起動しない**という
不変条件は `orchestrator.py` が保持する。これによりコスト設計（ファネル）とテスト容易性を両立する。

### 3. HITL 不変条件をコードで強制
`RiskFinding.set_hitl_status(status, actor_is_human)` は confirmed/dismissed を人間以外が設定すると例外を投げる。
エージェントは `in_review` までしか進められない（`test_contracts.py` / `test_agent_hitl.py`）。

### 4. 拡張フィールド
`SalesTransaction` はスキーマの全項目に加え、検知に効く任意フィールド（`unit_cost`・`posting_date`・
`poster_role`・`screening_status`・`registry_verified` 等）を持つ。スキーマは additionalProperties を禁じて
いないため準拠のまま。無ければ該当検知は静かにスキップし、母集団評価は続行する。

### 5. コネクタ依存の検知
外部照会が本質的なもの（反社/制裁・登記・銀行入金・通信のサイドレター）は、決定論層では利用可能なフィールドで
候補化し、L6 エージェントの read-only コネクタ（本番は差し替え、同梱は `MockConnectorProvider`）で証憑を収集・検証する。
`COMPL-003`（談合・入札データ）や `PATT-001`（目標/予算データ）は入力に該当データが無い限り発火しない（`[]` を返す）。

## 実行と検証

```bash
pip install -r requirements.txt            # PyYAML / jsonschema / numpy

python run.py demo                          # 合成不正18種を混入→検出力(recall)を表示
python run.py demo --out out/               # レポート一式(JSON/MD/CSV/監査ログ)を出力
python run.py run --input data.csv --approve G0 G1
python run.py check-config                  # config 整合性
python run.py verify-audit out/audit_log.json

python -m unittest discover -t . -s tests   # テスト（74件）
```

検証手段（「動いた」の証拠）: 合成不正の検出力（recall = 18/18）、監査ログのハッシュチェーン整合性、
HITL 不変条件、インジェクション検出・矛盾検出。これらは `tests/` が自動で確認する。
