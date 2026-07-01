"""検知述語（detector）レジストリ。

各 detector は Context を受け取り RuleHit のリストを返す。rule_catalog.yaml のルールIDに対応する。
しきい値は detection_params.yaml から Context.p() 経由で読む（コードに直書きしない方針）。
entity_network 系（CIRC-002/003/004/006）は network.py が担うため、ここには含まれない。

データが無い場合は例外を投げず [] を返す（壊れた1件で母集団評価を止めない設計）。
"""
from __future__ import annotations

import datetime as _dt
from typing import Callable, Dict, List

import numpy as np

from ..contracts.models import SalesTransaction, parse_date
from .context import Context, RuleHit

Detector = Callable[[Context], List[RuleHit]]
REGISTRY: Dict[str, Detector] = {}

#: entity_network 系（network.py が担当。detectors では扱わない）
NETWORK_RULES = frozenset({"CIRC-002", "CIRC-003", "CIRC-004", "CIRC-006"})


def detector(rule_id: str) -> Callable[[Detector], Detector]:
    def deco(fn: Detector) -> Detector:
        REGISTRY[rule_id] = fn
        return fn
    return deco


def _hit(rule_id: str, t: SalesTransaction, detail: str) -> RuleHit:
    return RuleHit(rule_id=rule_id, transaction_ids=[t.transaction_id], detail_ja=detail, entity_ids=[t.entity_id])


def _days(a: _dt.date, b: _dt.date) -> int:
    return (a - b).days


# ============================ CUTOFF ======================================
@detector("CUTOFF-001")
def cutoff_001(ctx: Context) -> List[RuleHit]:
    """期末最終N営業日の計上が日次平均比で突出（期末駆け込み計上の集中）。"""
    ratio = float(ctx.p("CUTOFF-001", "concentration_ratio", 3.0))
    n = int(ctx.period_common("last_n_days", 5))
    hits: List[RuleHit] = []
    for period, txns in ctx.by_period.items():
        pe = ctx.period_end(period)
        if pe is None:
            continue
        daily: Dict[_dt.date, float] = {}
        for t in txns:
            d = t.d_recognition()
            if d is not None:
                daily[d] = daily.get(d, 0.0) + float(t.amount or 0.0)
        if not daily:
            continue
        avg = float(np.mean(list(daily.values())))
        if avg <= 0:
            continue
        for t in txns:
            d = t.d_recognition()
            if d is None or not (_dt.timedelta(0) <= (pe - d) <= _dt.timedelta(days=n)):
                continue
            if daily.get(d, 0.0) > ratio * avg:
                hits.append(_hit("CUTOFF-001", t, f"期末{n}日以内({d})の計上が日次平均の{ratio}倍超"))
    return hits


@detector("CUTOFF-002")
def cutoff_002(ctx: Context) -> List[RuleHit]:
    """未出荷・出荷前の収益認識（ship_date > 認識日、または出荷記録欠落）。"""
    hits: List[RuleHit] = []
    for t in ctx.transactions:
        if not ctx.is_physical_goods(t):
            continue
        rec = t.d_recognition()
        if rec is None:
            continue
        ship = t.d_ship()
        if ship is not None and ship > rec:
            hits.append(_hit("CUTOFF-002", t, f"出荷日{ship}が収益認識日{rec}より後（未出荷計上の疑い）"))
        elif ship is None and not t.shipment_id and t.performance_obligation_status in (
            None, "satisfied_point_in_time", "not_satisfied", "unknown"
        ):
            hits.append(_hit("CUTOFF-002", t, "物品取引だが出荷記録が無く point-in-time 認識（未出荷計上の疑い）"))
    return hits


@detector("CUTOFF-003")
def cutoff_003(ctx: Context) -> List[RuleHit]:
    """検収前計上（delivery_date > 認識日）。"""
    hits: List[RuleHit] = []
    for t in ctx.transactions:
        rec, deliv = t.d_recognition(), t.d_delivery()
        if rec is not None and deliv is not None and deliv > rec:
            hits.append(_hit("CUTOFF-003", t, f"納品・検収日{deliv}が認識日{rec}より後（支配移転前の計上の疑い）"))
    return hits


@detector("CUTOFF-004")
def cutoff_004(ctx: Context) -> List[RuleHit]:
    """期末計上→翌期取消・返品。"""
    m = int(ctx.p("CUTOFF-004", "next_m_days", 10))
    hits: List[RuleHit] = []
    for t in ctx.transactions:
        if not ctx.is_period_end_booking(t):
            continue
        pe = ctx.period_end(t.period)
        rev = t.d_reversal()
        if pe is not None and rev is not None and rev > pe and _days(rev, pe) <= m:
            hits.append(_hit("CUTOFF-004", t, f"期末計上が翌期{_days(rev, pe)}日目({rev})に取消・返品"))
        elif t.return_flag and t.credit_memo_ref:
            hits.append(_hit("CUTOFF-004", t, "期末計上に対応する赤伝あり（翌期取消の疑い）"))
    return hits


@detector("CUTOFF-005")
def cutoff_005(ctx: Context) -> List[RuleHit]:
    """帳簿の締め遅延（決算日後の記帳で当期計上）。"""
    hits: List[RuleHit] = []
    for t in ctx.transactions:
        pe = ctx.period_end(t.period)
        post = t.d_posting()
        if pe is not None and post is not None and post > pe:
            hits.append(_hit("CUTOFF-005", t, f"記帳日{post}が期末{pe}より後だが当期({t.period})計上（締め遅延）"))
    return hits


