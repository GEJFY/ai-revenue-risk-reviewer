"""データ契約の型（`config/schemas/data_contracts.json` に対応する Python モデル）。

4つの型: SalesTransaction / RiskFinding / Evidence / AuditLogEntry。
すべての受け渡しはこれらに正規化し、`SchemaValidator` で JSON Schema 準拠を検証する。

設計上の要点:
  - 売上の期間帰属（カットオフ）分析のため、受注・出荷・検収・請求・収益認識・取消の
    各日付を分離保持する（date triangulation の要）。
  - RiskFinding は AI が確定できない。`hitl_status` の confirmed/dismissed は人間のみ
    （絶対原則 #3・HITL）。`set_hitl_status()` がこれをコードで強制する。
"""
from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

# ---- 列挙（スキーマの enum に一致）----------------------------------------
ASSERTIONS = (
    "occurrence",       # 発生・実在性
    "completeness",     # 網羅性
    "accuracy",         # 正確性
    "cutoff",           # 期間帰属
    "classification",   # 分類
    "valuation",        # 評価
    "rights_obligations",  # 権利と義務
    "presentation",     # 表示・開示
)
SEVERITIES = ("low", "medium", "high", "critical")
HITL_STATUSES = ("open", "in_review", "confirmed", "dismissed")
#: 人間しか設定できない hitl_status（AI は open/in_review までしか設定できない）
HUMAN_ONLY_HITL_STATUSES = frozenset({"confirmed", "dismissed"})
CREATED_BY = ("rule_engine", "ml_model", "agent")

CHANNELS = ("direct", "distributor", "agent", "online", "consignment", "other")
GROSS_NET = ("principal_gross", "agent_net", "unknown")
PO_STATUS = (
    "satisfied_point_in_time",
    "satisfied_over_time",
    "not_satisfied",
    "unknown",
)
SOURCE_SYSTEMS = ("system_generated", "manual", "interface", "unknown")


def parse_date(value: Optional[str]) -> Optional[_dt.date]:
    """ISO 文字列（YYYY-MM-DD もしくは日時）を date に。失敗時は None。"""
    if value is None or value == "":
        return None
    if isinstance(value, _dt.date):
        return value
    s = str(value).strip()
    # date-time も許容（先頭10文字を日付として解釈）
    try:
        return _dt.date.fromisoformat(s[:10])
    except ValueError:
        return None


def _clean(d: Dict[str, Any]) -> Dict[str, Any]:
    """None / 空リスト / 空辞書を落とし、スキーマにきれいな dict を渡す。"""
    out: Dict[str, Any] = {}
    for k, v in d.items():
        if v is None:
            continue
        if isinstance(v, (list, dict)) and len(v) == 0:
            continue
        out[k] = v
    return out


