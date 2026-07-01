"""デモ用の実務レベル・リアルなデータを生成し、パイプラインを通して web/ 用データを書き出す。

- リアルな正常母集団（複数法人・多数の得意先/製品/担当・8四半期・対数正規の金額分布）を生成
- fraud_scenarios 由来の18種の不正を混入（synthetic.SyntheticGenerator を再利用）
- ETL→ルール/ML/ネットワーク→ファネル→エージェント→レポート を実行
- UI がそのまま読める `web/data/data.js`（window.DEMO_DATA=...）を出力（file:// でも動くよう JS 変数に inline）

決定論的（seed 固定）。実データは扱わない（すべて合成）。
"""
from __future__ import annotations

import datetime as _dt
import json
import math
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from revenue_risk.contracts.models import SalesTransaction  # noqa: E402
from revenue_risk.etl.ingest import ingest_records  # noqa: E402
from revenue_risk.pipeline import Pipeline  # noqa: E402
from revenue_risk.synthetic.generator import SyntheticGenerator, _PRODUCTS  # noqa: E402
from revenue_risk.agent.connectors import MockConnectorProvider, ReadOnlyConnectors  # noqa: E402

CATEGORY_NAMES = {
    "CUTOFF": "期間帰属・カットオフ", "FICT": "架空売上・実在性", "CIRC": "循環取引・関連当事者",
    "RECOG": "収益認識", "RETURN": "返品・赤伝・変動対価", "PRICE": "価格・数量・粗利",
    "CREDIT": "与信・回収可能性", "CUST": "得意先マスタ・スクリーニング", "CTRL": "統制・承認・職務分掌",
    "JE": "仕訳・トップサイド調整", "PATT": "パターン・集中・行動", "COMPL": "コンプライアンス",
}
ASSERTION_NAMES = {
    "occurrence": "発生・実在性", "completeness": "網羅性", "accuracy": "正確性",
    "cutoff": "期間帰属", "classification": "分類", "valuation": "評価",
    "rights_obligations": "権利と義務", "presentation": "表示・開示",
}

# 中立・架空の会社名の部品（特定企業を想起させない）
_C1 = ["山手", "北央", "みなと", "青葉", "常盤", "けやき", "白鳥", "曙", "彩", "大和", "扶桑", "瑞穂",
       "東名", "西海", "北陸", "南光", "中央", "帝都", "共栄", "第一"]
_C2 = ["商事", "産業", "物流", "電機", "化成", "製作所", "テック", "システムズ", "食品", "精機",
       "マテリアル", "ソリューションズ", "ホールディングス", "工業", "通商", "興業"]
_ENTITIES = [
    ("ENT-JP", "国内事業会社（日本）"),
    ("ENT-US", "北米子会社"),
    ("ENT-EU", "欧州子会社"),
    ("ENT-APAC", "アジア子会社"),
]
_PERIODS = [
    ("2024-Q1", _dt.date(2024, 1, 1), _dt.date(2024, 3, 31)),
    ("2024-Q2", _dt.date(2024, 4, 1), _dt.date(2024, 6, 30)),
    ("2024-Q3", _dt.date(2024, 7, 1), _dt.date(2024, 9, 30)),
    ("2024-Q4", _dt.date(2024, 10, 1), _dt.date(2024, 12, 31)),
    ("2025-Q1", _dt.date(2025, 1, 1), _dt.date(2025, 3, 31)),
    ("2025-Q2", _dt.date(2025, 4, 1), _dt.date(2025, 6, 30)),
    ("2025-Q3", _dt.date(2025, 7, 1), _dt.date(2025, 9, 30)),
    ("2025-Q4", _dt.date(2025, 10, 1), _dt.date(2025, 12, 31)),
]


def _iso(d: _dt.date) -> str:
    return d.isoformat()


