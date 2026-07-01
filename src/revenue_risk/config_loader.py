"""config/ の宣言的定義（ルール・シナリオ・検知パラメータ・エンゲージメント）を読み込む。

大原則（rule-authoring スキル）: 検知ロジックはコードに直書きせず、ルールのメタデータ
（アサーション・severity・base_weight・誤検知注記）は YAML に置く。コードはそれを読んで評価する。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from . import paths
from .contracts.models import ASSERTIONS, SEVERITIES


# ---- 型 -------------------------------------------------------------------
@dataclass
class Rule:
    id: str
    category: str
    name_ja: str
    description_ja: str
    assertion: List[str]
    severity: str
    base_weight: float
    detection_ja: str = ""
    applies_to: str = "transaction"
    false_positive_notes_ja: str = ""
    hitl: bool = True
    references: List[str] = field(default_factory=list)


@dataclass
class Scenario:
    id: str
    name_ja: str
    category: str
    modus_operandi_ja: str = ""
    who_ja: str = ""
    focus_points_ja: str = ""
    verification_data_ja: str = ""
    methods: List[str] = field(default_factory=list)
    linked_rules: List[str] = field(default_factory=list)
    connectors: List[str] = field(default_factory=list)
    assertion: List[str] = field(default_factory=list)
    hypothesis_ja: str = ""
    synthetic_test_ja: str = ""


@dataclass
class Catalog:
    version: str
    domain: str
    rules: Dict[str, Rule]
    risk_scoring: Dict[str, Any]
    thresholds: Dict[str, Any]

    def by_category(self, category: str) -> List[Rule]:
        return [r for r in self.rules.values() if r.category == category]

    def severity_multiplier(self, severity: str) -> float:
        mult = self.risk_scoring.get("severity_multiplier", {})
        return float(mult.get(severity, 1.0))

    @property
    def rule_ml_blend(self) -> Dict[str, float]:
        b = self.risk_scoring.get("rule_ml_blend", {})
        return {
            "rule_weight": float(b.get("rule_weight", 0.6)),
            "ml_weight": float(b.get("ml_weight", 0.4)),
        }

    @property
    def high_threshold(self) -> float:
        return float(self.thresholds.get("high", 70))

    @property
    def medium_threshold(self) -> float:
        return float(self.thresholds.get("medium", 40))


@dataclass
class EngagementConfig:
    track: str = "track_a"
    deployment_layer: str = "third_line"
    enforcement: Dict[str, Any] = field(default_factory=dict)
    hitl: Dict[str, Any] = field(default_factory=dict)
    agent_limits: Dict[str, Any] = field(default_factory=dict)
    privacy: Dict[str, Any] = field(default_factory=dict)

    def hard_block_allowlist(self) -> List[str]:
        return list(self.enforcement.get("hard_block_allowlist", []))

    def allows_hard_block(self, rule_id: str) -> bool:
        """1線かつ許可リスト内の高精度決定論ルールのみ硬ブロックできる。"""
        if self.deployment_layer != "first_line":
            return False
        if self.enforcement.get("deterministic_actions") != "hard_block":
            return False
        return rule_id in self.hard_block_allowlist()

    def limit(self, key: str, default: Any) -> Any:
        return self.agent_limits.get(key, default)

    def privacy_enabled(self, key: str) -> bool:
        return bool(self.privacy.get(key, False))


# ---- ローダ ---------------------------------------------------------------
def _read_yaml(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load_catalog(path: Optional[Path] = None) -> Catalog:
    data = _read_yaml(path or paths.rule_catalog_path())
    rules: Dict[str, Rule] = {}
    for raw in data.get("rules", []):
        assertion = raw.get("assertion") or []
        if isinstance(assertion, str):
            assertion = [assertion]
        rule = Rule(
            id=raw["id"],
            category=raw["category"],
            name_ja=raw.get("name_ja", ""),
            description_ja=raw.get("description_ja", ""),
            assertion=list(assertion),
            severity=raw.get("severity", "medium"),
            base_weight=float(raw.get("base_weight", 0)),
            detection_ja=raw.get("detection_ja", ""),
            applies_to=raw.get("applies_to", "transaction"),
            false_positive_notes_ja=raw.get("false_positive_notes_ja", ""),
            hitl=bool(raw.get("hitl", True)),
            references=list(raw.get("references", []) or []),
        )
        rules[rule.id] = rule
    return Catalog(
        version=str(data.get("version", "")),
        domain=str(data.get("domain", "")),
        rules=rules,
        risk_scoring=data.get("risk_scoring", {}) or {},
        thresholds=data.get("thresholds", {}) or {},
    )


def load_scenarios(path: Optional[Path] = None) -> List[Scenario]:
    data = _read_yaml(path or paths.fraud_scenarios_path())
    out: List[Scenario] = []
    for raw in data.get("scenarios", []):
        assertion = raw.get("assertion") or []
        if isinstance(assertion, str):
            assertion = [assertion]
        out.append(
            Scenario(
                id=raw["id"],
                name_ja=raw.get("name_ja", ""),
                category=raw.get("category", ""),
                modus_operandi_ja=raw.get("modus_operandi_ja", ""),
                who_ja=raw.get("who_ja", ""),
                focus_points_ja=raw.get("focus_points_ja", ""),
                verification_data_ja=raw.get("verification_data_ja", ""),
                methods=list(raw.get("methods", []) or []),
                linked_rules=list(raw.get("linked_rules", []) or []),
                connectors=list(raw.get("connectors", []) or []),
                assertion=list(assertion),
                hypothesis_ja=raw.get("hypothesis_ja", ""),
                synthetic_test_ja=raw.get("synthetic_test_ja", ""),
            )
        )
    return out


def load_detection_params(path: Optional[Path] = None) -> Dict[str, Any]:
    p = path or paths.detection_params_path()
    if not Path(p).exists():
        return {}
    return _read_yaml(p)


def load_engagement(path: Optional[Path] = None) -> EngagementConfig:
    p = path or paths.engagement_path()
    if not Path(p).exists():
        return EngagementConfig()
    data = _read_yaml(p)
    return EngagementConfig(
        track=data.get("track", "track_a"),
        deployment_layer=data.get("deployment_layer", "third_line"),
        enforcement=data.get("enforcement", {}) or {},
        hitl=data.get("hitl", {}) or {},
        agent_limits=data.get("agent_limits", {}) or {},
        privacy=data.get("privacy", {}) or {},
    )


# ---- 整合性チェック（rule-authoring スキル: 変更後に検証）------------------
def check_integrity(
    catalog: Optional[Catalog] = None,
    scenarios: Optional[List[Scenario]] = None,
    params: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """設定の整合性を検査し、問題のメッセージ一覧を返す（空なら健全）。"""
    catalog = catalog if catalog is not None else load_catalog()
    scenarios = scenarios if scenarios is not None else load_scenarios()
    params = params if params is not None else load_detection_params()
    problems: List[str] = []

    # 1) ルールID重複（load 時点で辞書化されるので、生データ件数と比較）
    raw = _read_yaml(paths.rule_catalog_path())
    raw_ids = [r["id"] for r in raw.get("rules", [])]
    seen = set()
    for rid in raw_ids:
        if rid in seen:
            problems.append(f"ルールID重複: {rid}")
        seen.add(rid)

    # 2) 各ルールのアサーション・severity の妥当性、誤検知注記の必須
    for rule in catalog.rules.values():
        if not rule.assertion:
            problems.append(f"{rule.id}: assertion が空（アサーション紐付けは必須）")
        for a in rule.assertion:
            if a not in ASSERTIONS:
                problems.append(f"{rule.id}: 未知のアサーション {a!r}")
        if rule.severity not in SEVERITIES:
            problems.append(f"{rule.id}: 未知の severity {rule.severity!r}")
        if not rule.false_positive_notes_ja.strip():
            problems.append(f"{rule.id}: false_positive_notes_ja が空（アラート疲れ対策として必須）")
        if not (0 <= rule.base_weight <= 40):
            problems.append(f"{rule.id}: base_weight {rule.base_weight} が範囲[0,40]外")

    # 3) シナリオの linked_rules が実在するか、assertion 妥当性
    for sc in scenarios:
        for rid in sc.linked_rules:
            if rid not in catalog.rules:
                problems.append(f"{sc.id}: linked_rules の {rid} が rule_catalog に存在しない")
        for a in sc.assertion:
            if a not in ASSERTIONS:
                problems.append(f"{sc.id}: 未知のアサーション {a!r}")
        if not sc.synthetic_test_ja.strip():
            problems.append(f"{sc.id}: synthetic_test_ja が空（検知とテストの単一定義源のため必須）")

    # 4) detection_params の rule_id が実在するか（誤記の検出）
    for rid in (params.get("rules", {}) or {}):
        if rid not in catalog.rules:
            problems.append(f"detection_params: 未知の rule_id {rid}")

    # 5) engagement の hard_block_allowlist が実在ルールか
    eng = load_engagement()
    for rid in eng.hard_block_allowlist():
        if rid not in catalog.rules:
            problems.append(f"engagement.hard_block_allowlist: 未知の rule_id {rid}")

    return problems