@dataclass
class SalesTransaction:
    """分析対象の売上取引明細。受注→出荷→検収→請求→収益認識→入金→返品 を1レコードで追跡。"""

    # --- 必須（スキーマ required）---
    # 既定値を持たせ、from_dict が欠損レコードでも構築できるようにする（ETL は欠損を
    # data_quality に記録し母集団評価を止めない）。欠損の有無はスキーマ検証で検出する。
    transaction_id: str = ""
    entity_id: str = ""
    period: str = ""
    customer_id: str = ""
    revenue_recognition_date: str = ""
    amount: float = 0.0
    currency: str = ""

    # --- 任意（スキーマ properties）---
    customer_name: Optional[str] = None
    product_id: Optional[str] = None
    product_name: Optional[str] = None
    salesperson_id: Optional[str] = None
    channel: Optional[str] = None
    order_id: Optional[str] = None
    order_date: Optional[str] = None
    shipment_id: Optional[str] = None
    ship_date: Optional[str] = None
    delivery_date: Optional[str] = None
    invoice_id: Optional[str] = None
    invoice_date: Optional[str] = None
    revenue_account: Optional[str] = None
    quantity: Optional[float] = None
    unit_price: Optional[float] = None
    gross_net_indicator: Optional[str] = None
    performance_obligation_status: Optional[str] = None
    contract_id: Optional[str] = None
    payment_terms_days: Optional[int] = None
    credit_limit: Optional[float] = None
    credit_used: Optional[float] = None
    return_flag: Optional[bool] = None
    credit_memo_ref: Optional[str] = None
    reversal_date: Optional[str] = None
    related_party_flag: Optional[bool] = None
    source_system: Optional[str] = None
    approver_id: Optional[str] = None
    data_quality: Dict[str, Any] = field(default_factory=dict)

    # --- 拡張フィールド（任意・スキーマは additionalProperties を禁じていない）---
    # 実データにあれば検知精度が上がるが、無くても評価は動く（欠損は data_quality に記録）。
    unit_cost: Optional[float] = None          # 原価。粗利分析（PRICE-003）に使用
    posting_date: Optional[str] = None         # 記帳日。決算後計上（CUTOFF-005/JE-006）に使用
    entry_date: Optional[str] = None           # 入力日。バックデート（JE-005）に使用
    poster_role: Optional[str] = None          # 起票者の職位（JE-003 統制無効化）。'executive'/'manager'/'staff'
    master_change_date: Optional[str] = None   # 直近マスタ変更日（口座/住所）（CUST-002）
    screening_status: Optional[str] = None     # 反社/制裁スクリーニング結果（'hit'/'clear'/None）
    government_customer: Optional[bool] = None  # 政府系顧客（COMPL-005）
    is_journal: Optional[bool] = None          # 明細が仕訳（journal）由来か（JE 系の適用対象）
    receipt_date: Optional[str] = None         # 入金確認日。無ければ入金なしの疑い（FICT-003）
    registry_verified: Optional[bool] = None   # 法人登記/所在の外部確認（CUST-003）。False=確認取れず
    disclosed: Optional[bool] = None           # 関連当事者取引が開示リストに載っているか（CIRC-005）
    officer_related: Optional[bool] = None     # 得意先が役職員と関係あり（CUST-005・利益相反）
    multiple_po: Optional[bool] = None         # 契約が複数履行義務を含む（RECOG-005）
    over_time_progress: Optional[float] = None  # 一定期間履行義務の進捗率 0-1（RECOG-003）
    prev_progress: Optional[float] = None      # 前回報告時の進捗率 0-1（RECOG-003）
    discount_rate: Optional[float] = None      # 適用値引率 0-1（PRICE-004/RETURN-004）
    commission_amount: Optional[float] = None  # 受注に紐づく仲介手数料（COMPL-001）

    # 内部保持: 取込時に検出したデータ品質の注記（missing_fields 等）
    def missing_fields(self) -> List[str]:
        return list(self.data_quality.get("missing_fields", []))

    @property
    def reconciled_to_gl(self) -> Optional[bool]:
        return self.data_quality.get("reconciled_to_gl")

    # 日付の parsed アクセサ（カットオフ三角照合で多用）
    def d_order(self) -> Optional[_dt.date]: return parse_date(self.order_date)
    def d_ship(self) -> Optional[_dt.date]: return parse_date(self.ship_date)
    def d_delivery(self) -> Optional[_dt.date]: return parse_date(self.delivery_date)
    def d_invoice(self) -> Optional[_dt.date]: return parse_date(self.invoice_date)
    def d_recognition(self) -> Optional[_dt.date]: return parse_date(self.revenue_recognition_date)
    def d_reversal(self) -> Optional[_dt.date]: return parse_date(self.reversal_date)
    def d_posting(self) -> Optional[_dt.date]: return parse_date(self.posting_date)
    def d_entry(self) -> Optional[_dt.date]: return parse_date(self.entry_date)
    def d_master_change(self) -> Optional[_dt.date]: return parse_date(self.master_change_date)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SalesTransaction":
        known = {f for f in cls.__dataclass_fields__}  # noqa: SIM118
        kwargs = {k: v for k, v in d.items() if k in known}
        return cls(**kwargs)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        # 拡張フィールドは None のとき出力しない（スキーマ検証をきれいに通す）
        return _clean(d)


