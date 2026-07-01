"""L1: データ取込・統合（ETL）。

受注・出荷・検収・請求・売上仕訳・売掛金・入金・返品/赤伝・各マスタを統合し、
`SalesTransaction` に正規化する。全件主張の裏付けとして取込時に:
  (a) 売上計上額と総勘定元帳の突合（reconciled_to_gl）
  (b) 連番・欠番チェック
  (c) 期間網羅の確認
  (d) 必須項目欠損の記録（data_quality.missing_fields）
を行い、欠損は所見に `data_quality` フラグとして残す（docs/architecture.md §4）。
"""
from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..contracts.models import SalesTransaction
from ..contracts.validation import SchemaValidator

# SalesTransaction の型ヒント（CSV は全て文字列で来るため、ここでキャストする）
_FLOAT_FIELDS = {
    "amount", "quantity", "unit_price", "credit_limit", "credit_used", "unit_cost",
    "over_time_progress", "prev_progress", "discount_rate", "commission_amount",
}
_INT_FIELDS = {"payment_terms_days"}
_BOOL_FIELDS = {
    "return_flag", "related_party_flag", "government_customer", "is_journal",
    "registry_verified", "disclosed", "officer_related", "multiple_po",
}
_REQUIRED = ("transaction_id", "entity_id", "period", "customer_id", "revenue_recognition_date", "amount", "currency")


@dataclass
class IngestResult:
    transactions: List[SalesTransaction]
    invalid_count: int = 0
    schema_errors: Dict[str, List[str]] = field(default_factory=dict)  # "row{i}:{tid}" -> errors
    gl_reconciliation: List[Dict[str, Any]] = field(default_factory=list)
    sequence_gaps: List[str] = field(default_factory=list)
    sequence_duplicates: List[str] = field(default_factory=list)  # 再利用された連番（二重計上/架空の兆候）
    period_coverage: List[str] = field(default_factory=list)
    missing_periods: List[str] = field(default_factory=list)  # expected_periods 指定時の欠落期間
    missing_summary: Dict[str, int] = field(default_factory=dict)  # field -> 欠損件数

    def reconciled(self) -> bool:
        """全区分が GL と一致したか（GL 未提供なら None 扱いで False を返さない）。"""
        if not self.gl_reconciliation:
            return False
        return all(r.get("reconciled") for r in self.gl_reconciliation)


def _to_bool(v: Any) -> Optional[bool]:
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in ("true", "1", "yes", "y", "t"):
        return True
    if s in ("false", "0", "no", "n", "f"):
        return False
    return None


def _to_number(v: Any, as_int: bool = False) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        if as_int:
            return int(float(v))
        return float(v)
    except (TypeError, ValueError):
        return None


def _cast_record(raw: Dict[str, Any]) -> Dict[str, Any]:
    """CSV/JSON の1レコードを型キャストして SalesTransaction 用の dict に。"""
    out: Dict[str, Any] = {}
    for k, v in raw.items():
        if v is None:
            continue
        if isinstance(v, str) and v.strip() == "":
            continue
        if k in _FLOAT_FIELDS:
            out[k] = _to_number(v)
        elif k in _INT_FIELDS:
            out[k] = _to_number(v, as_int=True)
        elif k in _BOOL_FIELDS:
            out[k] = _to_bool(v)
        else:
            out[k] = v
    return out


def _split_id(tid: str) -> Optional[tuple]:
    """ID を (系列プレフィックス, 末尾連番) に分解する。連番が無ければ None。

    例: 'INV-2024-001' -> ('INV-2024-', 1)、'INV-002' -> ('INV-', 2)。
    系列（請求/赤伝/受注 等）が混在しても、欠番はプレフィックス単位で判定する。
    """
    s = str(tid)
    m = re.search(r"^(.*?)(\d+)\s*$", s)
    if not m:
        return None
    return (m.group(1), int(m.group(2)))