@detector("CUTOFF-006")
def cutoff_006(ctx: Context) -> List[RuleHit]:
    """期末月売上の異常増（期間トレンドからの上振れ）。"""
    z = float(ctx.p("CUTOFF-006", "zscore", 2.5))
    totals = {p: sum(float(t.amount or 0) for t in txns) for p, txns in ctx.by_period.items()}
    if len(totals) < 4:
        return []
    periods = list(totals)
    zs = ctx.zscores([totals[p] for p in periods])
    hits: List[RuleHit] = []
    for p, zz in zip(periods, zs):
        if zz > z:
            for t in ctx.by_period[p]:
                if ctx.is_period_end_booking(t):
                    hits.append(_hit("CUTOFF-006", t, f"期間{p}の売上が趨勢からz={zz:.1f}上振れ"))
    return hits


# ============================ FICT ========================================
@detector("FICT-001")
def fict_001(ctx: Context) -> List[RuleHit]:
    """出荷・納品証憑のない物品売上。"""
    hits: List[RuleHit] = []
    for t in ctx.transactions:
        if not ctx.is_physical_goods(t):
            continue
        if not t.shipment_id and not t.ship_date and not t.delivery_date:
            hits.append(_hit("FICT-001", t, "物品取引だが出荷・納品の証憑が全欠落（実在性に疑義）"))
    return hits


@detector("FICT-002")
def fict_002(ctx: Context) -> List[RuleHit]:
    """新設・初回取引先への高額売上。"""
    days = int(ctx.p("FICT-002", "new_customer_days", 90))
    q = float(ctx.p("FICT-002", "amount_quantile", 0.9))
    thr = ctx.amount_quantile(q)
    hits: List[RuleHit] = []
    for t in ctx.transactions:
        first = ctx.customer_first_date(t.customer_id)
        rec = t.d_recognition()
        if first is None or rec is None:
            continue
        n_cust = len(ctx.by_customer.get(t.customer_id, []))
        is_new = _days(rec, first) <= days
        if is_new and float(t.amount or 0) >= thr and (n_cust <= 3 or rec == first):
            hits.append(_hit("FICT-002", t, f"初回取引({days}日以内)の得意先への高額計上(上位{int(q*100)}%)"))
    return hits


@detector("FICT-003")
def fict_003(ctx: Context) -> List[RuleHit]:
    """入金実績のない売上（相当期間経過後も対応入金なし）。"""
    horizon = int(ctx.p("FICT-003", "no_receipt_days", 120))
    if ctx.max_date is None:
        return []
    hits: List[RuleHit] = []
    for t in ctx.transactions:
        rec = t.d_recognition()
        if rec is None or t.receipt_date:  # 入金確認済はスキップ
            continue
        if t.return_flag:
            continue
        if _days(ctx.max_date, rec) > horizon:
            hits.append(_hit("FICT-003", t, f"認識から{horizon}日超経過し対応入金の確認なし"))
    return hits


@detector("FICT-004")
def fict_004(ctx: Context) -> List[RuleHit]:
    """bill-and-hold（請求済・出荷保留）。"""
    hits: List[RuleHit] = []
    for t in ctx.transactions:
        if not ctx.is_physical_goods(t):
            continue
        invoiced = bool(t.invoice_id or t.invoice_date)
        rec = t.d_recognition()
        ship = t.d_ship()
        if invoiced and rec is not None and (ship is None or ship > rec):
            hits.append(_hit("FICT-004", t, "請求・計上済だが未出荷（bill-and-hold 要件の充足要確認）"))
    return hits


@detector("FICT-005")
def fict_005(ctx: Context) -> List[RuleHit]:
    """期末一括大口計上後、以降の取引消滅。"""
    q = float(ctx.p("FICT-005", "amount_quantile", 0.9))
    thr = ctx.amount_quantile(q)
    if ctx.max_date is None:
        return []
    hits: List[RuleHit] = []
    for cid, txns in ctx.by_customer.items():
        dated = [(t.d_recognition(), t) for t in txns if t.d_recognition()]
        if not dated:
            continue
        last_date, last_t = max(dated, key=lambda x: x[0])
        # 顧客の最終取引が期末大口で、その後は母集団最新日まで取引なし
        if (
            ctx.is_period_end_booking(last_t)
            and float(last_t.amount or 0) >= thr
            and last_date < ctx.max_date
            and len(txns) <= 2
        ):
            hits.append(_hit("FICT-005", last_t, "期末大口計上後、以降の取引が途絶（実需の裏付け要確認）"))
    return hits


@detector("FICT-006")
def fict_006(ctx: Context) -> List[RuleHit]:
    """受注・出荷・請求の三点不整合（いずれか欠落のまま計上）。"""
    hits: List[RuleHit] = []
    for t in ctx.transactions:
        if not ctx.is_physical_goods(t):
            continue
        has_order = bool(t.order_id or t.order_date)
        has_ship = bool(t.shipment_id or t.ship_date)
        has_inv = bool(t.invoice_id or t.invoice_date)
        present = sum([has_order, has_ship, has_inv])
        if 1 <= present <= 2:  # 一部だけ存在＝連鎖が不完全
            missing = [n for n, ok in (("受注", has_order), ("出荷", has_ship), ("請求", has_inv)) if not ok]
            hits.append(_hit("FICT-006", t, f"三点照合の連鎖が不完全（欠落: {'/'.join(missing)}）"))
    return hits


# ============================ CIRC (transaction/customer) ==================
@detector("CIRC-001")
def circ_001(ctx: Context) -> List[RuleHit]:
    """関連当事者取引の価格乖離。"""
    z_thr = float(ctx.p("CIRC-001", "price_zscore", 2.0))
    hits: List[RuleHit] = []
    # 製品ごとに、第三者取引の単価分布から関連当事者取引が乖離しているか
    for pid, txns in ctx.by_product.items():
        third = [float(t.unit_price) for t in txns if not t.related_party_flag and t.unit_price is not None]
        if len(third) < 3:
            continue
        mu, sd = float(np.mean(third)), float(np.std(third))
        if sd == 0:
            continue
        for t in txns:
            if t.related_party_flag and t.unit_price is not None:
                zz = (float(t.unit_price) - mu) / sd
                if abs(zz) > z_thr:
                    hits.append(_hit("CIRC-001", t, f"関連当事者向け単価が第三者分布からz={zz:.1f}乖離"))
    return hits


