"""改ざん不能の監査ログ（WORM＋ハッシュチェーン）。

入力明細 → 適用ルール/モデル/ネットワーク判定 → 収集証憑 → スコア → アサーション → 結論 →
HITL 判断 の連鎖を記録する（docs/governance.md §4）。各エントリは直前の prev_hash を含み、
自身の hash で連結する。1件でも改ざんすると以降の鎖が壊れ、verify() が検知する。
連番（seq）の欠番も改ざんの兆候として検出する。
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ..contracts.models import AuditLogEntry

GENESIS_HASH = "0" * 64


def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def hash_inputs(inputs: Any) -> str:
    """入力のハッシュ（内容は保持せず参照可能性のみ）。"""
    if inputs is None:
        return ""
    return _sha256(_canonical(inputs))


def _entry_hash(seq: int, timestamp: str, actor: str, action: str,
                target: Optional[str], inputs_hash: Optional[str], prev_hash: str) -> str:
    payload = _canonical({
        "seq": seq,
        "timestamp": timestamp,
        "actor": actor,
        "action": action,
        "target": target or "",
        "inputs_hash": inputs_hash or "",
        "prev_hash": prev_hash,
    })
    return _sha256(payload)


@dataclass
class ChainVerification:
    valid: bool
    entries_checked: int
    problems: List[str] = field(default_factory=list)
    first_broken_seq: Optional[int] = None


class AuditLog:
    """append-only の監査ログ。ハッシュチェーンで完全性を保証する。"""

    def __init__(self, clock: Optional[Callable[[], str]] = None) -> None:
        self._entries: List[AuditLogEntry] = []
        self._clock = clock or (lambda: _dt.datetime.now(_dt.timezone.utc).isoformat())

    def __len__(self) -> int:
        return len(self._entries)

    @property
    def entries(self) -> List[AuditLogEntry]:
        # 参照用（WORM: 追記のみ。呼び出し側での書換は verify() で検出される）
        return list(self._entries)

    def append(self, actor: str, action: str, target: Optional[str] = None, inputs: Any = None) -> AuditLogEntry:
        seq = len(self._entries) + 1
        timestamp = self._clock()
        prev_hash = self._entries[-1].hash if self._entries else GENESIS_HASH
        ih = hash_inputs(inputs)
        h = _entry_hash(seq, timestamp, actor, action, target, ih, prev_hash)
        entry = AuditLogEntry(
            seq=seq,
            timestamp=timestamp,
            actor=actor,
            action=action,
            hash=h,
            prev_hash=prev_hash,
            target=target,
            inputs_hash=ih or None,
        )
        self._entries.append(entry)
        return entry

    def head_hash(self) -> str:
        """末尾（最新）エントリの hash。GENESIS_HASH なら空。"""
        return self._entries[-1].hash if self._entries else GENESIS_HASH

    def checkpoint(self) -> Dict[str, Any]:
        """チェーンのアンカー（長さ＋末尾hash）。別途 WORM 保管し verify() に渡すことで、
        末尾の切り詰め・最終エントリの書換えを検知できる（内部連結だけでは末尾は守れない）。"""
        return {"length": len(self._entries), "head_hash": self.head_hash()}

    def verify(
        self,
        expected_length: Optional[int] = None,
        expected_head_hash: Optional[str] = None,
    ) -> ChainVerification:
        """ハッシュチェーンを検証する。

        内部連結（各エントリの自己hash・prev_hash・seq連番）に加え、任意で外部アンカー
        （expected_length / expected_head_hash、= 事前に別保管した checkpoint）を照合する。
        アンカーを与えると、後続を持たない末尾エントリの改ざんや末尾の切り詰めも検知できる。
        """
        problems: List[str] = []
        first_broken: Optional[int] = None

        def mark(seq: Optional[int]) -> None:
            nonlocal first_broken
            if first_broken is None and seq is not None:
                first_broken = seq

        prev_hash = GENESIS_HASH
        for i, e in enumerate(self._entries):
            expected_seq = i + 1
            if e.seq != expected_seq:
                problems.append(f"seq不整合: 位置{i}で seq={e.seq}（期待{expected_seq}）欠番/改ざんの兆候")
                mark(expected_seq)
            if e.prev_hash != prev_hash:
                problems.append(f"seq {e.seq}: prev_hash が直前の hash と不一致（鎖の断絶）")
                mark(expected_seq)
            recomputed = _entry_hash(e.seq, e.timestamp, e.actor, e.action, e.target, e.inputs_hash, e.prev_hash)
            if recomputed != e.hash:
                problems.append(f"seq {e.seq}: hash がエントリ内容と不一致（改ざんの兆候）")
                mark(expected_seq)
            prev_hash = e.hash

        # 外部アンカー照合（末尾切り詰め・最終エントリ書換えの検知）
        if expected_length is not None and expected_length != len(self._entries):
            problems.append(
                f"チェーン長不一致: 実{len(self._entries)} 期待{expected_length}（末尾の切り詰め/追加の兆候）"
            )
            mark(min(expected_length, len(self._entries)) + 1)
        if expected_head_hash is not None and expected_head_hash != self.head_hash():
            problems.append("末尾hash不一致: 最終エントリの改ざん/切り詰めの兆候")
            mark(len(self._entries))

        return ChainVerification(
            valid=not problems,
            entries_checked=len(self._entries),
            problems=problems,
            first_broken_seq=first_broken,
        )

    # ---- 永続化 ---------------------------------------------------------
    def to_list(self) -> List[Dict[str, Any]]:
        return [e.to_dict() for e in self._entries]

    def save(self, path: str | Path, write_checkpoint: bool = True) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(self.to_list(), fh, ensure_ascii=False, indent=2)
        if write_checkpoint:
            # 末尾改ざん検知用アンカー。真の WORM 保証には別の保護領域へ退避すること。
            with open(self.checkpoint_path(p), "w", encoding="utf-8") as fh:
                json.dump(self.checkpoint(), fh, ensure_ascii=False, indent=2)

    @staticmethod
    def checkpoint_path(log_path: str | Path) -> Path:
        p = Path(log_path)
        return p.with_name(p.stem + ".checkpoint.json")

    @classmethod
    def load(cls, path: str | Path) -> "AuditLog":
        log = cls()
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        log._entries = [AuditLogEntry.from_dict(d) for d in data]
        return log
