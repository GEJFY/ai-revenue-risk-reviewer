"""エージェントの read-only ツール（外部証憑コネクタ）。

すべて read-only・最小権限。書き込み系は存在しない（docs/agent-design.md §2）。
comms.search / contract.retrieve / delivery.proof は攻撃者が操作しうる非構造データを返すため
`is_untrusted=True` とし、呼び出し側（orchestrator）で必ずインジェクション走査を行う。

本番のコネクタ（運送業者・登記・制裁リスト・銀行・通信）は資格情報を要するため、ここでは
テスト・デモ用の MockConnectorProvider を同梱する。実コネクタは同じインタフェースで差し替える。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from ..contracts.models import SalesTransaction

# 非構造（信頼できない）コンテンツを返すツール
UNTRUSTED_TOOLS = frozenset({"delivery.proof", "contract.retrieve", "comms.search"})
# 高感度カテゴリ（G1 の対象）
SENSITIVE_TOOLS = frozenset({"sanctions.lookup", "related_party.graph", "comms.search"})
# プライバシー影響のある収集（G0 の対象）
PRIVACY_TOOLS = frozenset({"comms.search", "related_party.graph"})

ALL_TOOLS = (
    "shipment.lookup", "delivery.proof", "contract.retrieve", "customer.verify",
    "sanctions.lookup", "related_party.graph", "bank.receipt_match", "ar.aging",
    "master.lookup", "comms.search",
)

# ツール -> Evidence.type の対応
TOOL_EVIDENCE_TYPE = {
    "shipment.lookup": "shipment_confirmation",
    "delivery.proof": "delivery_proof",
    "contract.retrieve": "contract_terms",
    "customer.verify": "corporate_registry",
    "sanctions.lookup": "sanctions_screening",
    "related_party.graph": "related_party_registry",
    "bank.receipt_match": "bank_receipt",
    "ar.aging": "other",
    "master.lookup": "price_master",
    "comms.search": "communication",
}


@dataclass
class ConnectorResponse:
    tool: str
    found: bool
    data: Dict[str, Any] = field(default_factory=dict)
    content: Optional[str] = None          # 非構造・信頼できないテキスト（untrusted のみ）
    source: str = "mock"
    provenance: str = ""
    legal_basis: Optional[str] = None
    is_untrusted: bool = False

    @property
    def read_only(self) -> bool:
        return True


class ConnectorProvider:
    """コネクタの抽象。実装は provider.handle(tool, txn) を返す。"""

    def handle(self, tool: str, txn: SalesTransaction) -> ConnectorResponse:  # pragma: no cover - interface
        raise NotImplementedError


class ReadOnlyConnectors:
    """provider をラップし read-only アクセスを提供する薄いディスパッチャ。"""

    def __init__(self, provider: ConnectorProvider) -> None:
        self._provider = provider

    def call(self, tool: str, txn: SalesTransaction) -> ConnectorResponse:
        if tool not in ALL_TOOLS:
            raise ValueError(f"未知のツール: {tool}")
        resp = self._provider.handle(tool, txn)
        resp.is_untrusted = tool in UNTRUSTED_TOOLS
        return resp


class MockConnectorProvider(ConnectorProvider):
    """テスト・デモ用の証憑プロバイダ。取引フィールドから証憑を導出する。

    comms_store / contract_store / delivery_store に planted テキストを与えると、
    レッドチーム（SC-RED-01）のインジェクション試験ができる。キーは transaction_id か customer_id。
    """

    def __init__(
        self,
        transactions=None,
        comms_store: Optional[Dict[str, str]] = None,
        contract_store: Optional[Dict[str, str]] = None,
        delivery_store: Optional[Dict[str, str]] = None,
        legal_basis: Optional[str] = None,
    ) -> None:
        self._comms = comms_store or {}
        self._contracts = contract_store or {}
        self._delivery = delivery_store or {}
        self._legal_basis = legal_basis

    def _planted(self, store: Dict[str, str], txn: SalesTransaction) -> Optional[str]:
        return store.get(txn.transaction_id) or store.get(txn.customer_id)

    def handle(self, tool: str, txn: SalesTransaction) -> ConnectorResponse:
        prov = f"{tool}?transaction_id={txn.transaction_id}"
        if tool == "shipment.lookup":
            found = bool(txn.ship_date or txn.shipment_id)
            return ConnectorResponse(tool, found, {"ship_date": txn.ship_date, "shipment_id": txn.shipment_id}, provenance=prov)
        if tool == "delivery.proof":
            planted = self._planted(self._delivery, txn)
            content = planted or (f"検収・納品証憑: 納品日={txn.delivery_date}" if txn.delivery_date else "納品証憑なし")
            return ConnectorResponse(tool, bool(txn.delivery_date), {"delivery_date": txn.delivery_date},
                                     content=content, provenance=prov)
        if tool == "contract.retrieve":
            planted = self._planted(self._contracts, txn)
            content = planted or (f"契約条件: contract_id={txn.contract_id}, terms=標準" if txn.contract_id else "契約参照なし")
            return ConnectorResponse(tool, bool(txn.contract_id), {"contract_id": txn.contract_id},
                                     content=content, provenance=prov)
        if tool == "customer.verify":
            verified = txn.registry_verified
            return ConnectorResponse(tool, verified is not False,
                                     {"registry_verified": verified, "customer_id": txn.customer_id}, provenance=prov)
        if tool == "sanctions.lookup":
            hit = txn.screening_status == "hit"
            return ConnectorResponse(tool, True, {"screening_status": txn.screening_status or "unknown", "hit": hit},
                                     provenance=prov)
        if tool == "related_party.graph":
            return ConnectorResponse(tool, True,
                                     {"related_party": bool(txn.related_party_flag), "disclosed": txn.disclosed,
                                      "officer_related": bool(txn.officer_related)},
                                     provenance=prov, legal_basis=self._legal_basis)
        if tool == "bank.receipt_match":
            found = bool(txn.receipt_date)
            return ConnectorResponse(tool, found, {"receipt_date": txn.receipt_date}, provenance=prov)
        if tool == "ar.aging":
            return ConnectorResponse(tool, True,
                                     {"payment_terms_days": txn.payment_terms_days, "receipt_date": txn.receipt_date},
                                     provenance=prov)
        if tool == "master.lookup":
            return ConnectorResponse(tool, True,
                                     {"master_change_date": txn.master_change_date, "unit_price": txn.unit_price},
                                     provenance=prov)
        if tool == "comms.search":
            planted = self._planted(self._comms, txn)
            return ConnectorResponse(tool, planted is not None, {"customer_id": txn.customer_id},
                                     content=planted, provenance=prov, legal_basis=self._legal_basis)
        raise ValueError(f"未知のツール: {tool}")