@detector("CIRC-005")
def circ_005(ctx: Context) -> List[RuleHit]:
    """関連当事者取引の未開示（開示閾値超で開示リストに不在）。"""
    threshold = float(ctx.p("CIRC-005", "disclosure_amount", 10_000_000))
    hits: List[RuleHit] = []
    for cid, txns in ctx.by_customer.items():
        rp = [t for t in txns if t.related_party_flag]
        if not rp:
            continue
        total = sum(float(t.amount or 0) for t in rp)
        undisclosed = any(t.disclosed is False for t in rp) or (
            total >= threshold and not any(t.disclosed for t in rp)
        )
        if undisclosed:
            for t in rp:
                hits.append(_hit("CIRC-005", t, f"関連当事者取引 累計{total:,.0f} が開示閾値超だが未開示の疑い"))
    return hits


# ============================ RECOG =======================================
@detector("RECOG-001")
def recog_001(ctx: Context) -> List[RuleHit]:
    """履行義務未充足での前倒し認識。"""
    hits: List[RuleHit] = []
    for t in ctx.transactions:
        if t.performance_obligation_status == "not_satisfied":
            hits.append(_hit("RECOG-001", t, "履行義務 not_satisfied のまま収益認識（前倒しの疑い）"))
    return hits


@detector("RECOG-002")
def recog_002(ctx: Context) -> List[RuleHit]:
    """総額・純額の誤り（代理人取引を総額計上）。"""
    hits: List[RuleHit] = []
    for t in ctx.transactions:
        # 代理人指標(agent_net)なのに、代理人チャネルでない＝総額の売上経路に載っている疑い。
        # agent_net かつ channel=='agent' は「正しく純額計上した正常状態」なので発火させない
        # （旧・第2分岐は正常な代理人取引を誤検知していた）。
        if t.gross_net_indicator == "agent_net" and t.channel not in ("agent", "consignment"):
            hits.append(_hit("RECOG-002", t, "代理人(agent_net)指標だが総額計上の疑い（トップライン水増し）"))
    return hits


@detector("RECOG-003")
def recog_003(ctx: Context) -> List[RuleHit]:
    """進捗度の操作（一定期間履行義務での急変）。"""
    hits: List[RuleHit] = []
    for t in ctx.transactions:
        if t.performance_obligation_status != "satisfied_over_time":
            continue
        cur, prev = t.over_time_progress, t.prev_progress
        if cur is not None and prev is not None and (cur - prev) >= 0.3:
            hits.append(_hit("RECOG-003", t, f"進捗率が{prev:.0%}→{cur:.0%}へ急上昇（利益前倒しの疑い）"))
    return hits


@detector("RECOG-004")
def recog_004(ctx: Context) -> List[RuleHit]:
    """委託・消化仕入を通常売上計上。"""
    hits: List[RuleHit] = []
    for t in ctx.transactions:
        if t.channel in ("consignment", "agent") and t.gross_net_indicator != "agent_net":
            hits.append(_hit("RECOG-004", t, f"{t.channel} 取引を通常売上(総額)計上の疑い"))
    return hits


@detector("RECOG-005")
def recog_005(ctx: Context) -> List[RuleHit]:
    """複数履行義務への配分欠如。"""
    hits: List[RuleHit] = []
    for t in ctx.transactions:
        if t.multiple_po and t.performance_obligation_status not in ("satisfied_over_time",):
            hits.append(_hit("RECOG-005", t, "複数履行義務を含む契約だが単一計上（取引価格の配分欠如の疑い）"))
    return hits


@detector("RECOG-006")
def recog_006(ctx: Context) -> List[RuleHit]:
    """変動対価の未計上・過少（返品実績に対し引当が乖離）。"""
    tol = float(ctx.p("RECOG-006", "variance_tolerance", 0.5))
    # 実績返品率がこの水準未満なら発火しない（1件の返品で全取引を挙げる過検出を防ぐ・アラート疲れ対策）
    materiality = float(ctx.p("RECOG-006", "materiality", 0.1))
    max_per_customer = int(ctx.p("RECOG-006", "max_per_customer", 5))
    hits: List[RuleHit] = []
    for cid, txns in ctx.by_customer.items():
        n = len(txns)
        if n < 5:
            continue
        return_rate = sum(1 for t in txns if t.return_flag) / n
        if return_rate < materiality:  # 重要性の乏しい返品率は対象外
            continue
        # 変動対価が返品実績に対し過少な取引を、代表として金額上位から数件のみ提示
        under = [t for t in txns if float(t.discount_rate or 0.0) < return_rate * (1 - tol)]
        under.sort(key=lambda t: abs(float(t.amount or 0)), reverse=True)
        for t in under[:max_per_customer]:
            hits.append(_hit("RECOG-006", t, f"得意先返品率{return_rate:.0%}に対し変動対価計上が過少"))
    return hits


