---
name: revenue-recognition
description: Use when working on revenue recognition logic, cutoff (period attribution) analysis, mapping detection rules to financial-statement assertions, the 5-step revenue model (contract, performance obligations, transaction price, allocation, satisfaction), principal-vs-agent (gross/net), variable consideration, or journal-entry (top-side) testing on revenue accounts.
---

# 収益認識・監査アサーション

売上リスク分析を実務グレードにする土台。検知を、会計監査の言語（収益認識の5ステップ＋財務諸表アサーション）に対応づける。

## なぜ売上が最重要リスクか
監査基準委員会報告書240 は、**収益認識に不正リスクが存在すると推定する**（反証可能な推定）。売上不正は経営者が財務諸表を歪める財務諸表不正であり、**統制を無効化できる立場の者**による不正を含む（末端従業員による経費不正と根本的に違う）。ゆえに統制の逸脱に加え、統制をすり抜ける取引の実在性・期間帰属・仕訳を重点評価する。

## アサーション紐付け（必須）
全ルール・全所見に、リスクに晒すアサーションを付す（`RiskFinding.assertion`）: 発生(occurrence)・網羅性(completeness)・正確性(accuracy)・期間帰属(cutoff)・分類(classification)・評価(valuation)・権利と義務(rights_obligations)・表示(presentation)。これで所見が「監査人が何を検証すべきか」に直結する。

## カットオフ（期間帰属）が主軸
不正・利益調整の多くは「ある数字を、ある期日までに作る」ために起こる。だから受注・出荷・検収・請求・収益認識・入金・返品の各日付を分離保持し三角照合する（`SalesTransaction` の日付群はこのため）。未出荷計上・検収前計上・締め遅延・期末計上→翌期取消の検出はここから。

## 仕訳テスト（JE）
監基報240 は統制無効化への対応として仕訳テストを求める。手動・トップサイドの売上仕訳、決算直近の調整、上位者起票、異例な勘定組合せ、バックデートに着目（JE カテゴリ）。

## 参照
`docs/revenue-recognition.md`（5ステップ対応表・アサーション表・JEテスト）、設計判断は `docs/design-rationale.md`。