def build_rich_clean(rng: random.Random, n: int) -> list:
    """リアルな正常母集団を生成する（複数法人・多数の得意先/製品/担当・季節性・対数正規金額）。"""
    # 製品: 生成器の基本5製品（不正の文脈に必要）＋追加20製品。
    # 価格帯を広く連続的にとり、金額の先頭桁分布が自然（ベンフォード則に整合）になるようにする。
    products = dict(_PRODUCTS)
    for i in range(6, 26):
        # 対数一様分布で価格を引く（先頭桁がベンフォード則に自然整合）。極端な高低差は避け、
        # 特定製品ラインが常時外れ値化しないようレンジを抑える。
        base = int(10 ** rng.uniform(math.log10(40000), math.log10(900000)))
        products[f"P{i:02d}"] = (base, int(base * rng.uniform(0.55, 0.78)))
    prod_ids = list(products)

    # 得意先60（C001-C060、うち C001-C010 は不正シナリオが参照）
    customers = []
    for i in range(1, 61):
        customers.append((f"C{i:03d}", f"{rng.choice(_C1)}{rng.choice(_C2)}"))
    sales = [f"S{i}" for i in range(1, 11)]
    txns = []
    counter = 0
    for i in range(n):
        period, start, end = rng.choice(_PERIODS)
        span = (end - start).days
        # 季節性: Q4 をやや厚めに（正当な需要）。ただし期末最終15日は避ける
        off = rng.randint(5, max(6, span - 15))
        rec = start + _dt.timedelta(days=off)
        pid = rng.choice(prod_ids)
        price, cost = products[pid]  # 製品ごとに一定単価（マスタ整合・粗利一定＝価格系ルールは正常）
        qty = max(1, int(rng.lognormvariate(0.8, 0.45)))  # 現実的だが極端な裾は抑える
        amount = price * qty
        ent_id, _ = rng.choice(_ENTITIES)
        cust_id, cust_name = rng.choice(customers)
        channel = rng.choices(["direct", "distributor", "online"], weights=[72, 18, 10])[0]
        # 正常母集団は基本的に回収済み（未回収の滞留＝正当なリスク兆候をノイズにしない）。
        # 直近四半期のごく一部のみ、まだ支払サイト内で未入金（滞留ではない）。
        recent = rec >= _dt.date(2025, 10, 1)
        has_receipt = not (recent and rng.random() < 0.15)
        counter += 1
        txns.append(SalesTransaction(
            transaction_id=f"TXN-{counter:07d}",  # 単一連番系列（欠番なし）
            entity_id=ent_id, period=period, customer_id=cust_id, customer_name=cust_name,
            revenue_recognition_date=_iso(rec), amount=float(amount), currency="JPY",
            product_id=pid, product_name=f"製品{pid}", salesperson_id=rng.choice(sales), channel=channel,
            order_id=f"ORD-{counter:06d}", order_date=_iso(rec - _dt.timedelta(days=rng.randint(14, 28))),
            shipment_id=f"SHP-{counter:06d}", ship_date=_iso(rec - _dt.timedelta(days=rng.randint(6, 12))),
            delivery_date=_iso(rec - _dt.timedelta(days=rng.randint(1, 5))),
            invoice_id=f"INV-{counter:06d}", invoice_date=_iso(rec + _dt.timedelta(days=1)),
            revenue_account="4000", quantity=float(qty), unit_price=float(price), unit_cost=float(cost),
            gross_net_indicator="principal_gross", performance_obligation_status="satisfied_point_in_time",
            payment_terms_days=rng.choice([30, 45, 60]), credit_limit=float(rng.choice([50_000_000, 100_000_000])),
            credit_used=float(rng.randint(0, 20) * 1_000_000), source_system="system_generated",
            approver_id=f"A{rng.randint(1,5)}", registry_verified=True, screening_status="clear",
            receipt_date=_iso(rec + _dt.timedelta(days=rng.randint(35, 70))) if has_receipt else None,
            related_party_flag=(rng.random() < 0.02), disclosed=True,
        ))
    return txns


def txn_public(t: SalesTransaction) -> dict:
    return {k: v for k, v in t.to_dict().items()}