# ============================ RETURN ======================================
@detector("RETURN-001")
def return_001(ctx: Context) -> List[RuleHit]:
    """期末売上への翌期高返品（チャネルスタッフィング）。"""
    mult = float(ctx.p("RETURN-001", "cohort_multiple", 2.0))
    n = len(ctx.transactions)
    if n == 0:
        return []
    overall = sum(1 for t in ctx.transactions if t.return_flag) / n
    hits: List[RuleHit] = []
    pe_txns = [t for t in ctx.transactions if ctx.is_period_end_booking(t)]
    if not pe_txns:
        return []
    cohort_rate = sum(1 for t in pe_txns if t.return_flag) / len(pe_txns)
    if overall > 0 and cohort_rate > mult * overall:
        for t in pe_txns:
            if t.return_flag:
                hits.append(_hit("RETURN-001", t, f"期末計上ロットの返品率{cohort_rate:.0%}が全体{overall:.0%}の{mult}倍超"))
    return hits


@detector("RETURN-002")
def return_002(ctx: Context) -> List[RuleHit]:
    """赤伝・値引の期ずれ計上（発生日と計上日の期またぎ）。"""
    lag = int(ctx.p("RETURN-002", "lag_days", 15))
    hits: List[RuleHit] = []
    for t in ctx.transactions:
        # 返品発生（reversal_date）が認識期の期末より前なのに、当期に計上が残っている等の期ずれ
        rev = t.d_reversal()
        pe = ctx.period_end(t.period)
        if t.return_flag and rev is not None and pe is not None and rev < pe and _days(pe, rev) > lag:
            hits.append(_hit("RETURN-002", t, f"返品発生({rev})から計上まで{_days(pe, rev)}日の期ずれ"))
    return hits


@detector("RETURN-003")
def return_003(ctx: Context) -> List[RuleHit]:
    """特定得意先の異常返品率。"""
    z_thr = float(ctx.p("RETURN-003", "zscore", 2.5))
    rates, cids = [], []
    for cid, txns in ctx.by_customer.items():
        if len(txns) >= 5:
            rates.append(sum(1 for t in txns if t.return_flag) / len(txns))
            cids.append(cid)
    if len(rates) < 3:
        return []
    zs = ctx.zscores(rates)
    hits: List[RuleHit] = []
    for cid, zz in zip(cids, zs):
        if zz > z_thr:
            for t in ctx.by_customer[cid]:
                if t.return_flag:
                    hits.append(_hit("RETURN-003", t, f"得意先の返品率が分布からz={zz:.1f}の外れ値"))
    return hits


@detector("RETURN-004")
def return_004(ctx: Context) -> List[RuleHit]:
    """リベート・値引の急増/マスタ逸脱。"""
    max_rate = float(ctx.p("RETURN-004", "max_discount_rate", 0.3))
    hits: List[RuleHit] = []
    for t in ctx.transactions:
        if t.discount_rate is not None and float(t.discount_rate) > max_rate:
            hits.append(_hit("RETURN-004", t, f"値引率{float(t.discount_rate):.0%}が上限{max_rate:.0%}超"))
    return hits


@detector("RETURN-005")
def return_005(ctx: Context) -> List[RuleHit]:
    """返品権付き取引（裏契約）の疑い。"""
    # 決定論層では「販売店向け・期末・返品実績」の組合せで候補化。裏契約の実在は agent(comms.search) が検証。
    hits: List[RuleHit] = []
    for t in ctx.transactions:
        if t.channel == "distributor" and ctx.is_period_end_booking(t) and t.return_flag:
            hits.append(_hit("RETURN-005", t, "期末の販売店向け計上に返品発生（返品権付き裏契約の疑い・要 comms 検証）"))
    return hits


# ============================ PRICE =======================================
@detector("PRICE-001")
def price_001(ctx: Context) -> List[RuleHit]:
    """単価のマスタ乖離（製品別分布からの逸脱を代理指標とする）。"""
    tol = float(ctx.p("PRICE-001", "master_tolerance", 0.2))
    hits: List[RuleHit] = []
    for pid, txns in ctx.by_product.items():
        prices = [float(t.unit_price) for t in txns if t.unit_price is not None]
        if len(prices) < 5:
            continue
        median = float(np.median(prices))
        if median <= 0:
            continue
        for t in txns:
            if t.unit_price is not None and abs(float(t.unit_price) - median) / median > tol:
                hits.append(_hit("PRICE-001", t, f"単価{float(t.unit_price):,.0f}が製品中央値{median:,.0f}から{tol:.0%}超乖離"))
    return hits


@detector("PRICE-002")
def price_002(ctx: Context) -> List[RuleHit]:
    """数量の異常（負値・桁外れ）。"""
    z_thr = float(ctx.p("PRICE-002", "zscore", 4.0))
    qtys = [float(t.quantity) for t in ctx.transactions if t.quantity is not None]
    hits: List[RuleHit] = []
    mu = sd = 0.0
    if len(qtys) >= 5:
        mu, sd = float(np.mean(qtys)), float(np.std(qtys))
    for t in ctx.transactions:
        if t.quantity is None:
            continue
        q = float(t.quantity)
        if q < 0:
            hits.append(_hit("PRICE-002", t, f"数量が負値({q})"))
        elif sd > 0 and abs((q - mu) / sd) > z_thr:
            hits.append(_hit("PRICE-002", t, f"数量{q}が分布から大きく乖離"))
    return hits


@detector("PRICE-003")
def price_003(ctx: Context) -> List[RuleHit]:
    """異常な粗利率（unit_cost がある場合）。"""
    z_thr = float(ctx.p("PRICE-003", "margin_zscore", 2.5))
    hits: List[RuleHit] = []
    for pid, txns in ctx.by_product.items():
        margins, valid = [], []
        for t in txns:
            if t.unit_price and t.unit_cost is not None and float(t.unit_price) > 0:
                margins.append((float(t.unit_price) - float(t.unit_cost)) / float(t.unit_price))
                valid.append(t)
        if len(margins) < 5:
            continue
        zs = ctx.zscores(margins)
        for t, zz in zip(valid, zs):
            if abs(zz) > z_thr:
                hits.append(_hit("PRICE-003", t, f"粗利率が製品分布からz={zz:.1f}乖離"))
    return hits