@dataclass
class RiskFinding:
    """エンジン／エージェントが生成する所見。アサーション紐付けと根拠が必須。AIは確定できない。"""

    finding_id: str
    transaction_ids: List[str]
    risk_score: float
    assertion: List[str]
    rationale: Dict[str, Any]
    created_by: str  # rule_engine / ml_model / agent
    hitl_status: str = "open"

    entity_ids: List[str] = field(default_factory=list)
    rule_ids: List[str] = field(default_factory=list)
    ml_scores: Dict[str, Any] = field(default_factory=dict)
    severity: Optional[str] = None
    hypothesis_ja: Optional[str] = None
    recommended_review_ja: Optional[str] = None

    def set_hitl_status(self, status: str, *, actor_is_human: bool) -> None:
        """hitl_status を更新する。

        絶対原則 #3（HITL）: confirmed / dismissed は人間のみ。AI は次のいずれも行えない:
          (1) confirmed / dismissed を設定する、
          (2) 人間が確定/棄却した所見を別状態へ戻す（in_review 等に上書きして人間判断を覆す）。
        いずれも PermissionError を送出する。
        """
        if status not in HITL_STATUSES:
            raise ValueError(f"未知の hitl_status: {status!r}")
        if not actor_is_human:
            if status in HUMAN_ONLY_HITL_STATUSES:
                raise PermissionError(
                    f"hitl_status={status!r} は人間のみが設定できる（AIは open/in_review まで）。"
                )
            if self.hitl_status in HUMAN_ONLY_HITL_STATUSES:
                raise PermissionError(
                    f"人間が確定/棄却した所見（{self.hitl_status}）を AI が変更することはできない。"
                )
        self.hitl_status = status

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "RiskFinding":
        known = {f for f in cls.__dataclass_fields__}  # noqa: SIM118
        return cls(**{k: v for k, v in d.items() if k in known})

    def to_dict(self) -> Dict[str, Any]:
        return _clean(asdict(self))


@dataclass
class Evidence:
    """エージェントが収集した外部証憑。すべて read-only。出所・法的基盤・インジェクション検査を保持。"""

    evidence_id: str
    finding_id: str
    type: str
    source: str
    provenance: str
    collected_at: str
    read_only: bool = True  # スキーマ上 const: true
    legal_basis: Optional[str] = None
    injection_flags: List[str] = field(default_factory=list)
    content_summary_ja: Optional[str] = None

    def __post_init__(self) -> None:
        # read_only は常に True（書き込み系証憑は存在しない）
        self.read_only = True

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Evidence":
        known = {f for f in cls.__dataclass_fields__}  # noqa: SIM118
        return cls(**{k: v for k, v in d.items() if k in known})

    def to_dict(self) -> Dict[str, Any]:
        d = _clean(asdict(self))
        d["read_only"] = True
        return d


@dataclass
class AuditLogEntry:
    """改ざん不能の監査ログ1件。WORM＋ハッシュチェーン。"""

    seq: int
    timestamp: str
    actor: str      # human:<id> / agent / system
    action: str     # tool_call / evidence_collected / finding_created / hitl_decision ...
    hash: str
    prev_hash: str
    target: Optional[str] = None
    inputs_hash: Optional[str] = None
    worm: bool = True

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "AuditLogEntry":
        known = {f for f in cls.__dataclass_fields__}  # noqa: SIM118
        return cls(**{k: v for k, v in d.items() if k in known})

    def to_dict(self) -> Dict[str, Any]:
        d = _clean(asdict(self))
        d["worm"] = True
        return d