def main(n_clean: int = 3200, seed: int = 7) -> None:
    rng = random.Random(seed)
    gen = SyntheticGenerator(seed=seed)
    clean = build_rich_clean(rng, n_clean)
    inj_txns, injected = gen.generate(n_clean=0)  # 18不正シナリオの取引のみ（文脈は clean が提供）
    all_txns = clean + inj_txns
    print(f"generated {len(all_txns)} transactions ({len(clean)} clean + {len(inj_txns)} fraud)")

    # 総勘定元帳の期待値（区分別売上合計）。母集団は帳簿と一致する前提＝網羅性の裏付け。
    gl_totals = {}
    for t in all_txns:
        key = (t.entity_id, t.period)
        gl_totals[key] = gl_totals.get(key, 0.0) + float(t.amount or 0.0)
    ingest = ingest_records([t.to_dict() for t in all_txns],
                            gl_totals=gl_totals,
                            expected_periods=[p[0] for p in _PERIODS])
    conns = ReadOnlyConnectors(MockConnectorProvider(transactions=ingest.transactions, comms_store=gen.comms_store))
    result = Pipeline().run(ingest, connectors=conns, approvals={"G0", "G1"})

    rep = result.report.report
    # レポートの enriched findings（selected_for_deepdive / funnel_reasons を含む）を利用
    findings = rep["findings"]
    # 所見が参照する取引の詳細だけを持たせる（サイズ抑制）
    ref_ids = set()
    for f in result.findings:
        ref_ids.update(f.transaction_ids)
    txn_index = {t.transaction_id: t for t in ingest.transactions}
    transactions = {tid: txn_public(txn_index[tid]) for tid in ref_ids if tid in txn_index}
    evidence = [e.to_dict() for e in (result.agent_result.evidence if result.agent_result else [])]
    audit_entries = result.audit.to_list()
    chain = result.audit.verify()

    catalog = Pipeline().catalog
    rules = {rid: {"name_ja": r.name_ja, "category": r.category, "assertion": r.assertion,
                   "severity": r.severity} for rid, r in catalog.rules.items()}

    # 検出力（recall）を計算してデモに明示
    flagged = {t for f in result.findings for t in f.transaction_ids}
    detected = [inj.__dict__ | {"detected": bool(set(inj.transaction_ids) & flagged)} for inj in injected]

    data = {
        "meta": {
            "generated_at": _dt.datetime.now().strftime("%Y-%m-%d"),
            "engagement": rep["metadata"]["engagement"],
            "thresholds": rep["metadata"]["thresholds"],
            "disclaimer_ja": (
                "本画面はプロトタイプのデモです。すべて合成データであり、実在の企業・取引とは無関係です。"
                "所見はAIによる提示であり確定ではありません。確定・是正・通報・開示は独立した人間が判断します。"
            ),
        },
        "population": rep["population"],
        "funnel": rep["funnel"],
        "breakdown": rep["breakdown"],
        "data_quality": rep["data_quality"],
        "exploratory": rep["exploratory"],
        "agent": rep["agent"],
        "coverage": rep["coverage"],
        "findings": findings,
        "transactions": transactions,
        "evidence": evidence,
        "audit": {
            "entries": audit_entries,
            "total": len(audit_entries),
            "chain_valid": chain.valid,
            "checkpoint": result.audit.checkpoint(),
        },
        "rules": rules,
        "category_names": CATEGORY_NAMES,
        "assertion_names": ASSERTION_NAMES,
        "scenarios_detected": detected,
        "summary_md": result.report.summary_md,
    }

    out_dir = ROOT / "web" / "data"
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = "window.DEMO_DATA = " + json.dumps(data, ensure_ascii=False) + ";\n"
    (out_dir / "data.js").write_text(payload, encoding="utf-8")
    size_kb = len((out_dir / "data.js").read_bytes()) / 1024
    print(f"wrote web/data/data.js ({size_kb:.0f} KB)")
    print(f"findings={len(findings)} selected={rep['funnel']['selected']} "
          f"recall={sum(d['detected'] for d in detected)}/{len(detected)} chain_valid={chain.valid}")
    print(f"severity={rep['breakdown']['by_severity']}")


if __name__ == "__main__":
    main()