@detector("PRICE-004")
def price_004(ctx: Context) -> List[RuleHit]:
    """承認外の大幅値引。"""
    max_rate = float(ctx.p("PRICE-004", "max_discount_rate", 0.3))
    hits: List[RuleHit] = []
    for t in ctx.transactions:
        if t.discount_rate is not None and float(t.discount_rate) > max_rate and not t.approver_id:
            hits.append(_hit("PRICE-004", t, f"値引率{float(t.discount_rate):.0%}が権限超だが承認記録なし"))
    return hits


@detector("PRICE-005")
def price_005(ctx: Context) -> List[RuleHit]:
    """金額分布のベンフォード乖離（先頭桁のカイ二乗適合度）。"""
    p_thr = float(ctx.p("PRICE-005", "p_value", 0.05))
    min_n = int(ctx.p("PRICE-005", "min_sample", 300))
    amounts = [abs(float(t.amount)) for t in ctx.transactions if t.amount and abs(float(t.amount)) >= 1]
    if len(amounts) < min_n:
        return []
    # 先頭桁分布
    lead = [int(str(int(a))[0]) for a in amounts if str(int(a))[0].isdigit() and str(int(a))[0] != "0"]
    observed = np.array([lead.count(d) for d in range(1, 10)], dtype=float)
    n = observed.sum()
    if n == 0:
        return []
    expected = np.array([np.log10(1 + 1 / d) for d in range(1, 10)]) * n
    chi2 = float(np.sum((observed - expected) ** 2 / expected))
    # 自由度8のカイ二乗の 0.05 臨界値 ≈ 15.51、0.01 ≈ 20.09
    critical = 15.51 if p_thr >= 0.05 else 20.09
    if chi2 > critical:
        # 母集団レベルの兆候（補助指標）。χ² 検定は大標本で過検出になりやすいため、
        # 全件を挙げずに、過剰な先頭桁を持つ取引の中から金額上位の代表 max_flags 件のみ提示する
        # （アラート疲れ対策）。母集団特性の考慮が必要な補助指標として扱う。
        max_flags = int(ctx.p("PRICE-005", "max_flags", 25))
        over_digit = int(np.argmax(observed - expected)) + 1
        cands = [t for t in ctx.transactions
                 if abs(float(t.amount or 0)) >= 1 and str(int(abs(float(t.amount))))[0] == str(over_digit)]
        cands.sort(key=lambda t: abs(float(t.amount or 0)), reverse=True)
        return [_hit("PRICE-005", t, f"先頭桁{over_digit}が母集団で過剰（ベンフォード乖離 χ²={chi2:.1f}・補助指標）")
                for t in cands[:max_flags]]
    return []


# ============================ CREDIT ======================================
@detector("CREDIT-001")
def credit_001(ctx: Context) -> List[RuleHit]:
    """与信限度超過の売上（承認なし）。"""
    hits: List[RuleHit] = []
    for t in ctx.transactions:
        if t.credit_limit is None:
            continue
        used = float(t.credit_used or 0.0)
        if used + float(t.amount or 0.0) > float(t.credit_limit) and not t.approver_id:
            hits.append(_hit("CREDIT-001", t, f"与信残高超過(使用{used:,.0f}+計上{float(t.amount or 0):,.0f}>限度{float(t.credit_limit):,.0f})かつ承認なし"))
    return hits


@detector("CREDIT-002")
def credit_002(ctx: Context) -> List[RuleHit]:
    """得意先別DSOの急増（入金日がある場合の支払遅延トレンド）。"""
    inc = int(ctx.p("CREDIT-002", "dso_increase_days", 30))
    hits: List[RuleHit] = []
    for cid, txns in ctx.by_customer.items():
        by_period: Dict[str, List[int]] = {}
        for t in txns:
            rec = t.d_recognition()
            rcp = parse_date(t.receipt_date)  # 不正日付でも None にフォールバック（全体を止めない）
            if rec and rcp:
                by_period.setdefault(t.period, []).append((rcp - rec).days)
        if len(by_period) < 2:
            continue
        periods = sorted(by_period)
        first_dso = np.mean(by_period[periods[0]])
        last_dso = np.mean(by_period[periods[-1]])
        if last_dso - first_dso >= inc:
            for t in ctx.by_customer[cid]:
                if t.period == periods[-1]:
                    hits.append(_hit("CREDIT-002", t, f"得意先DSOが{first_dso:.0f}→{last_dso:.0f}日へ急増"))
    return hits


@detector("CREDIT-003")
def credit_003(ctx: Context) -> List[RuleHit]:
    """回収懸念先への売上増（外部信用悪化と売上増の同時）。"""
    # 決定論層の代理: 登記/所在の確認が取れない先(registry_verified=False)への売上増。
    hits: List[RuleHit] = []
    for cid, txns in ctx.by_customer.items():
        if any(t.registry_verified is False for t in txns) or any(t.screening_status == "hit" for t in txns):
            total = sum(float(t.amount or 0) for t in txns)
            if total > ctx.amount_quantile(0.75):
                for t in txns:
                    hits.append(_hit("CREDIT-003", t, "信用に懸念のある先(登記未確認/スクリーニング該当)への売上"))
    return hits


@detector("CREDIT-004")
def credit_004(ctx: Context) -> List[RuleHit]:
    """支払条件の異常延長。"""
    max_terms = int(ctx.p("CREDIT-004", "max_payment_terms_days", 180))
    hits: List[RuleHit] = []
    for t in ctx.transactions:
        if t.payment_terms_days is not None and int(t.payment_terms_days) > max_terms:
            hits.append(_hit("CREDIT-004", t, f"支払サイト{t.payment_terms_days}日が標準({max_terms}日)を大きく超過"))
    return hits


