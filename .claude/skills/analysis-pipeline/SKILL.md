---
name: analysis-pipeline
description: Use when building or changing the data analysis pipeline — the ETL/ingestion layer, the analysis engines (rule-based, exploratory, ML anomaly detection, entity-network analysis for circular trading), or the cost funnel that pre-filters transactions before the agent deep-dives. Triggers on scoring, the layered engine, data flow, GL reconciliation, or cost/scale design.
---

# 分析パイプライン

売上・収益データを複数の層で評価し、リスクスコアとアサーションを付ける:

1. **ルールベース** — 65ルール（`config/rules/rule_catalog.yaml`）を全件に決定論的適用。各所見にアサーションを付す。
2. **探索的分析** — 得意先・製品・担当・時系列・粗利の可視化で異常を洗い出す。
3. **機械学習（異常検知）** — Isolation Forest / LOF / Autoencoder / PCA で外れ値を数値化。寄与要因は SHAP で提示。
4. **エンティティ・ネットワーク分析（売上特有）** — 取引・資金フローをグラフ化し、循環経路（A→B→C→A）・買戻し・関連当事者の環流を検出。単一明細では見えない循環取引はここで捕まえる。
5. **自律AIエージェント** — 上記を統合し仮説検証（`agent-orchestration` スキル）。

## 絶対に守る: コスト・ファネル
全件（数百万明細）に LLM を直接流してはならない。順序が逆だと破綻する:
- **PHASE 1**: ルール＋ML＋ネットワーク判定を全件に決定論的・低コストで適用し、高リスク明細だけを選別。
- **PHASE 2 以降**: LLM／エージェントの重い探索は、選別後の高リスク部分集合にのみ適用。

選別ライン（例 `risk_score ≥ 70`、critical 発火、実在性・期間帰属の high）は `config/rules/rule_catalog.yaml` の `thresholds`。

## データ品質・完全性（全件主張の裏付け）
「全件を評価した」と言うには母集団が完全である必要がある。取込時に総勘定元帳との突合（`reconciled_to_gl`）・連番/欠番・期間網羅・必須欠損を確認し、欠損は所見に `data_quality` フラグを残す。網羅性（completeness）は売上では簿外・計上漏れの裏返しとして重要。

## 参照
`docs/architecture.md`（§1 レイヤ / §3 データフロー・コスト / §4 データ品質 / §5 ネットワーク分析）。各所見に根拠（違反ルール名 / SHAP寄与）とアサーションを必ず添える。型は `data-contracts` スキル。
