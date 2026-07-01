"""リポジトリ内リソース（config/・docs/）の場所解決。

ソースツリーから実行する前提。`src/revenue_risk/paths.py` から見て parents[2] がリポジトリルート。
環境変数 REVENUE_RISK_CONFIG_DIR / REVENUE_RISK_ROOT で上書き可能。
"""
from __future__ import annotations

import os
from pathlib import Path

_THIS = Path(__file__).resolve()


def repo_root() -> Path:
    env = os.environ.get("REVENUE_RISK_ROOT")
    if env:
        return Path(env).resolve()
    return _THIS.parents[2]


def config_dir() -> Path:
    env = os.environ.get("REVENUE_RISK_CONFIG_DIR")
    if env:
        return Path(env).resolve()
    return repo_root() / "config"


def rules_dir() -> Path:
    return config_dir() / "rules"


def schema_path() -> Path:
    return config_dir() / "schemas" / "data_contracts.json"


def rule_catalog_path() -> Path:
    return rules_dir() / "rule_catalog.yaml"


def fraud_scenarios_path() -> Path:
    return rules_dir() / "fraud_scenarios.yaml"


def detection_params_path() -> Path:
    return rules_dir() / "detection_params.yaml"


def engagement_path() -> Path:
    return config_dir() / "engagement.yaml"