@detector("CREDIT-005")
def credit_005(ctx: Context) -> List[RuleHit]:
    """長期滞留債権に対応する売上。"""
    horizon = int(ctx.p("CREDIT-005", "aging_days", 180))
    if ctx.max_date is None:
        return []
    hits: List[RuleHit] = []
    for t in ctx.transactions:
        rec = t.d_recognition()
        if rec is None or t.receipt_date or t.return_flag:
            continue
        if _days(ctx.max_date, rec) > horizon:
            hits.append(_hit("CREDIT-005", t, f"認識から{_days(ctx.max_date, rec)}日 未回収の長期滞留（評価の妥当性要確認）"))
    return hits


# ============================ CUST ========================================
@detector("CUST-001")
def cust_001(ctx: Context) -> List[RuleHit]:
    """反社・制裁該当の得意先。"""
    hits: List[RuleHit] = []
    for t in ctx.transactions:
        if t.screening_status == "hit":
            hits.append(_hit("CUST-001", t, "得意先が反社/制裁/PEPスクリーニングに該当（確定は人間が判断）"))
    return hits


@detector("CUST-002")
def cust_002(ctx: Context) -> List[RuleHit]:
    """得意先マスタの不審変更（振込先・住所の直前変更）。"""
    prox = int(ctx.p("CUST-002", "change_proximity_days", 30))
    q = float(ctx.p("CUST-002", "amount_quantile", 0.9))
    thr = ctx.amount_quantile(q)
    hits: List[RuleHit] = []
    for t in ctx.transactions:
        chg = t.d_master_change()
        rec = t.d_recognition()
        if chg is not None and rec is not None and 0 <= _days(rec, chg) <= prox and float(t.amount or 0) >= thr:
            hits.append(_hit("CUST-002", t, f"マスタ変更{chg}の{_days(rec, chg)}日後に高額取引"))
    return hits


@detector("CUST-003")
def cust_003(ctx: Context) -> List[RuleHit]:
    """実在性の疑わしい得意先（登記・所在確認が取れない）。"""
    hits: List[RuleHit] = []
    for t in ctx.transactions:
        if t.registry_verified is False:
            hits.append(_hit("CUST-003", t, "得意先の法人登記/所在の外部確認が取れない（実在性に疑義）"))
    return hits


@detector("CUST-004")
def cust_004(ctx: Context) -> List[RuleHit]:
    """得意先集中の急変。"""
    jump = float(ctx.p("CUST-004", "concentration_jump", 0.15))
    periods = sorted(ctx.by_period)
    if len(periods) < 2:
        return []
    def shares(period: str) -> Dict[str, float]:
        txns = ctx.by_period[period]
        total = sum(float(t.amount or 0) for t in txns) or 1.0
        agg: Dict[str, float] = {}
        for t in txns:
            agg[t.customer_id] = agg.get(t.customer_id, 0.0) + float(t.amount or 0)
        return {c: v / total for c, v in agg.items()}
    prev, cur = shares(periods[-2]), shares(periods[-1])
    hits: List[RuleHit] = []
    for cid, s in cur.items():
        if s - prev.get(cid, 0.0) >= jump:
            for t in ctx.by_period[periods[-1]]:
                if t.customer_id == cid:
                    hits.append(_hit("CUST-004", t, f"得意先の売上構成比が{prev.get(cid,0):.0%}→{s:.0%}へ急上昇"))
    return hits


@detector("CUST-005")
def cust_005(ctx: Context) -> List[RuleHit]:
    """役職員と関係のある得意先（利益相反）。※privacy 有効化が前提。"""
    hits: List[RuleHit] = []
    for t in ctx.transactions:
        if t.officer_related:
            hits.append(_hit("CUST-005", t, "得意先が自社役職員と関係あり（利益相反・関連当事者の疑い）"))
    return hits


# ============================ CTRL ========================================
@detector("CTRL-001")
def ctrl_001(ctx: Context) -> List[RuleHit]:
    """販売プロセスの職務分掌違反（同一者が起票と承認）。"""
    hits: List[RuleHit] = []
    for t in ctx.transactions:
        if t.salesperson_id and t.approver_id and t.salesperson_id == t.approver_id:
            hits.append(_hit("CTRL-001", t, "起票者と承認者が同一（職務分掌違反）"))
    return hits


@detector("CTRL-002")
def ctrl_002(ctx: Context) -> List[RuleHit]:
    """承認のない売上・価格・与信（承認必須で approver 欠落）。"""
    q = 0.9
    thr = ctx.amount_quantile(q)
    hits: List[RuleHit] = []
    for t in ctx.transactions:
        # 高額取引または与信超過取引で承認記録が欠落
        needs = float(t.amount or 0) >= thr or (
            t.credit_limit is not None and float(t.credit_used or 0) + float(t.amount or 0) > float(t.credit_limit)
        )
        if needs and not t.approver_id:
            hits.append(_hit("CTRL-002", t, "承認必須の取引だが承認記録(approver_id)が欠落"))
    return hits


@detector("CTRL-003")
def ctrl_003(ctx: Context) -> List[RuleHit]:
    """マスタ変更統制の不備（変更者が同一取引の起票者）。"""
    hits: List[RuleHit] = []
    for t in ctx.transactions:
        # master 変更日が取引直近かつ承認者=営業＝自己統制の兆候（簡易代理）
        if t.d_master_change() is not None and t.approver_id and t.salesperson_id == t.approver_id:
            hits.append(_hit("CTRL-003", t, "マスタ変更取引で起票者と承認者が同一（変更統制の不備）"))
    return hits


