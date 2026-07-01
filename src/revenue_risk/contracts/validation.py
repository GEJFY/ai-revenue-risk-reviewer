"""JSON Schema による契約検証。`config/schemas/data_contracts.json` の definitions を使う。

スキーマ違反は実行時に検出する。ETL では検証失敗を data_quality フラグに落とし、
分析は継続できるようにする（全件主張のため、壊れた1件で母集団評価を止めない）。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..paths import schema_path


class ContractError(ValueError):
    """データ契約（スキーマ）違反。"""


class SchemaValidator:
    """data_contracts.json の各 definition に対して dict を検証する。"""

    def __init__(self, schema_file: Optional[Path] = None) -> None:
        self._path = Path(schema_file) if schema_file else schema_path()
        with open(self._path, "r", encoding="utf-8") as fh:
            self._schema: Dict[str, Any] = json.load(fh)
        self._defs: Dict[str, Any] = self._schema.get("definitions", {})
        # jsonschema は任意依存にしない（必須依存）。無ければ簡易検証にフォールバック。
        try:
            import jsonschema  # noqa: F401

            self._jsonschema = jsonschema
        except Exception:  # pragma: no cover - 通常は入っている
            self._jsonschema = None

    @property
    def definitions(self) -> Dict[str, Any]:
        return self._defs

    def _schema_for(self, name: str) -> Dict[str, Any]:
        if name not in self._defs:
            raise KeyError(f"未知の definition: {name}")
        return {
            "$schema": self._schema.get("$schema", "http://json-schema.org/draft-07/schema#"),
            "definitions": self._defs,
            "allOf": [{"$ref": f"#/definitions/{name}"}],
        }

    def errors(self, name: str, obj: Dict[str, Any]) -> List[str]:
        """検証エラーのメッセージ一覧を返す（空なら適合）。"""
        if self._jsonschema is not None:
            schema = self._schema_for(name)
            validator_cls = self._jsonschema.Draft7Validator
            validator = validator_cls(schema)
            msgs = []
            for err in sorted(validator.iter_errors(obj), key=lambda e: list(e.path)):
                loc = "/".join(str(p) for p in err.path) or "(root)"
                msgs.append(f"{loc}: {err.message}")
            return msgs
        return self._fallback_errors(name, obj)

    def is_valid(self, name: str, obj: Dict[str, Any]) -> bool:
        return not self.errors(name, obj)

    def validate(self, name: str, obj: Dict[str, Any]) -> None:
        errs = self.errors(name, obj)
        if errs:
            raise ContractError(f"{name} がスキーマに適合しません: " + "; ".join(errs))

    # --- jsonschema が無い環境向けの最小フォールバック（required と enum のみ）---
    def _fallback_errors(self, name: str, obj: Dict[str, Any]) -> List[str]:
        spec = self._defs[name]
        msgs: List[str] = []
        for req in spec.get("required", []):
            if req not in obj or obj[req] is None:
                msgs.append(f"{req}: 必須項目が欠落")
        props = spec.get("properties", {})
        for key, val in obj.items():
            pspec = props.get(key)
            if not pspec:
                continue
            enum = pspec.get("enum")
            if enum is not None and val is not None and val not in enum:
                msgs.append(f"{key}: {val!r} は許容値 {enum} に含まれない")
        return msgs
