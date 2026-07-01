"""ガイドデモのナレーション音声を生成する（高品質ニューラルTTS = edge-tts）。

web/data/data.js から件数（母集団・選別）を読み、demo.js の字幕と一致する文面を組み立てて
web/data/narration/step-XX.mp3 を書き出す。ネットワークが無い場合はスキップ（demo.js が
ブラウザ内蔵TTSへ自動フォールバックする）。

  python scripts/generate_narration.py
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VOICE = "ja-JP-NanamiNeural"   # 中立・プロフェッショナルな女性ニューラル音声
RATE = "-3%"                    # わずかに落ち着いたテンポ


def load_counts() -> dict:
    data_js = (ROOT / "web" / "data" / "data.js").read_text(encoding="utf-8")
    payload = data_js.split("window.DEMO_DATA = ", 1)[1].rsplit(";", 1)[0]
    d = json.loads(payload)
    return {
        "total": f"{int(d['population']['transaction_count']):,}",
        "selected": f"{int(d['funnel']['selected']):,}",
    }


def steps(c: dict) -> list:
    t, s = c["total"], c["selected"]
    return [
        ("01", "これは、売上・収益リスクを全件ベースで評価する、監査レビューコンソールのデモです。表示しているデータはすべて合成データで、実在の企業や取引とは関係ありません。"),
        ("02", f"まず、{t}件すべての取引を、ルール・機械学習・ネットワーク分析で低コストに評価します。そのうえで、高リスクな{s}件だけを、エージェントの深掘り対象に絞り込みます。"),
        ("03", "すべての所見は、発生・網羅性・正確性・期間帰属といった、財務諸表アサーションに紐づきます。監査人が何を検証すべきかに、直結させる設計です。"),
        ("04", "総勘定元帳との突合、連番の欠番、期間の網羅を確認します。これが、全件を評価したという主張を裏付けます。"),
        ("05", f"こちらが、高リスク所見の一覧です。重要度、アサーション、カテゴリで絞り込めます。今は、重大と高の{s}件を表示しています。"),
        ("06", "一件を開きます。受注から出荷、検収、計上までの日付を三角照合します。この取引は、出荷日が収益認識日より後になっており、未出荷での計上が疑われます。"),
        ("07", "エージェントは、読み取り専用で外部証憑を収集します。契約や通信、出荷、入金を確認し、証憑に埋め込まれた命令には従わず、一次データとの矛盾を検出します。"),
        ("08", "確定と棄却は、人間だけが行えます。エーアイは、所見の提示と、レビュー中までに限られます。ここでは、人間の判断として確定を記録しました。"),
        ("09", "エージェントの全行動は、改ざん不能な監査ログに記録されます。ワームとハッシュチェーンにより、一件でも改ざんや欠番があれば検知できます。"),
        ("10", "最後に、経営者や監査役に向けたサマリを生成します。所見はあくまでエーアイによる提示であり、確定や是正の判断は、独立した人間が行います。"),
        ("11", "以上がデモです。全件評価、アサーションへの紐付け、説明可能性、人間による確定、そして改ざん不能なログを、一つのワークフローに統合しています。"),
    ]


async def synth(text: str, path: Path) -> None:
    import edge_tts
    comm = edge_tts.Communicate(text, VOICE, rate=RATE)
    await comm.save(str(path))


async def main() -> int:
    try:
        import edge_tts  # noqa: F401
    except Exception:
        print("edge-tts 未インストール。`pip install edge-tts` 後に再実行してください。", file=sys.stderr)
        return 1
    counts = load_counts()
    out = ROOT / "web" / "data" / "narration"
    out.mkdir(parents=True, exist_ok=True)
    ok = 0
    for sid, text in steps(counts):
        path = out / f"step-{sid}.mp3"
        try:
            await synth(text, path)
            ok += 1
            print(f"  ✔ step-{sid}.mp3 ({path.stat().st_size // 1024} KB)")
        except Exception as exc:
            print(f"  ✘ step-{sid}: {type(exc).__name__}: {exc}", file=sys.stderr)
    print(f"生成 {ok} / {len(steps(counts))} 件 -> {out}")
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
