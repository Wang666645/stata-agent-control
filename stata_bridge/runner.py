"""Stata 批处理执行器（核心）。

工作模型:
  1. 每次调用创建唯一 ASCII 临时工作目录（%TEMP%\\stata_<runid>\\）；
  2. 组装 do 文件（UTF-8 无 BOM，可含中文）: 前导 set more off 等 + cd 到工作目录 + 用户脚本；
  3. subprocess 启动 StataMP-64.exe /e do，带超时与进程清理；
  4. 读取自动日志（UTF-8），解析为结构化结果；
  5. 工作目录内的产物文件自动拷贝到项目 stata_outputs/<runid>_<title>/。

编码约定（本机实验结论, Stata 17 MP / zh-CN）:
  - .do 文件必须为 UTF-8（无 BOM）: BOM 会让首行命令报 r(199), GBK 会乱码/失败；
  - Stata 自动日志为 UTF-8；
  - 中文内容（注释、display、路径）均可用。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

from . import log_parser
from .locator import require_stata

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUTS_ROOT = PROJECT_ROOT / "stata_outputs"

_SUPPORTED_DATA_EXT = {".dta", ".csv", ".xlsx", ".xls"}


class BridgeError(RuntimeError):
    """桥接层错误（非 Stata 执行错误）。"""


def _tmp_root() -> Path:
    override = os.environ.get("STATABRIDGE_TMP")
    base = Path(override) if override else Path(tempfile.gettempdir())
    try:
        base.mkdir(parents=True, exist_ok=True)
        probe = base / ".stata_bridge_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return base
    except OSError as exc:  # pragma: no cover - 仅当 TEMP 不可写时
        raise BridgeError(f"临时目录不可写: {base} ({exc})；可设置环境变量 STATABRIDGE_TMP") from exc


def _new_workdir() -> Path:
    root = _tmp_root()
    work = root / f"stata_{uuid.uuid4().hex[:12]}"
    work.mkdir(parents=True, exist_ok=False)
    return work


def _build_do(script: str) -> str:
    """前导 + 用户脚本。do 文件将由调用方以 UTF-8 无 BOM 写出。"""
    if "\x00" in script:
        raise BridgeError("脚本包含 NUL 字节，无法执行")
    if script.startswith("\ufeff"):
        script = script.lstrip("\ufeff")
    return (
        "set more off\n"
        "set linesize 200\n"
        "capture log close\n"
        + script
        + ("\n" if not script.endswith("\n") else "")
    )


class StataRunner:
    """一次运行一个任务的批处理执行器。"""

    def __init__(self):
        self._stata = None
        self._lock_path = None

    @property
    def stata(self) -> dict:
        if self._stata is None:
            self._stata = require_stata()
        return self._stata

    # ------------------------------------------------------------------
    def run_script(self, script: str, *, timeout: int = 300,
                   title: str = "run", data_file: str | os.PathLike | None = None,
                   keep_workdir: bool = False) -> dict:
        """执行一段 do 脚本。

        data_file: 若提供, 复制为工作目录中的 stata_input.<ext>,
                   脚本中可用对应的 import 语句装载（见模板与 SKILL 文档）。
        """
        stata = self.stata
        workdir = _new_workdir()
        run_id = workdir.name
        started = time.time()
        do_path = workdir / "task.do"
        log_path = workdir / "task.log"
        stdout_path = workdir / "task.stdout.txt"

        warnings: list[str] = []
        if "log using" in script.lower():
            warnings.append("脚本包含 log using：请删除，runner 会自动读取 Stata 批处理日志。")

        data_staged: str | None = None
        if data_file is not None:
            src = Path(data_file)
            if not src.is_file():
                raise BridgeError(f"数据文件不存在: {src}")
            ext = src.suffix.lower()
            if ext not in _SUPPORTED_DATA_EXT:
                raise BridgeError(
                    f"不支持的文件类型 {ext!r}；支持: {sorted(_SUPPORTED_DATA_EXT)}")
            data_staged = f"stata_input{ext}"
            shutil.copy2(src, workdir / data_staged)

        body = _build_do(script)
        body = body.replace("{WORKDIR}", workdir.as_posix())  # 允许脚本用 {WORKDIR}
        do_path.write_text(body, encoding="utf-8", newline="\n")

        result = {
            "ok": True,
            "run_id": run_id,
            "stata": {"home": stata["home"], "exe": stata["exe"],
                      "edition": stata["edition"]},
            "workdir": str(workdir),
            "warnings": warnings,
            "data_staged": data_staged,
            "duration_s": None,
            "exit_code": None,
            "timeout": False,
            "rcs": [],
            "first_error": None,
            "error_blocks": [],
            "text": "",
            "tail": "",
            "tables": [],
            "artifacts": [],
        }

        proc = None
        try:
            with stdout_path.open("wb") as out:
                flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                proc = subprocess.Popen(
                    [stata["exe"], "/e", "do", str(do_path)],
                    cwd=str(workdir), stdout=out, stderr=subprocess.STDOUT,
                    creationflags=flags)
                try:
                    proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    result["timeout"] = True
                    result["ok"] = False
                    try:
                        proc.kill()
                    except OSError:
                        pass
                    # 尝试连带清理子进程树
                    try:
                        subprocess.run(
                            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                            capture_output=True, timeout=15)
                    except Exception:
                        pass
        except OSError as exc:
            raise BridgeError(f"无法启动 Stata: {exc}") from exc
        finally:
            result["duration_s"] = round(time.time() - started, 2)

        result["exit_code"] = proc.returncode if proc else None

        # 读自动日志（等待其落盘）
        raw = b""
        if log_path.exists():
            raw = log_path.read_bytes()
        if not raw and stdout_path.exists():
            raw = stdout_path.read_bytes()
        if raw:
            parsed = log_parser.parse_log(raw)
            result.update({k: parsed[k] for k in
                           ("ok", "rcs", "first_error", "error_blocks",
                            "text", "tail", "tables")})
            result["ok"] = parsed["ok"] and not result["timeout"]
        elif result["timeout"]:
            result["ok"] = False
            result["text"] = "(Stata 运行超时，已强制结束；日志未生成)"
        else:
            result["text"] = "(未产生日志；stdout 为空)"

        # 拷贝产物到项目 outputs
        try:
            result["artifacts"] = self._collect_artifacts(workdir, run_id, title)
        except Exception as exc:  # 拷贝失败不致命
            result["warnings"].append(f"产物拷贝失败: {exc}")

        if not keep_workdir:
            # 保留日志/输入以便复查，删除其它中间文件
            pass  # 默认保留工作目录（可复查），由 clean 命令清理
        return result

    def _collect_artifacts(self, workdir: Path, run_id: str, title: str) -> list:
        out_dir = OUTPUTS_ROOT / f"{run_id}_{_slug(title)}"
        out_dir.mkdir(parents=True, exist_ok=True)
        artifacts = []
        for child in workdir.iterdir():
            if not child.is_file():
                continue
            if child.name in ("task.do", "task.log", "task.stdout.txt") or \
               child.name.startswith("stata_input"):
                continue
            target = out_dir / child.name
            shutil.copy2(child, target)
            artifacts.append({"name": child.name, "path": str(target)})
        return artifacts

    # ------------------------------------------------------------------
    # 便捷命令
    # ------------------------------------------------------------------
    def run_command(self, cmd: str, *, timeout: int = 300,
                    data_file: str | os.PathLike | None = None) -> dict:
        return self.run_script(cmd, timeout=timeout, title="cmd",
                               data_file=data_file)

    def which_command(self, names: list[str], *, timeout: int = 180) -> dict:
        script_lines = []
        for name in names:
            script_lines.append(
                f'capture which {name}\n'
                f'if _rc == 0 {{\n    which {name}\n'
                f'    display "WHICH_RESULT|{name}|INSTALLED"\n'
                f'}}\nelse {{\n'
                f'    display "WHICH_RESULT|{name}|MISSING"\n'
                f'}}\n')
        res = self.run_script("\n".join(script_lines), timeout=timeout,
                              title="which")
        found = {}
        for line in res.get("text", "").splitlines():
            line = line.strip()
            if line.startswith("WHICH_RESULT|"):
                _, name, state = line.split("|")
                found[name] = {"installed": state == "INSTALLED"}
        res["which"] = found
        return res

    def env_info(self, *, timeout: int = 180) -> dict:
        script = (
            'display "ENV_VERSION=" c(version)\n'
            'display "ENV_EDITION=" c(edition)\n'
            'display "ENV_BITS=" c(bit)\n'
            'display "ENV_OS=" c(os)\n'
            'display "ENV_DATE=" c(current_date)\n'
            'display "ENV_TIME=" c(current_time)\n'
        )
        res = self.run_script(script, timeout=timeout, title="env")
        env = {}
        for line in res.get("text", "").splitlines():
            line = line.strip()
            if line.startswith("ENV_"):
                k, _, v = line.partition("=")
                env[k[4:].lower()] = v.strip()
        res["env"] = env
        return res

    # ------------------------------------------------------------------
    def load_file(self, file: str | os.PathLike, *,
                  encoding: str | None = None,
                  summarize: bool = True, timeout: int = 300) -> dict:
        """把外部数据文件载入并给出变量概览（describe + summarize）。"""
        src = Path(file)
        ext = src.suffix.lower()
        if ext not in _SUPPORTED_DATA_EXT:
            raise BridgeError(f"不支持的文件类型 {ext!r}；支持: {sorted(_SUPPORTED_DATA_EXT)}")
        import_line = import_line_for(ext, encoding=encoding)
        script = [import_line, 'describe']
        if summarize:
            script.append('summarize')
            script.append('* 若上述 summarize 无输出, 说明变量均为字符串; 可用 encode/destring 转换')
        return self.run_script("\n".join(script), timeout=timeout,
                               title="load", data_file=src)


def import_line_for(ext: str, encoding: str | None = None) -> str:
    """返回把工作目录中 stata_input<ext> 载入的 Stata 语句。"""
    ext = ext.lower()
    if ext == ".dta":
        return 'use "stata_input.dta", clear'
    if ext == ".csv":
        enc = (encoding or "utf-8").lower()
        return (f'import delimited "stata_input.csv", varnames(1) '
                f'encoding("{enc}") clear')
    if ext in (".xlsx", ".xls"):
        return 'import excel using "stata_input.xlsx", firstrow clear'
    raise BridgeError(f"不支持的文件类型 {ext!r}")


def _slug(title: str) -> str:
    keep = [ch for ch in title if ch.isalnum() or ch in "-_."]
    s = "".join(keep).strip("._") or "run"
    return s[:40]
