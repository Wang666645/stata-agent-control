"""Stata 安装探测。

Windows 优先（注册表 Uninstall 项 + 常见安装目录），macOS/Linux 兜底常见路径。
探测结果可被 STATA_HOME / STATAPATH 环境变量覆盖。
"""
from __future__ import annotations

import os
import platform
import sys

# 各平台可能的可执行文件名（按优先级排列）
_EXE_CANDIDATES = {
    "win32": [
        "StataMP-64.exe", "StataSE-64.exe", "StataBE-64.exe",
        "StataMP.exe", "StataSE.exe", "StataBE.exe",
    ],
    "darwin": ["StataMP", "StataSE", "StataBE"],          # Stata 18 终端版
    "linux": ["stata-mp", "stata-se", "stata-be", "stata"],
}

_EDITION_MAP = {  # exe 名片段 -> edition 标识
    "mp": "MP", "se": "SE", "be": "BE",
}


class StataNotFoundError(RuntimeError):
    """找不到 Stata 安装。"""


def _exe_names_for(platform_name: str):
    return _EXE_CANDIDATES.get(platform_name, _EXE_CANDIDATES["linux"])


def _edition_of(exe_name: str) -> str:
    low = exe_name.lower()
    for key, ed in _EDITION_MAP.items():
        if key in low:
            return ed
    return "MP"


def _home_candidates() -> list:
    """按优先级返回候选安装目录列表。"""
    homes = []
    for env in ("STATA_HOME", "STATAPATH"):
        if os.environ.get(env):
            homes.append(os.environ[env])
    homes.extend(_homes_from_registry())
    homes.extend(_homes_from_program_files())
    return _dedup([h for h in homes if h])


def _dedup(items):
    seen, out = set(), []
    for it in items:
        key = os.path.normpath(it).lower()
        if key not in seen:
            seen.add(key)
            out.append(it)
    return out


def _homes_from_registry() -> list:
    if sys.platform != "win32":
        return []
    homes = []
    try:
        import winreg
    except Exception:
        return homes
    roots = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]
    for root, sub in roots:
        try:
            with winreg.OpenKey(root, sub) as key:
                count = winreg.QueryInfoKey(key)[0]
                for i in range(count):
                    try:
                        name = winreg.EnumKey(key, i)
                        with winreg.OpenKey(key, name) as k2:
                            display = ""
                            loc = ""
                            for j in range(winreg.QueryInfoKey(k2)[0] + 1):
                                try:
                                    vname, vdata, _ = winreg.EnumValue(k2, j)
                                except OSError:
                                    break
                                if vname == "DisplayName":
                                    display = str(vdata)
                                elif vname == "InstallLocation":
                                    loc = str(vdata)
                            if display.lower().startswith("stata"):
                                if loc and os.path.isdir(loc):
                                    homes.append(loc)
                                else:
                                    # 常见布局: C:\Program Files\<DisplayName>\
                                    guess = os.path.join(
                                        os.environ.get("ProgramFiles", r"C:\Program Files"), display)
                                    if os.path.isdir(guess):
                                        homes.append(guess)
                    except OSError:
                        continue
        except OSError:
            continue
    return homes


def _homes_from_program_files() -> list:
    if sys.platform != "win32":
        return []
    homes = []
    for pf in (os.environ.get("ProgramFiles", r"C:\Program Files"),
               os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")):
        try:
            for entry in sorted(os.listdir(pf), reverse=True):
                if entry.lower().startswith("stata"):
                    full = os.path.join(pf, entry)
                    if os.path.isdir(full):
                        homes.append(full)
        except OSError:
            continue
    return homes


def _home_candidates_unix() -> list:
    if sys.platform == "win32":
        return []
    out = []
    for base in ("/usr/local/stata", "/usr/local/stata18", "/usr/local/stata17",
                 "/opt/stata", "/Applications/Stata"):
        if os.path.isdir(base):
            out.append(base)
    # PATH 直接找命令所在目录
    for exe in _exe_names_for(sys.platform):
        for path in os.environ.get("PATH", "").split(os.pathsep):
            if not path:
                continue
            full = os.path.join(path, exe)
            if os.path.isfile(full):
                # Stata 18 终端版: 可执行文件在安装根目录
                if os.path.basename(os.path.dirname(full)) == "bin":
                    out.append(os.path.dirname(os.path.dirname(full)))
                else:
                    out.append(os.path.dirname(full))
    return _dedup(out)


def find_stata() -> dict | None:
    """返回 {'home', 'exe', 'edition'}；找不到返回 None。"""
    candidates = _home_candidates()
    if sys.platform != "win32":
        candidates = _dedup(candidates + _home_candidates_unix())
    for home in candidates:
        if not os.path.isdir(home):
            continue
        for exe_name in _exe_names_for(sys.platform):
            exe = os.path.join(home, exe_name)
            if os.path.isfile(exe):
                return {"home": home, "exe": exe, "edition": _edition_of(exe_name)}
    # 最后兜底: 从 PATH 找
    for exe_name in _exe_names_for(sys.platform):
        for path in os.environ.get("PATH", "").split(os.pathsep):
            if not path:
                continue
            exe = os.path.join(path, exe_name)
            if os.path.isfile(exe):
                home = os.path.dirname(exe) if sys.platform != "win32" else os.path.dirname(exe)
                return {"home": home, "exe": exe, "edition": _edition_of(exe_name)}
    return None


def require_stata() -> dict:
    found = find_stata()
    if found is None:
        raise StataNotFoundError(
            "未找到 Stata。可设置环境变量 STATA_HOME 指向安装目录，"
            "或安装 Stata 后重试。")
    return found
