"""コマンドラインインタフェース。

  revenue-risk demo [--out DIR]          合成不正データで一気通貫デモ（検出力を確認）
  revenue-risk run --input FILE [--out D] CSV/JSON の売上明細を評価しレポート出力
  revenue-risk check-config               config/ の整合性チェック
  revenue-risk verify-audit FILE          監査ログのハッシュチェーンを検証
"""
from __future__ import annotations

import argparse
import sys
from typing import List, Optional

from . import config_loader as cl
from .audit.audit_log import AuditLog
from .etl.ingest import load_transactions, ingest_records
from .pipeline import Pipeline


def _cmd_check_config(args: argparse.Namespace) -> int:
    problems = cl.check_integrity()
    catalog = cl.load_catalog()
    scenarios = cl.load_scenarios()
    print(f"ルール: {len(catalog.rules)} 件 / シナリオ: {len(scenarios)} 件")
    if problems:
        print(f"整合性の問題 {len(problems)} 件:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("整合性チェック: 問題なし ✔")
    return 0


def _summarize(result, out: Optional[str]) -> None:
    fn = result.funnel.stats
    print(f"取引: {fn['total']} 件 / 高リスク選別: {fn['selected']} 件 ({fn['selection_rate']:.1%})")
    print(f"所見: {len(result.findings)} 件 / 監査ログ: {len(result.audit)} エントリ")
    chain = result.audit.verify()
    print(f"監査ログ整合性: {'OK ✔' if chain.valid else 'NG ✘ ' + '; '.join(chain.problems)}")
    if result.agent_result:
        print(f"エージェント: {result.agent_result.stats}")
    print("\n重要度上位の所見:")
    for f in result.findings[:8]:
        print(f"  [{f.severity or '-':8}] {f.finding_id:14} score={f.risk_score:5.1f} "
              f"assert={','.join(f.assertion)} rules={','.join(f.rule_ids)}")
    if out:
        paths = result.report.save(out)
        print(f"\nレポートを出力しました: {out}")
        for k, v in paths.items():
            print(f"  {k}: {v}")


def _cmd_demo(args: argparse.Namespace) -> int:
    from .synthetic.generator import SyntheticGenerator
    from .agent.connectors import MockConnectorProvider, ReadOnlyConnectors

    gen = SyntheticGenerator(seed=args.seed)
    txns, injected = gen.generate()
    ingest = ingest_records([t.to_dict() for t in txns])
    conns = ReadOnlyConnectors(MockConnectorProvider(transactions=ingest.transactions, comms_store=gen.comms_store))
    result = Pipeline().run(ingest, connectors=conns, approvals={"G0", "G1"})
    print(f"合成不正シナリオ: {len(injected)} 種を混入")
    flagged_txn = {t for f in result.findings for t in f.transaction_ids}
    detected = sum(1 for inj in injected if set(inj.transaction_ids) & flagged_txn)
    print(f"検出（recall）: {detected}/{len(injected)} シナリオ\n")
    _summarize(result, args.out)
    missed = [inj.scenario_id for inj in injected if not (set(inj.transaction_ids) & flagged_txn)]
    if missed:
        print(f"\n未検出シナリオ: {missed}")
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    ingest = load_transactions(args.input)
    result = Pipeline().run(ingest, approvals=set(args.approve or []))
    _summarize(result, args.out)
    return 0


def _cmd_verify_audit(args: argparse.Namespace) -> int:
    import json
    from pathlib import Path

    log = AuditLog.load(args.file)
    expected_len = expected_head = None
    cp_path = Path(args.checkpoint) if args.checkpoint else AuditLog.checkpoint_path(args.file)
    if cp_path.exists():
        cp = json.loads(cp_path.read_text(encoding="utf-8"))
        expected_len, expected_head = cp.get("length"), cp.get("head_hash")
        print(f"アンカー（checkpoint）を照合: {cp_path.name}")
    else:
        print("注: checkpoint が無いため末尾の切り詰め/最終エントリ改ざんは検知できません（内部連結のみ検証）")
    chain = log.verify(expected_length=expected_len, expected_head_hash=expected_head)
    if chain.valid:
        print(f"監査ログ整合性: OK ✔（{chain.entries_checked} エントリ）")
        return 0
    print(f"監査ログ整合性: NG ✘（最初の破損 seq={chain.first_broken_seq}）")
    for p in chain.problems:
        print(f"  - {p}")
    return 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="revenue-risk", description="売上・収益リスク分析 自律AIエージェント")
    sub = p.add_subparsers(dest="command", required=True)

    d = sub.add_parser("demo", help="合成不正データで一気通貫デモ")
    d.add_argument("--out", help="レポート出力ディレクトリ")
    d.add_argument("--seed", type=int, default=42)
    d.set_defaults(func=_cmd_demo)

    r = sub.add_parser("run", help="CSV/JSON の売上明細を評価")
    r.add_argument("--input", required=True, help="入力 CSV または JSON")
    r.add_argument("--out", help="レポート出力ディレクトリ")
    r.add_argument("--approve", nargs="*", help="承認する HITL ゲート（例: G0 G1）")
    r.set_defaults(func=_cmd_run)

    c = sub.add_parser("check-config", help="config/ の整合性チェック")
    c.set_defaults(func=_cmd_check_config)

    v = sub.add_parser("verify-audit", help="監査ログのハッシュチェーン検証")
    v.add_argument("file", help="audit_log.json")
    v.add_argument("--checkpoint", help="アンカー（length/head_hash）JSON。省略時は同名 .checkpoint.json を探す")
    v.set_defaults(func=_cmd_verify_audit)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