def ingest_records(
    records: List[Dict[str, Any]],
    *,
    validator: Optional[SchemaValidator] = None,
    gl_totals: Optional[Dict[Tuple[str, str], float]] = None,
    sequence_key: str = "transaction_id",
    gl_tolerance: float = 0.01,
    expected_periods: Optional[Sequence[str]] = None,
) -> IngestResult:
    """dict のリストを取り込み、正規化・検証・品質チェックを行う。

    gl_totals: {(entity_id, period): 期待売上合計} を与えると reconciled_to_gl を判定する。
    expected_periods: 期待される会計期間を与えると、欠落期間（網羅性ギャップ）を検出する。
    """
    validator = validator or SchemaValidator()
    txns: List[SalesTransaction] = []
    schema_errors: Dict[str, List[str]] = {}
    missing_summary: Dict[str, int] = {}
    invalid = 0

    for row_idx, raw in enumerate(records):
        # 壊れた1件で母集団評価を止めない。レコード単位の例外は捕捉し invalid として記録する。
        try:
            cast = _cast_record(raw)
            # data_quality は mapping のみ受け付ける（不正な形状は品質エラーとして記録）
            raw_dq = cast.get("data_quality")
            dq_shape_error = raw_dq is not None and not isinstance(raw_dq, dict)
            dq = dict(raw_dq) if isinstance(raw_dq, dict) else {}
            # 必須欠損を data_quality に記録
            missing = [f for f in _REQUIRED if not cast.get(f) and cast.get(f) != 0]
            if missing:
                dq["missing_fields"] = sorted(set(dq.get("missing_fields", []) + list(missing)))
                for f in missing:
                    missing_summary[f] = missing_summary.get(f, 0) + 1
            if dq:
                cast["data_quality"] = dq
            elif "data_quality" in cast:
                cast.pop("data_quality")  # 不正形状は落とす（構築を壊さない）

            txn = SalesTransaction.from_dict(cast)
            errs = list(validator.errors("SalesTransaction", txn.to_dict()))
            if missing:
                errs.append(f"必須欠落: {', '.join(missing)}")
            if dq_shape_error:
                errs.append("data_quality が object でない（無視して継続）")
        except Exception as exc:  # 予期しない不正レコードでも母集団評価を止めない
            txn = SalesTransaction(transaction_id=str((raw or {}).get("transaction_id", "")) or f"<row{row_idx}>")
            errs = [f"取込例外: {type(exc).__name__}: {exc}"]

        # スキーマ違反・例外は棄却せず記録し、母集団評価は続行（行番号キーで衝突を防ぐ）
        if errs:
            invalid += 1
            schema_errors[f"row{row_idx}:{txn.transaction_id or '?'}"] = errs
        txns.append(txn)

    result = IngestResult(
        transactions=txns,
        invalid_count=invalid,
        schema_errors=schema_errors,
        missing_summary=missing_summary,
    )

    # (a) GL 突合
    if gl_totals:
        booked: Dict[Tuple[str, str], float] = {}
        for t in txns:
            key = (t.entity_id, t.period)
            booked[key] = booked.get(key, 0.0) + float(t.amount or 0.0)
        recon: List[Dict[str, Any]] = []
        keys = set(gl_totals) | set(booked)
        for key in sorted(keys):
            exp = float(gl_totals.get(key, 0.0))
            got = float(booked.get(key, 0.0))
            ok = abs(exp - got) <= max(gl_tolerance, abs(exp) * 1e-6)
            recon.append(
                {
                    "entity_id": key[0],
                    "period": key[1],
                    "gl_total": exp,
                    "booked_total": got,
                    "difference": round(got - exp, 4),
                    "reconciled": ok,
                }
            )
        result.gl_reconciliation = recon
        # 突合済みフラグを各取引に反映
        recon_map = {(r["entity_id"], r["period"]): r["reconciled"] for r in recon}
        for t in txns:
            ok = recon_map.get((t.entity_id, t.period))
            if ok is not None:
                dq = dict(t.data_quality)
                dq["reconciled_to_gl"] = bool(ok)
                t.data_quality = dq

    # (b) 連番・欠番・重複チェック（系列プレフィックス単位）
    result.sequence_gaps, result.sequence_duplicates = _detect_gaps(txns, sequence_key)

    # (c) 期間網羅（observed）。expected_periods 指定時は欠落期間も報告する。
    observed = sorted({t.period for t in txns if t.period})
    result.period_coverage = observed
    if expected_periods:
        obs = set(observed)
        result.missing_periods = [p for p in expected_periods if p not in obs]

    return result


def _detect_gaps(txns: List[SalesTransaction], sequence_key: str) -> tuple:
    """連番の欠番と重複を、系列（プレフィックス）ごとに検出する。

    戻り値: (gaps, duplicates)。無関係な系列（請求/赤伝/受注）を混ぜて幻の欠番を作らないよう、
    プレフィックス単位で判定する。重複（再利用番号）は二重計上・架空の兆候として別途報告する。
    """
    by_series: Dict[str, List[int]] = {}
    for t in txns:
        val = getattr(t, sequence_key, None)
        parts = _split_id(val) if val is not None else None
        if parts is not None:
            by_series.setdefault(parts[0], []).append(parts[1])

    gaps: List[str] = []
    duplicates: List[str] = []
    for prefix, nums in by_series.items():
        # 重複（再利用番号）
        seen: set = set()
        dups: set = set()
        for n in nums:
            (dups if n in seen else seen).add(n)
        for n in sorted(dups):
            duplicates.append(f"{prefix}{n}")
        # 欠番（重複を畳んだ後の連続性で判定）
        uniq = sorted(seen)
        if len(uniq) < 2:
            continue
        for a, b in zip(uniq, uniq[1:]):
            if b - a > 1:
                missing = b - a - 1
                if missing <= 5:
                    gaps.extend(f"{prefix}{x}" for x in range(a + 1, b))
                else:
                    gaps.append(f"{prefix}{a + 1}..{prefix}{b - 1}({missing}件)")
    return gaps, duplicates


def load_transactions(path: str | Path, **kwargs: Any) -> IngestResult:
    """CSV または JSON ファイルから取引を読み込み、取り込む。"""
    p = Path(path)
    if p.suffix.lower() == ".json":
        with open(p, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        records = data.get("transactions", data) if isinstance(data, dict) else data
    else:
        with open(p, "r", encoding="utf-8-sig", newline="") as fh:
            records = list(csv.DictReader(fh))
    return ingest_records(records, **kwargs)
