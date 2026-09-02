# -*- coding: utf-8 -*-
"""STATA Bridge MCP Server（stdio, JSON-RPC 2.0, 零第三方依赖）。

给支持 MCP 的客户端（Antigravity / Claude Code / IDE 等）提供工具:
  stata_run / stata_load / stata_which / stata_env / stata_locate /
  stata_templates / stata_outputs / stata_clean

启动: python -X utf8 mcp_server/stata_mcp.py
协议: 每行一条 JSON-RPC 消息（LSP 风格 stdio）。
实现要点: handler 函数可单测（不依赖真实管道）；tools/call 复用 stata_bridge 逻辑。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "stata_bridge"))

from stata_bridge import templates_lib  # noqa: E402
from stata_bridge.locator import find_stata  # noqa: E402
from stata_bridge.runner import OUTPUTS_ROOT, StataRunner  # noqa: E402
from stata_bridge.templates_lib import render  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stdin.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

TOOLS = [
    {
        "name": "stata_locate",
        "description": "探测本机 Stata 安装（exe 路径/版本类型）",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "stata_env",
        "description": "启动一次 Stata 读取环境信息（version/edition/os）",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "stata_run",
        "description": "执行一段自足的 Stata do 脚本（每次全新会话）。"
                       "script 首行需含数据装载语句; 有数据文件时传 data。"
                       "返回 JSON: ok/rcs/first_error/tables/text/tail/artifacts",
        "inputSchema": {
            "type": "object",
            "properties": {
                "script": {"type": "string", "description": "do 脚本内容（UTF-8）"},
                "cmd": {"type": "string", "description": "单条命令（与 script 二选一）"},
                "data": {"type": "string", "description": "数据文件绝对路径 dta/csv/xlsx"},
                "timeout": {"type": "integer", "default": 300},
                "title": {"type": "string", "default": "mcp"},
            },
            "required": ["script"],
        },
    },
    {
        "name": "stata_load",
        "description": "载入数据文件并输出 describe/summarize 变量概览",
        "inputSchema": {
            "type": "object",
            "properties": {
                "file": {"type": "string"},
                "encoding": {"type": "string",
                             "description": "csv 编码, 默认 utf-8; GBK 中文用 gb18030"},
                "timeout": {"type": "integer", "default": 300},
            },
            "required": ["file"],
        },
    },
    {
        "name": "stata_which",
        "description": "检查外部 Stata 命令是否安装（逗号分隔, 如 reghdfe,esttab）",
        "inputSchema": {
            "type": "object",
            "properties": {"names": {"type": "string"}},
            "required": ["names"],
        },
    },
    {
        "name": "stata_templates",
        "description": "学术模板库: 空参=列表; {show}=查看骨架; "
                       "{render, render_name, params:[@KEY@=value,...]}=渲染",
        "inputSchema": {
            "type": "object",
            "properties": {
                "show": {"type": "string"},
                "render": {"type": "string", "description": "模板名"},
                "params": {"type": "array", "items": {"type": "string"}},
            },
        },
    },
    {
        "name": "stata_outputs",
        "description": "列出 stata_outputs/ 下的产物文件",
        "inputSchema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "default": 10}},
        },
    },
    {
        "name": "stata_clean",
        "description": "清理 %TEMP%\\stata_* 与旧产物",
        "inputSchema": {
            "type": "object",
            "properties": {"keep": {"type": "integer", "default": 20}},
        },
    },
]


def _content(payload: dict) -> list:
    return [{"type": "text",
             "text": json.dumps(payload, ensure_ascii=False, default=str)}]


def handle_tool(name: str, args: dict) -> dict:
    runner = StataRunner()
    try:
        if name == "stata_locate":
            found = find_stata()
            return {"ok": found is not None, "stata": found,
                    "error": None if found else "未找到 Stata"}
        if name == "stata_env":
            return runner.env_info(timeout=int(args.get("timeout", 180)))
        if name == "stata_run":
            script = args.get("script") or args.get("cmd")
            if not script:
                return {"ok": False, "error": "缺少 script"}
            return runner.run_script(
                script, timeout=int(args.get("timeout", 300)),
                title=str(args.get("title", "mcp")), data_file=args.get("data"))
        if name == "stata_load":
            return runner.load_file(
                args["file"], encoding=args.get("encoding"),
                timeout=int(args.get("timeout", 300)))
        if name == "stata_which":
            return runner.which_command(
                [s.strip() for s in str(args.get("names", "")).split(",") if s.strip()],
                timeout=int(args.get("timeout", 180)))
        if name == "stata_templates":
            if args.get("show"):
                tpl = templates_lib.TEMPLATES.get(args["show"])
                if not tpl:
                    return {"ok": False, "error": f"未知模板 {args['show']}"}
                return {"ok": True, "name": args["show"], "about": tpl["about"],
                        "tokens": tpl["tokens"], "script": tpl["script"]}
            if args.get("render"):
                kwargs = {}
                for kv in args.get("params") or []:
                    k, _, v = kv.partition("=")
                    kwargs[k.strip("@").upper()] = v
                script = render(args["render"], kwargs, import_line=None)
                return {"ok": True, "script": script}
            return {"ok": True, "templates": templates_lib.list_templates()}
        if name == "stata_outputs":
            if not OUTPUTS_ROOT.is_dir():
                return {"ok": True, "runs": []}
            runs = []
            for child in sorted(OUTPUTS_ROOT.iterdir(),
                                key=lambda p: p.stat().st_mtime, reverse=True)[
                    :int(args.get("limit", 10))]:
                runs.append({"run": child.name,
                             "files": [f.name for f in sorted(child.iterdir())
                                       if f.is_file()]})
            return {"ok": True, "runs": runs}
        if name == "stata_clean":
            import shutil
            from stata_bridge.runner import _tmp_root
            removed = 0
            root = _tmp_root()
            for child in root.glob("stata_*"):
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                    removed += 1
            return {"ok": True, "removed_workdirs": removed}
        return {"ok": False, "error": f"未知工具 {name}"}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "kind": type(exc).__name__}


class McpServer:
    """无依赖的 JSON-RPC 2.0 (stdio, 每行一条消息)。"""

    def __init__(self):
        self.request_id = 0

    def handle(self, raw: str) -> str | None:
        """处理一行输入, 返回要写出的响应行（可能多条用 \\n 合并），无响应返回 None。"""
        raw = raw.strip()
        if not raw:
            return None
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return json.dumps({"jsonrpc": "2.0", "id": None,
                               "error": {"code": -32700, "message": "Parse error"}},
                              ensure_ascii=False)
        method = msg.get("method")
        msg_id = msg.get("id")
        params = msg.get("params") or {}

        if method == "initialize":
            return json.dumps({
                "jsonrpc": "2.0", "id": msg_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "stata-bridge-mcp", "version": "0.1.0"},
                }}, ensure_ascii=False)
        if method in ("notifications/initialized",):
            return None
        if method == "ping":
            return json.dumps({"jsonrpc": "2.0", "id": msg_id, "result": {}},
                              ensure_ascii=False)
        if method == "tools/list":
            return json.dumps({"jsonrpc": "2.0", "id": msg_id,
                               "result": {"tools": TOOLS}}, ensure_ascii=False)
        if method == "tools/call":
            name = params.get("name", "")
            args = params.get("arguments") or {}
            payload = handle_tool(name, args)
            return json.dumps({"jsonrpc": "2.0", "id": msg_id,
                               "result": {"content": _content(payload),
                                          "isError": not payload.get("ok", True)}},
                              ensure_ascii=False)
        return json.dumps({"jsonrpc": "2.0", "id": msg_id,
                           "error": {"code": -32601, "message": f"未知方法 {method}"}},
                          ensure_ascii=False)

    def serve(self) -> int:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            resp = self.handle(line)
            if resp:
                sys.stdout.write(resp + "\n")
                sys.stdout.flush()
        return 0


if __name__ == "__main__":
    sys.exit(McpServer().serve())
