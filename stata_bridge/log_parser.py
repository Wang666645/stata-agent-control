"""Stata batch auto-log 解析。

输入: StataMP-64.exe /e do 生成的日志文本（或字节）。
输出: 结构化的结果字典:
  - text        : 去掉启动横幅后的完整日志（含命令回显）
  - rcs         : 出现的全部错误码 r(601) 等
  - error_blocks: 每个错误码附近的上下文（前 4 行 + 后 2 行）
  - tables      : 尽力提取的统计表格（OLS/xtreg/logit 等输出块）
  - tail        : 日志尾部（供 agent 快速浏览）
  - ok          : 是否无错误
"""
from __future__ import annotations

import re

_RC_RE = re.compile(r"\br\((\d{1,4})\);")
_ECHO_RE = re.compile(r"^\.\s|^\.$|^> ")
_BORDER_RE = re.compile(r"^-{12,}$")
_STRONG_HITS = ("Coef.", "Std. err.", "Odds ratio", "dy/dx",
                "Coefficient", "Robust std. err.", "Variable |")

# 常见 rc 码解释（速查用）
RC_HINTS = {
    1: "do 文件主动 exit",
    110: "变量未找到（拼写/大小写？）",
    111: "变量名不合法",
    198: "语法无效（检查命令写法）",
    601: "文件未找到（路径/文件名错误）",
    603: "文件无法打开（路径错误/权限/文件被占用）",
    608: "文件无法修改或删除（被占用/只读）",
    2000: "没有观测值",
    2001: "没有变量",
    2002: "变量没有观测值",
    2004: "观测值不足",
    430: "外部命令未找到（需 ssc install，如 reghdfe/estout）",
    451: "面板内时间重复（xtset 报错: 先 duplicates report 检查 id-year 唯一性）",
    1114: "变量类型错误（string 不能回归：先 encode/destring）",
    4500: "数据未按面板声明（先 xtset）",
}


def _decode(raw: bytes) -> str:
    """日志可能是 UTF-8 或 GB18030（中文 Windows 老版本），择优解码。"""
    def score(text: str) -> int:
        return text.count("\ufffd")
    best, best_score = None, None
    for enc in ("utf-8-sig", "gb18030"):
        try:
            text = raw.decode(enc)
        except (UnicodeDecodeError, ValueError):
            continue
        sc = score(text)
        if best is None or sc < best_score:
            best, best_score = text, sc
    if best is None:
        best = raw.decode("utf-8", errors="replace")
    return best


def _strip_banner(lines: list) -> list:
    """去掉启动横幅：从第一条 '. xxx' 命令回显开始保留。"""
    for i, line in enumerate(lines):
        if line.startswith(". ") or line == ".":
            return lines[i:]
    # 找不到命令回显时（异常崩溃），去掉明显横幅行
    for i, line in enumerate(lines):
        if "Stata license" in line or "Statistics and Data Science" in line:
            # 从该行往后 20 行内找空行后开始
            for j in range(i, min(i + 20, len(lines))):
                if not lines[j].strip():
                    return lines[j + 1:]
            return lines[i + 6:]
    return lines


def _clean(lines: list, max_chars: int = 200_000) -> str:
    out, prev_blank = [], False
    for line in lines:
        s = line.rstrip()
        if not s.strip():
            if prev_blank:
                continue
            prev_blank = True
            out.append("")
        else:
            prev_blank = False
            out.append(s)
    text = "\n".join(out).strip("\n")
    return text[:max_chars]


def _extract_tables(lines: list, limit: int = 4) -> list:
    """按『命令回显行』分块，在输出块内用 ---- 边框界定统计表格。

    每个表格块: 表头（含 Coef./Std. err./Variable | 等强特征）所在行
    向上取上一个边框为起点，向下取第二个边框（表内分隔线 + 表尾线）
    为终点，从而覆盖表头 + 全部系数行。
    """
    seen: set[str] = set()
    out: list[str] = []
    chunk: list[str] = []

    def flush():
        nonlocal chunk
        if len(chunk) < 3:
            chunk = []
            return
        i, n = 0, len(chunk)
        while i < n and len(out) < limit:
            line = chunk[i].strip()
            if not any(h in line for h in _STRONG_HITS):
                i += 1
                continue
            start = i
            while start > 0 and not _BORDER_RE.match(chunk[start - 1].strip()):
                start -= 1
                if i - start > 15:
                    start = i
                    break
            # 从表头往后收集边框: 内部隔线 + 结尾线, 取第二个
            borders = []
            j = i + 1
            while j < min(n, i + 40) and len(borders) < 2:
                if _BORDER_RE.match(chunk[j].strip()):
                    borders.append(j)
                j += 1
            if len(borders) >= 2:
                end = borders[1]
            elif borders:
                end = borders[0]
            else:
                end = min(i + 25, n - 1)
            block = chunk[start:end + 1]
            txt = "\n".join(x.rstrip() for x in block).strip()
            if (txt and txt not in seen and len(block) >= 3
                    and any(re.search(r"\d", x) for x in block)):
                seen.add(txt)
                out.append(txt)
            i = j if borders else end + 1
        chunk = []

    for line in lines:
        if _ECHO_RE.match(line):
            flush()          # 命令回显是输出块的边界
        else:
            chunk.append(line)
    flush()
    return out[:limit]


def parse_log(raw: bytes | str, keep_tail: int = 60) -> dict:
    if isinstance(raw, bytes):
        text_raw = _decode(raw)
    else:
        text_raw = raw
    lines_all = text_raw.splitlines()
    lines = _strip_banner(lines_all)

    rcs, blocks = [], []
    for m in _RC_RE.finditer(text_raw):
        code = int(m.group(1))
        if code in rcs:
            continue
        rcs.append(code)
        pos = text_raw.count("\n", 0, m.start())
        ctx = lines_all[max(0, pos - 4): pos + 3]
        blocks.append({
            "rc": code,
            "hint": RC_HINTS.get(code, "见 Stata 文档 (help errortrap)"),
            "context": "\n".join(c.rstrip() for c in ctx if c.strip()),
        })

    text = _clean(lines)
    tail = "\n".join(lines[-keep_tail:])
    tables = _extract_tables(lines)

    return {
        "ok": len(rcs) == 0,
        "rcs": rcs,
        "first_error": blocks[0] if blocks else None,
        "error_blocks": blocks,
        "tables": tables,
        "text": text,
        "tail": tail,
    }