@detector("CTRL-004")
def ctrl_004(ctx: Context) -> List[RuleHit]:
    """三点照合の不一致（数量・金額）。"""
    hits: List[RuleHit] = []
    for t in ctx.transactions:
        # unit_price*quantity と amount の不一致（分割納品等は誤検知注記で除外）
        if t.unit_price is not None and t.quantity is not None:
            expected = float(t.unit_price) * float(t.quantity)
            amt = float(t.amount or 0)
            if expected > 0 and abs(expected - amt) / expected > 0.01:
                hits.append(_hit("CTRL-004", t, f"単価×数量({expected:,.0f})と計上額({amt:,.0f})が不一致"))
    return hits


@detector("CTRL-005")
def ctrl_005(ctx: Context) -> List[RuleHit]:
    """限度のオーバーライド（与信/値引限度の超過フラグ）。"""
    hits: List[RuleHit] = []
    for t in ctx.transactions:
        over_credit = t.credit_limit is not None and float(t.credit_used or 0) + float(t.amount or 0) > float(t.credit_limit)
        over_disc = t.discount_rate is not None and float(t.discount_rate) > float(ctx.p("PRICE-004", "max_discount_rate", 0.3))
        if (over_credit or over_disc) and t.approver_id:
            hits.append(_hit("CTRL-005", t, "限度超過が上長オーバーライドで承認（頻度・集中を要確認）"))
    return hits


# ============================ JE ==========================================
@detector("JE-001")
def je_001(ctx: Context) -> List[RuleHit]:
    """売上勘定への手動仕訳（トップサイド）。"""
    hits: List[RuleHit] = []
    for t in ctx.transactions:
        if t.source_system == "manual":
            hits.append(_hit("JE-001", t, "システム外の手動仕訳で売上計上（トップサイド調整の疑い）"))
    return hits


@detector("JE-002")
def je_002(ctx: Context) -> List[RuleHit]:
    """期末・決算直近の売上手動仕訳。"""
    n = int(ctx.p("JE-002", "near_close_days", 5))
    hits: List[RuleHit] = []
    for t in ctx.transactions:
        if ctx.is_journal(t) and ctx.is_period_end_booking(t, n_days=n):
            hits.append(_hit("JE-002", t, f"決算日近接({n}日以内)の手動売上仕訳"))
    return hits


@detector("JE-003")
def je_003(ctx: Context) -> List[RuleHit]:
    """経営者・上位者による売上調整仕訳（統制無効化）。"""
    hits: List[RuleHit] = []
    for t in ctx.transactions:
        if ctx.is_journal(t) and t.poster_role in ("executive", "manager", "director", "cfo"):
            hits.append(_hit("JE-003", t, f"上位職位({t.poster_role})による売上調整仕訳（統制無効化の兆候）"))
    return hits


@detector("JE-004")
def je_004(ctx: Context) -> List[RuleHit]:
    """異例な勘定組合せ（相手勘定が過去分布から逸脱）。"""
    counts: Dict[str, int] = {}
    for t in ctx.transactions:
        if t.revenue_account:
            counts[t.revenue_account] = counts.get(t.revenue_account, 0) + 1
    if not counts:
        return []
    total = sum(counts.values())
    hits: List[RuleHit] = []
    for t in ctx.transactions:
        if ctx.is_journal(t) and t.revenue_account and counts.get(t.revenue_account, 0) / total < 0.02:
            hits.append(_hit("JE-004", t, f"稀な相手勘定({t.revenue_account})への売上仕訳（異例な組合せ）"))
    return hits


@detector("JE-005")
def je_005(ctx: Context) -> List[RuleHit]:
    """バックデート・丸め・閾値直下。"""
    base = float(ctx.p("JE-005", "round_base", 100000))
    band = float(ctx.p("JE-005", "threshold_band", 0.02))
    hits: List[RuleHit] = []
    for t in ctx.transactions:
        if not ctx.is_journal(t):
            continue
        reasons = []
        entry, post = t.d_entry(), t.d_posting()
        # バックデート＝後から入力し、より過去の計上日を付す（入力日 > 計上日）
        if entry is not None and post is not None and entry > post:
            reasons.append(f"入力日{entry}>計上日{post}(バックデート)")
        amt = float(t.amount or 0)
        if base > 0 and amt > 0:
            rem = amt % base
            if rem == 0:
                reasons.append(f"丸め金額({amt:,.0f})")
            elif (base - rem) <= band * base:
                # 承認閾値（round_base の境界）の直下に張り付く（閾値回避の疑い）
                reasons.append(f"閾値直下({amt:,.0f})")
        if reasons:
            hits.append(_hit("JE-005", t, "／".join(reasons)))
    return hits


@detector("JE-006")
def je_006(ctx: Context) -> List[RuleHit]:
    """決算後の遡及計上。"""
    hits: List[RuleHit] = []
    for t in ctx.transactions:
        pe = ctx.period_end(t.period)
        post = t.d_posting()
        if ctx.is_journal(t) and pe is not None and post is not None and post > pe:
            hits.append(_hit("JE-006", t, f"決算後({post})に当期({t.period})へ遡及計上した仕訳"))
    return hits


# ============================ PATT ========================================
@detector("PATT-001")
def patt_001(ctx: Context) -> List[RuleHit]:
    """目標ギリギリ達成の反復（budget が与えられている場合）。"""
    # budget 情報を SalesTransaction は持たないため、決定論層では評価しない（[] を返す）。
    # 目標データがある運用では、期末計上・仕訳との相関で評価する（agent/レポート層）。
    return []


