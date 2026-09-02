# STATA Agent Control — MCP Server

Model Context Protocol (MCP) server that lets AI agents / IDEs
(Claude Code, Antigravity, Gemini CLI, …) **drive a local Stata** instance for
data analysis and econometrics: load data, run regressions, export tables and
charts, and get structured results back — all from natural-language or
tool-call instructions.

## What you get

- One **stdio MCP server**, zero third-party Python dependencies (stdlib only).
- 8 tools:

| Tool | Purpose |
|---|---|
| `stata_locate` | detect the local Stata installation |
| `stata_env` | Stata version / edition / OS info |
| `stata_run` | execute a self-contained Stata do script (core) |
| `stata_load` | import a data file (dta/csv/xlsx) and summarize variables |
| `stata_which` | check whether external commands (reghdfe, esttab, …) exist |
| `stata_templates` | curated academic template snippets (OLS/panel FE/DID/IV/logit…) |
| `stata_outputs` | list exported artifacts (PNG charts, RTF/CSV tables) |
| `stata_clean` | clean temporary work dirs and old artifacts |

## Requirements

- **Windows** with a 64-bit **Stata** (MP/SE/BE) installed in the default
  location (`C:\Program Files\StataNN\`) — auto-detected via the registry;
  environment override: `STATA_HOME`.
- **Python 3.10+** (developed and tested on 3.14). No `pip install` needed.
- Outbound network only if you let Stata fetch user-written commands
  (`ssc install reghdfe`, …).

## How it works

Each tool call runs one **headless batch** session:
`StataMP-64.exe /e do <temp.do>` (new session per call, ~1–3 s startup).
- do-scripts are written as UTF-8 (no BOM) — Chinese comments/paths are fine.
- Output (log) is parsed into structured JSON: return codes with human hints,
  extracted coefficient tables, error context.
- Files produced by the script (charts, esttab reports) are copied into
  `<repo>/stata_outputs/<runid>_<title>/` (or `~/.stata-skill/stata_outputs/`).

## Quick start

```bash
python -X utf8 mcp_server/stata_mcp.py
```

Or set `PYTHONUTF8=1` in the environment instead of `-X utf8`.

Smoke test (newline-delimited JSON-RPC over stdio):

```bash
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"t","version":"1"}}}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
  '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"stata_locate","arguments":{}}}' \
| python -X utf8 mcp_server/stata_mcp.py
```

## Client configuration

Claude Code (user scope):

```bash
claude mcp add -s user stata -e PYTHONUTF8=1 -- python /absolute/path/to/mcp_server/stata_mcp.py
```

> Note: `-e` is variadic — place it after the name and before `--`; do not pass
> `-X` to python through the launcher (it gets eaten by the CLI parser), use the
> `PYTHONUTF8=1` environment variable instead.

Antigravity (`~/.gemini/config/mcp_config.json` or `<workspace>/.agents/mcp_config.json`):

```json
{
  "mcpServers": {
    "stata": {
      "command": "python",
      "args": ["/absolute/path/to/mcp_server/stata_mcp.py"],
      "env": { "PYTHONUTF8": "1" }
    }
  }
}
```

Any other MCP client: standard stdio transport, same command.

## Tool result shape

Every tool returns a JSON object with at least `ok`; estimation runs add
`rcs` (Stata error codes), `first_error` (code + hint + context), `tables`
(extracted coefficient tables), `text`/`tail` (full log), and `artifacts`.

## Tips & pitfalls (learned the hard way)

- Stata do-files must be **UTF-8 without BOM** (BOM ⇒ `r(199)`; GBK ⇒ mojibake).
  This server handles it; don't hand-write BOM files.
- Never trust the batch process exit code alone — parse the log for `r(###)`.
- Scripts are self-contained per call (no dataset persists between calls);
  pass the data file each time (`use "..."` inside your script or see
  `stata_load`).
- External commands like `reghdfe`/`esttab` may need `ssc install` once.

## License

MIT — see [LICENSE](LICENSE).
