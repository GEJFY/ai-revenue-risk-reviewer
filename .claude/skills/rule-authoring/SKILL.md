---
name: rule-authoring
description: Use when adding, editing, or reviewing detection rules or fraud scenarios in config/rules/*.yaml — creating a new rule, tuning severity or weights, defining a fraud scenario, mapping a rule to a financial-statement assertion, or doing false-positive / alert-fatigue tuning.
---

# ルール／シナリオの作成

## 大原則: ルールはコードに直書きしない
検知ロジックは必ず `config/rules/rule_catalog.yaml` に宣言的に定義する（コードは YAML を読んで評価するだけ）。監査人がコードを触らず保守でき、変更履歴も追える。

## rule_catalog.yaml
- スキーマはファイル冒頭 `# schema:` コメントに従う。ID はカテゴリ接頭辞（CUTOFF/FICT/CIRC/RECOG/RETURN/PRICE/CREDIT/CUST/CTRL/JE/PATT/COMPL）＋連番。重複禁止。
- **`assertion` が必須** — 各ルールが、リスクに晒す財務諸表アサーション（occurrence/completeness/accuracy/cutoff/classification/valuation/rights_obligations/presentation）を持つ。これが会計監査の言語と接続する肝。
- **`false_positive_notes_ja`（誤検知の典型と正当な業務理由）も必須** — 誤検知の多いルールは現場で無視され（アラート疲れ）、ツール全体の信頼を損なう。
- `severity`・`base_weight` がスコアに効く。`critical` は未出荷計上（CUTOFF-002）・証憑なし売上（FICT-001）・資金環流（CIRC-003）・循環ネットワーク（CIRC-006）・反社/制裁（CUST-001）・制裁仕向地（COMPL-002）。
- 選別ライン（`high: 70`）と `assertion_gate`（実在性・期間帰属は閾値未満でもレビュー推奨）は `thresholds`（`analysis-pipeline` スキル）。

## fraud_scenarios.yaml
売上不正の手口・着眼点・検証データ・手法を定義。**検知とテストの単一の定義源**（同じシナリオから検知ルールと合成テストを導く）。新シナリオには `linked_rules`（実在するID）と `synthetic_test_ja` を必ず付ける。レッドチーム（SC-RED-01）はインジェクション検出力の評価用。

## 変更後
YAML が壊れていないか（パース＋ID重複＋linked_rules の参照整合）を検証してから終える。
