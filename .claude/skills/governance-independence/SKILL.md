---
name: governance-independence
description: Use for work involving auditor independence, deployment posture (internal audit vs continuous monitoring vs first-line real-time), engagement_mode configuration, the four expert lenses (internal audit / financial-statement audit / compliance / statutory auditor), ML model risk management, the immutable audit log, regulatory mapping (監基報240, J-SOX, 会社法, COSO, ACFE), or RACI and accountability.
---

# ガバナンス・独立性

## 4つの専門家視点
同じエンジンが、立場の異なる専門家に（アサーション紐付けと証跡を介して）それぞれの言語で価値を返す:
- **内部監査（3線）** — 販売プロセスの統制有効性・不正の早期発見（CTRL, JE, CUTOFF, PATT）。
- **会計監査（会計監査人）** — 監基報240 の不正推定・仕訳テスト・カットオフ・実在性をアサーション別に検証（全カテゴリ）。
- **コンプライアンス** — 販売チャネルの贈収賄・制裁・談合・反社（COMPL, CUST, CIRC）。
- **監査役** — 取締役の職務執行の適法性・利益相反・関連当事者・経営者の調整仕訳（CIRC, CUST-005, JE-003, COMPL）。

監査役視点（経営者の統制無効化・利益相反）は他ツールが手薄な領域。詳細 `docs/governance.md` §1。

## 独立性の区別（最重要・要法務確認）
売上不正は財務諸表不正であり、独立監査（会計監査人・監査役）がまさに対処すべき領域ゆえ、独立性が経費版より鋭い。2軸で管理:
- **利用主体**: Track A＝内部監査/監査役支援（既定）／ Track B＝外部商用化・法定監査。会計監査人が自らの手続として使うのは本来業務だが、被監査会社の販売統制を監査法人が構築・運用すると自己レビューの脅威。
- **配備レイヤ（未決定・要方針）**: 3線＝内部監査/会計監査（検知）→ 2線＝継続モニタリング → 1線＝現場リアルタイム（予防）。1線・2線へ寄るほど経営者自身の統制になり、監査クライアント相手では自己レビューの脅威が強まる。緩和レバーは提供形態（個社向け運用ほど自己レビュー寄り／製品化ほど防御的）。1線の硬ブロックは「未出荷計上・与信超過・制裁該当」級の高精度な決定論ルールに限定し、確率的なもの（ML・エージェント）は警告/レビュー送りに留める。

`engagement_mode` で明示的に切替。詳細 `docs/governance.md` §2。

## モデルリスク管理
バリデーション・ドリフト監視・バイアス検査・定期再検証をライフサイクルで回す。`docs/governance.md` §3。

## 改ざん不能な監査ログ・規制マッピング・RACI
WORM＋ハッシュチェーン（型は `data-contracts` スキル、要件は §4）。規制対応（監基報240/315/330・収益認識基準・J-SOX・金商法・会社法・独禁法/外為法・COSO/ACFE）は §6。責任分担では **AIが説明責任（A）を持つ行を作らない**（§5）。売上の確定・是正の A は独立した立場に置く。