@detector("PATT-002")
def patt_002(ctx: Context) -> List[RuleHit]:
    """特定営業担当への異常集中。※privacy 有効化が前提。"""
    z_thr = float(ctx.p("PATT-002", "zscore", 2.5))
    # 担当者別の「期末計上比率」を異常フラグ率の代理とする
    rates, sids = [], []
    for sid, txns in ctx.by_salesperson.items():
        if len(txns) >= 5:
            rates.append(sum(1 for t in txns if ctx.is_period_end_booking(t)) / len(txns))
            sids.append(sid)
    if len(rates) < 3:
        return []
    zs = ctx.zscores(rates)
    hits: List[RuleHit] = []
    for sid, zz in zip(sids, zs):
        if zz > z_thr:
            for t in ctx.by_salesperson[sid]:
                if ctx.is_period_end_booking(t):
                    hits.append(_hit("PATT-002", t, f"営業担当に期末計上が異常集中(z={zz:.1f})"))
    return hits


@detector("PATT-003")
def patt_003(ctx: Context) -> List[RuleHit]:
    """四半期末・期末への取引集中。"""
    ratio_thr = float(ctx.p("PATT-003", "concentration_ratio", 0.4))
    hits: List[RuleHit] = []
    for period, txns in ctx.by_period.items():
        if len(txns) < 10:
            continue
        pe_amt = sum(float(t.amount or 0) for t in txns if ctx.is_period_end_booking(t))
        total = sum(float(t.amount or 0) for t in txns) or 1.0
        if pe_amt / total >= ratio_thr:
            for t in txns:
                if ctx.is_period_end_booking(t):
                    hits.append(_hit("PATT-003", t, f"期{period}の売上の{pe_amt/total:.0%}が期末に集中"))
    return hits


@detector("PATT-004")
def patt_004(ctx: Context) -> List[RuleHit]:
    """得意先/製品集中が期末のみ突出。"""
    hits: List[RuleHit] = []
    for period, txns in ctx.by_period.items():
        pe_txns = [t for t in txns if ctx.is_period_end_booking(t)]
        non_pe = [t for t in txns if not ctx.is_period_end_booking(t)]
        if len(pe_txns) < 5 or len(non_pe) < 5:
            continue
        def top_share(group: List[SalesTransaction]) -> float:
            agg: Dict[str, float] = {}
            for t in group:
                agg[t.customer_id] = agg.get(t.customer_id, 0.0) + float(t.amount or 0)
            total = sum(agg.values()) or 1.0
            return max(agg.values()) / total
        if top_share(pe_txns) - top_share(non_pe) >= 0.25:
            for t in pe_txns:
                hits.append(_hit("PATT-004", t, "期末のみ特定得意先への集中が突出"))
    return hits


@detector("PATT-005")
def patt_005(ctx: Context) -> List[RuleHit]:
    """趨勢・季節性からの乖離。"""
    z_thr = float(ctx.p("PATT-005", "zscore", 2.5))
    totals = {p: sum(float(t.amount or 0) for t in txns) for p, txns in ctx.by_period.items()}
    if len(totals) < 4:
        return []
    periods = list(totals)
    zs = ctx.zscores([totals[p] for p in periods])
    hits: List[RuleHit] = []
    for p, zz in zip(periods, zs):
        if abs(zz) > z_thr:
            # 代表として当該期の最大金額取引をフラグ
            biggest = max(ctx.by_period[p], key=lambda t: float(t.amount or 0))
            hits.append(_hit("PATT-005", biggest, f"期{p}の売上が趨勢からz={zz:.1f}乖離"))
    return hits


# ============================ COMPL =======================================
@detector("COMPL-001")
def compl_001(ctx: Context) -> List[RuleHit]:
    """契約獲得に絡む高額コミッション（贈収賄の兆候）。"""
    rate = float(ctx.p("COMPL-001", "commission_rate", 0.15))
    hits: List[RuleHit] = []
    for t in ctx.transactions:
        if t.commission_amount is not None and float(t.amount or 0) > 0:
            r = float(t.commission_amount) / float(t.amount)
            if r > rate:
                hits.append(_hit("COMPL-001", t, f"受注額比{r:.0%}の高額コミッション（実体・受益者要確認）"))
    return hits


@detector("COMPL-002")
def compl_002(ctx: Context) -> List[RuleHit]:
    """制裁対象・仕向地規制への売上。"""
    hits: List[RuleHit] = []
    for t in ctx.transactions:
        if t.screening_status == "hit" and (t.government_customer or t.channel in ("distributor", "agent", "direct")):
            # スクリーニング該当を制裁仕向地の代理とする（該非判定・ライセンスの有無は要確認）
            hits.append(_hit("COMPL-002", t, "仕向地/最終需要者が制裁・輸出規制に該当の疑い"))
    return hits


@detector("COMPL-003")
def compl_003(ctx: Context) -> List[RuleHit]:
    """価格協調・談合の兆候。"""
    # 入札・競合価格データを持たないため決定論層では評価しない（[] を返す）。
    return []


@detector("COMPL-004")
def compl_004(ctx: Context) -> List[RuleHit]:
    """不審な販売代理店・仲介者。"""
    hits: List[RuleHit] = []
    for t in ctx.transactions:
        if t.channel in ("agent", "distributor") and (t.registry_verified is False or t.screening_status == "hit"):
            hits.append(_hit("COMPL-004", t, "実体・受益者が不明な代理店/仲介者経由の取引"))
    return hits


@detector("COMPL-005")
def compl_005(ctx: Context) -> List[RuleHit]:
    """公務員・政府系顧客への異例条件。"""
    hits: List[RuleHit] = []
    for t in ctx.transactions:
        if t.government_customer:
            unusual = (t.discount_rate is not None and float(t.discount_rate) > 0.2) or (
                t.commission_amount is not None and float(t.amount or 0) > 0
                and float(t.commission_amount) / float(t.amount) > 0.1
            )
            if unusual:
                hits.append(_hit("COMPL-005", t, "政府系顧客への通常外の値引/便益の紐付け"))
    return hits
