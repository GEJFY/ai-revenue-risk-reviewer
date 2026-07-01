---
name: data-contracts
description: Use when defining or changing data passed between modules — SalesTransaction inputs (multi-date fields for cutoff), RiskFinding outputs (with assertion mapping), Evidence records, or AuditLogEntry — or when implementing ETL output, the report schema, or the audit log. Ensures everything conforms to the JSON Schema.
---

# データ契約（モジュール間 I/O）

すべての受け渡しは `config/schemas/data_contracts.json`（JSON Schema）に準拠。スキーマ違反は実行時に検証し、所見に `data_quality` フラグを付す。4つの型:

- **SalesTransaction** — 入力の売上取引。受注→出荷→検収→請求→収益認識→入金→返品 を1レコードで追跡。**日付フィールドを分離保持**（order/ship/delivery/invoice/revenue_recognition/reversal date）＝カットオフ三角照合の要。`entity_id` は循環取引ネットワーク分析に必須。`gross_net_indicator`・`performance_obligation_status`・`source_system`（manual/system）が収益認識・仕訳リスクの判定に効く。
- **RiskFinding** — 出力の所見。**`assertion`（財務諸表アサーション）と `rationale`（根拠）が必須**。AIは `hitl_status` を自分で confirmed/dismissed にできない（確定は人間のみ）。`transaction_ids` は複数可（循環取引は複数取引にまたがる）。
- **Evidence** — 収集した証憑。出所（`provenance`）・法的基盤（`legal_basis`）・インジェクション検査結果（`injection_flags`）・`read_only=true` を保持。
- **AuditLogEntry** — 監査ログ。**WORM＋ハッシュチェーン**（`prev_hash`→`hash` で連結、`seq` の欠番も改ざんの兆候）。

変更時は JSON がパースできることを確認。改ざん不能ログの運用要件は `governance-independence` スキル。
