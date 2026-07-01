"""L5: エンティティ・ネットワーク分析（売上特有）。

取引相手（得意先・計上法人）と資金フローをグラフ化し、単一明細では見えない
循環取引・買戻し・介在業者・資金の閉路を検出する。エッジは「売り手(entity_id)→買い手(customer_id)」。
ノードは entity_id・customer_id が共有する id 空間で解決する（同一 id は同一エンティティ）。

担当ルール: CIRC-002（仕入先=得意先の同一性・買戻し）/ CIRC-003（資金の循環還流）/
           CIRC-004（実体の乏しい介在業者・スルー取引）/ CIRC-006（取引ネットワークの循環経路）。

探索は深度（ホップ数）と対象エンティティ数に上限を設ける（無限探索の禁止・docs/agent-design.md §4）。
"""
from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from ..contracts.models import SalesTransaction


@dataclass
class Edge:
    seller: str
    buyer: str
    txn_id: str
    amount: float
    date: Optional[_dt.date]


@dataclass
class NetworkFinding:
    rule_id: str
    transaction_ids: List[str]
    entity_ids: List[str]
    detail_ja: str = ""
    path: List[str] = field(default_factory=list)


class EntityNetwork:
    def __init__(self, transactions: Sequence[SalesTransaction], params: Optional[Dict] = None) -> None:
        self.params = params or {}
        self._rule_params = self.params.get("rules", {}) or {}
        self.edges: List[Edge] = []
        self.out: Dict[str, List[Edge]] = {}
        self.nodes: set[str] = set()
        self.truncated: bool = False  # analyze() が上限で探索を打ち切ったか
        for t in transactions:
            e = Edge(
                seller=t.entity_id,
                buyer=t.customer_id,
                txn_id=t.transaction_id,
                amount=float(t.amount or 0.0),
                date=t.d_recognition(),
            )
            self.edges.append(e)
            self.out.setdefault(e.seller, []).append(e)
            self.nodes.add(e.seller)
            self.nodes.add(e.buyer)

    def _p(self, rule_id: str, key: str, default):
        return (self._rule_params.get(rule_id, {}) or {}).get(key, default)

    #: サイクル列挙の総数上限（爆発防止の安全弁）。超過時は truncated=True を立てる。
    CYCLE_CAP = 500

    # ---- 公開 API -------------------------------------------------------
    def analyze(self, max_hops: int = 4, max_entities: int = 200) -> List[NetworkFinding]:
        findings: List[NetworkFinding] = []
        # 対象エンティティ数の上限（スコープ制御）
        if len(self.nodes) > max_entities:
            # 取引額の大きいノードに限定（探索コストの上限）
            weight: Dict[str, float] = {}
            for e in self.edges:
                weight[e.seller] = weight.get(e.seller, 0.0) + e.amount
                weight[e.buyer] = weight.get(e.buyer, 0.0) + e.amount
            keep = set(sorted(self.nodes, key=lambda n: weight.get(n, 0.0), reverse=True)[:max_entities])
        else:
            keep = set(self.nodes)

        self.truncated = False  # 上限で探索が打ち切られたか（網羅性の限界を呼び出し側へ通知）
        cycles = self._find_cycles(max_hops=max_hops, allowed=keep)
        findings.extend(self._cycle_findings(cycles))
        # CIRC-004 も同じ entity スコープ（keep）に従わせる（無限探索の禁止・コスト上限）
        findings.extend(self._passthrough_findings(allowed=keep))
        return findings

    # ---- サイクル検出（DFS・ホップ上限つき）-----------------------------
    def _find_cycles(self, max_hops: int, allowed: set) -> List[List[Edge]]:
        """max_hops 以内の有向サイクルを列挙（各サイクルは辺のリスト）。

        安全弁は dfs 内部でも評価し、単一の密ノードでの爆発も止める。上限で打ち切った場合は
        self.truncated=True を立て、結果が不完全（ノード順依存で欠落しうる）であることを通知する。
        """
        found: List[List[Edge]] = []
        seen_signatures: set = set()

        def dfs(start: str, current: str, path_edges: List[Edge], visited: set) -> None:
            if len(found) > self.CYCLE_CAP:
                self.truncated = True
                return
            if len(path_edges) >= max_hops:
                return
            for e in self.out.get(current, []):
                if e.buyer not in allowed:
                    continue
                if len(found) > self.CYCLE_CAP:
                    self.truncated = True
                    return
                if e.buyer == start and len(path_edges) >= 1:
                    cycle = path_edges + [e]
                    sig = tuple(sorted(x.txn_id for x in cycle))
                    if sig not in seen_signatures:
                        seen_signatures.add(sig)
                        found.append(cycle)
                    continue
                if e.buyer in visited:
                    continue
                dfs(start, e.buyer, path_edges + [e], visited | {e.buyer})
            return

        # start ノードは、最小限の重複で全サイクルを拾うため全ノードから
        for node in sorted(allowed):
            if len(found) > self.CYCLE_CAP:
                self.truncated = True
                break
            dfs(node, node, [], {node})
        return found

    def _cycle_findings(self, cycles: List[List[Edge]]) -> List[NetworkFinding]:
        out: List[NetworkFinding] = []
        net_ratio = float(self._p("CIRC-003", "net_residual_ratio", 0.1))
        max_days = int(self._p("CIRC-003", "max_cycle_days", 90))
        for cycle in cycles:
            nodes = [cycle[0].seller] + [e.buyer for e in cycle]
            txn_ids = [e.txn_id for e in cycle]
            entity_ids = sorted({n for n in nodes})
            length = len(cycle)

            # CIRC-002: 2社間の相互取引（買戻し）
            if length == 2:
                out.append(NetworkFinding(
                    rule_id="CIRC-002",
                    transaction_ids=txn_ids,
                    entity_ids=entity_ids,
                    detail_ja=f"{nodes[0]}⇔{nodes[1]} の相互取引（仕入先=得意先／買戻しの疑い）",
                    path=nodes,
                ))

            # CIRC-006: 循環経路（A→B→C→A 等）
            out.append(NetworkFinding(
                rule_id="CIRC-006",
                transaction_ids=txn_ids,
                entity_ids=entity_ids,
                detail_ja=f"取引グラフに循環経路 {'→'.join(nodes)} を検出",
                path=nodes,
            ))

            # CIRC-003: 資金の循環還流（ネット移動が僅少・短期閉路）
            # 「短期」は本ルールの本質（rule_catalog: 資金の循環的還流/critical）。日付が
            # 全辺に揃っていない閉路は短期性を立証できないため CIRC-003 は発火させない
            # （根拠なき所見を出さない・絶対原則 #2）。CIRC-006/002 は別途発火する。
            amounts = [e.amount for e in cycle]
            gross = sum(amounts)
            residual = max(amounts) - min(amounts)
            dates = [e.date for e in cycle if e.date]
            span_ok = len(dates) == len(cycle) and (max(dates) - min(dates)).days <= max_days
            if gross > 0 and residual / gross <= net_ratio and span_ok:
                out.append(NetworkFinding(
                    rule_id="CIRC-003",
                    transaction_ids=txn_ids,
                    entity_ids=entity_ids,
                    detail_ja=f"閉路 {'→'.join(nodes)} でネット資金移動が僅少（残差比{residual/gross:.0%}）",
                    path=nodes,
                ))
        return out

    # ---- 介在業者（スルー取引）------------------------------------------
    def _passthrough_findings(self, allowed: Optional[set] = None) -> List[NetworkFinding]:
        """ノードが「買って（customer として受領）→短期に売る（entity として販売）」構造を検出。

        allowed（entity スコープ上限）を尊重し、3当事者すべてが範囲内のペアのみ評価する。
        原点回帰（下流の買い手＝上流の売り手）は買戻し（CIRC-002/003）であり、スルー取引ではないため除外する。
        """
        thin = float(self._p("CIRC-004", "thin_margin_rate", 0.03))
        rev_days = int(self._p("CIRC-004", "reversal_days", 30))
        # ノードごとの購入（buyer として現れる）・販売（seller として現れる）
        buys: Dict[str, List[Edge]] = {}
        for e in self.edges:
            if allowed is not None and (e.seller not in allowed or e.buyer not in allowed):
                continue
            buys.setdefault(e.buyer, []).append(e)
        out: List[NetworkFinding] = []
        for node, in_edges in buys.items():
            if allowed is not None and node not in allowed:
                continue
            out_edges = self.out.get(node, [])
            for ie in in_edges:
                for oe in out_edges:
                    if allowed is not None and oe.buyer not in allowed:
                        continue
                    # 原点回帰（A→B→A）は買戻しであり介在業者(スルー)ではない。退化エッジも除外。
                    if oe.buyer == ie.seller or oe.buyer == node or ie.seller == node:
                        continue
                    if ie.date is None or oe.date is None:
                        continue
                    if 0 <= (oe.date - ie.date).days <= rev_days and ie.amount > 0:
                        margin = (oe.amount - ie.amount) / ie.amount
                        if abs(margin) <= thin:
                            out.append(NetworkFinding(
                                rule_id="CIRC-004",
                                transaction_ids=[ie.txn_id, oe.txn_id],
                                entity_ids=[ie.seller, node, oe.buyer],
                                detail_ja=f"介在業者 {node} が短期(≤{rev_days}日)・僅少粗利({margin:.0%})で通過（スルー取引の疑い）",
                                path=[ie.seller, node, oe.buyer],
                            ))
        return out

    def summary(self) -> Dict[str, int]:
        return {"nodes": len(self.nodes), "edges": len(self.edges)}
