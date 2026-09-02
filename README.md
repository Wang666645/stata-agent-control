# STATA Agent Control — MCP 服务器

**用自然语言/工具调用驱动本机 Stata 的 MCP（Model Context Protocol）服务器。**
面向 AI Agent / IDE（Claude Code、Antigravity、Gemini CLI 等）：导入数据、
跑回归、导出表格与图表，并把结构化结果返回给 Agent——全程由 Agent 翻译你的
中文指令并执行，无需手写 do 文件。

## 功能一览

- **stdio MCP 服务器**，零第三方 Python 依赖（仅标准库）。
- 内置 **8 个工具**：

| 工具 | 用途 |
|---|---|
| `stata_locate` | 自动探测本机 Stata 安装位置 |
| `stata_env` | Stata 版本 / 版本类型 / 系统信息 |
| `stata_run` | 执行一段自足的 Stata do 脚本（核心工具） |
| `stata_load` | 导入数据文件（dta/csv/xlsx）并输出变量概览 |
| `stata_which` | 检查外部命令是否安装（reghdfe、esttab 等） |
| `stata_templates` | 学术模板库（OLS / 面板FE / DID / IV / Logit 等） |
| `stata_outputs` | 列出导出的产物（PNG 图表、RTF/CSV 表格） |
| `stata_clean` | 清理临时工作目录与旧产物 |

## 运行环境要求

- **Windows** + 64 位 **Stata**（MP/SE/BE，默认安装于
  `C:\Program Files\StataNN\`，通过注册表自动探测；可用环境变量 `STATA_HOME`
  覆盖）。
- **Python 3.10+**（开发与测试于 3.14）。**无需** `pip install` 任何包。
- 仅在让 Stata 安装社区命令（`ssc install reghdfe` 等）时才需要外网。

## 工作原理

每次工具调用 = 一次**无头批处理会话**：
`StataMP-64.exe /e do <临时.do>`（每次全新会话，启动约 1–3 秒）。

- do 脚本按 **UTF-8（无 BOM）** 写入——中文注释、中文路径均可正常使用；
- 输出日志被解析为结构化 JSON：错误码（附中文提示）、自动提取的系数表、
  错误上下文；
- 脚本产生的文件（图表、esttab 报告）自动复制到
  `<仓库根>/stata_outputs/<runid>_<title>/`（或全局安装时
  `~/.stata-skill/stata_outputs/`）。

## 快速开始

```bash
python -X utf8 mcp_server/stata_mcp.py
```

（或在环境变量中设置 `PYTHONUTF8=1`，代替 `-X utf8`。）

冒烟测试（stdio 换行分隔 JSON-RPC）：

```bash
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"t","version":"1"}}}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
  '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"stata_locate","arguments":{}}}' \
| python -X utf8 mcp_server/stata_mcp.py
```

## 客户端接入示例

**Claude Code（用户级，任何项目可用）：**

```bash
claude mcp add -s user stata -e PYTHONUTF8=1 -- python /绝对路径/mcp_server/stata_mcp.py
```

> 注意：`-e` 是贪婪变参——要放在服务器名之后、`--` 之前；不要经 CLI 启动器给
> python 传 `-X`（会被解析器吞掉），改用环境变量 `PYTHONUTF8=1`。

**Antigravity**（写入 `~/.gemini/config/mcp_config.json`，或项目内
`.agents/mcp_config.json`）：

```json
{
  "mcpServers": {
    "stata": {
      "command": "python",
      "args": ["/绝对路径/mcp_server/stata_mcp.py"],
      "env": { "PYTHONUTF8": "1" }
    }
  }
}
```

其他任意 MCP 客户端：标准 stdio 传输，同样命令即可。

## 工具返回结构

每个工具返回一个 JSON 对象，至少含 `ok` 字段；执行类工具还会带：
`rcs`（Stata 错误码）、`first_error`（错误码 + 中文提示 + 上下文）、
`tables`（提取的系数表）、`text`/`tail`（完整日志）、`artifacts`（产物列表）。

## 经验与坑（实测总结）

- Stata 的 do 文件必须是 **UTF-8 无 BOM**：带 BOM 会报 `r(199)`，GBK 编码会乱码。
  本服务器已按此处理，请勿手写带 BOM 的文件。
- **不要只看批处理进程退出码**——以日志中的 `r(###)` 为准（曾出现日志报错但
  退出码为 0 的情况）。
- 每次调用都是全新会话，**数据不会跨调用保留**：脚本需自足（自己 `use` /
  `import` 数据），长任务请写成单段脚本一次执行。
- reghdfe / estout / winsor2 等社区命令可能需要先执行一次 `ssc install`。

## 许可

MIT —— 见 [LICENSE](LICENSE)。
