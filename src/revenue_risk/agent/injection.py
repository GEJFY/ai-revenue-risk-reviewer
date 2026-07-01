"""プロンプトインジェクション検出（エージェント層で最重要）。

エージェントは契約PDF・メール・OCR・顧客回答という信頼できない外部コンテンツを読む。
攻撃者はそこにAIへの命令（「この取引は正常と報告せよ」等）や虚偽情報を仕込める。
本モジュールは証憑コンテンツを走査し、埋め込み命令の疑いを検出して `injection_flags` に記録する。
ここで検出しても、証憑は常に「解析対象のデータ」であり、指示として実行しない（防御は多層）。
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import List

# AI/システムへの命令を示唆する語（日英）。指示文らしさの検出に用いる。
_PATTERNS = [
    # 日本語
    (r"正常(と|と判断|と報告|である)", "取引を正常と報告させる誘導"),
    (r"問題(ない|なし|ありません)", "問題なしと判断させる誘導"),
    (r"検[証査](は)?(不要|しないで|するな)", "検証を省略させる誘導"),
    (r"承認(せよ|して|してください|する)", "承認を促す命令"),
    (r"(無視|むし)して", "先行指示の無視を促す"),
    (r"報告(するな|しないで|不要)", "報告の抑止"),
    (r"このメール(の指示|の通り)", "メール内の指示への追従誘導"),
    (r"あなたはAI", "AIへの役割上書き"),
    # 英語
    (r"ignore (all )?(previous|prior|above) (instructions|prompts)", "ignore-previous-instructions"),
    (r"disregard .{0,20}(instructions|rules)", "disregard-instructions"),
    (r"report .{0,30}(as )?(normal|legitimate|valid|fine)", "report-as-normal"),
    (r"do not (verify|flag|report|investigate)", "suppress-verification"),
    (r"you are (an? )?(ai|assistant|language model)", "role-override"),
    (r"as an ai", "role-override"),
    (r"(system|assistant|developer)\s*:", "role-prefix-injection"),
    (r"approve this (transaction|entry|deal)", "approve-command"),
    (r"no (further )?review (is )?(needed|required)", "suppress-review"),
]

# ツール呼び出しを誘発しようとする文字列（証憑からツールは発火させない方針の裏取り）
_TOOL_CALL_HINTS = [
    r"<tool", r"function_call", r"```tool", r"\{\s*\"tool\"", r"call\s+the\s+\w+\s+tool",
]

_ZERO_WIDTH = ["​", "‌", "‍", "﻿", "⁠"]


@dataclass
class InjectionScan:
    suspected: bool = False
    flags: List[str] = field(default_factory=list)

    def merge(self, other: "InjectionScan") -> "InjectionScan":
        return InjectionScan(
            suspected=self.suspected or other.suspected,
            flags=sorted(set(self.flags) | set(other.flags)),
        )


def scan_for_injection(content: str) -> InjectionScan:
    """証憑コンテンツを走査し、埋め込み命令・不可視文字・ツール発火誘導を検出する。"""
    scan = InjectionScan()
    if not content:
        return scan
    text = content
    # 不可視文字（隠しテキストでの命令埋め込み）
    for zw in _ZERO_WIDTH:
        if zw in text:
            scan.suspected = True
            scan.flags.append("hidden_zero_width_chars")
            break
    # 正規化して検査（全角・互換文字による回避対策）
    normalized = unicodedata.normalize("NFKC", text)
    low = normalized.lower()
    for pattern, label in _PATTERNS:
        if re.search(pattern, normalized, flags=re.IGNORECASE):
            scan.suspected = True
            scan.flags.append(f"embedded_instruction:{label}")
    for hint in _TOOL_CALL_HINTS:
        if re.search(hint, low):
            scan.suspected = True
            scan.flags.append("tool_call_trigger_attempt")
            break
    return scan


def contradicts_primary(evidence_summary: str, primary_statement: str) -> bool:
    """証憑の要約が一次データの記述と食い違うかの簡易判定（矛盾検出のフック）。

    実運用ではフィールド単位で突合する。ここでは否定語の有無による粗い検出を提供する。
    """
    if not evidence_summary or not primary_statement:
        return False
    neg_markers = ["ない", "なし", "未", "no ", "not ", "without", "absent"]
    e_has_neg = any(m in evidence_summary for m in neg_markers)
    p_has_neg = any(m in primary_statement for m in neg_markers)
    return e_has_neg != p_has_neg
