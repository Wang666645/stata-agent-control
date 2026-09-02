"""学术任务模板库。

每个模板是「自包含 do 片段」骨架，占位符以 @NAME@ 形式出现。
CLI:  python cli.py templates            # 列出全部模板
      python cli.py templates --show ols # 查看模板脚本
      python cli.py templates --render ols @Y@=price @X@="mpg weight" ...

模板使用约定:
  - 数据通过 --data 传入时被暂存为工作目录中 stata_input.<ext>,
    模板中 @IMPORT@ 会被替换为对应的 import 语句（也可自行书写 import 行）;
  - 没有数据文件的模板调用会自动注入 sysuse auto 示例数据行(@IMPORT@ 变体);
  - 产物（图表/表格）请写相对文件名, 会进入当前工作目录并被自动拷贝到
    项目 stata_outputs/<runid>_<title>/。

所有 Stata 代码保持 UTF-8（含中文注释/标签均可），切勿以 BOM 保存。
"""
from __future__ import annotations

import re

TEMPLATES: dict[str, dict] = {
    "describe": {
        "about": "数据体检: 变量清单/类型/缺失/重复/极值分布",
        "tokens": [],
        "script": """\
@IMPORT@
describe
summarize, detail
misstable summarize
duplicates report
* 查看变量清单后, 可进一步用 tabulate/table 检查类别变量
""",
    },
    "ols": {
        "about": "OLS 回归 + 稳健标准误 + VIF + 存储估计结果",
        "tokens": ["@Y@", "@X@"],
        "script": """\
@IMPORT@
regress @Y@ @X@, robust
estat vif
estimates store m_ols
* 系数表已在上方输出; 若需导出到 Word/Excel, 见模板 report
""",
    },
    "panel_fe": {
        "about": "面板数据固定效应 (xtreg, fe), 含聚类稳健标准误",
        "tokens": ["@Y@", "@X@", "@ID@", "@TIME@", "@CLUSTER@"],
        "script": """\
@IMPORT@
xtset @ID@ @TIME@
xtreg @Y@ @X@, fe vce(cluster @CLUSTER@)
estimates store m_fe
* 建议接着做: xtreg @Y@ @X@, re 后 hausman m_fe ., 判断 FE/RE
""",
    },
    "did": {
        "about": "双重差分 (面板双向固定效应): 需先构造 treat/post/did 变量",
        "tokens": ["@Y@", "@X@", "@ID@", "@TIME@", "@TREAT@", "@POST@", "@DID@"],
        "script": """\
@IMPORT@
* 假设数据中已有: @TREAT@(组别,0/1) @POST@(期别,0/1) @DID@=@TREAT@*@POST@
* 若没有, 先补造: gen @DID@ = @TREAT@ * @POST@
xtset @ID@ @TIME@
xtreg @Y@ @DID@ @X@ @TREAT@#i.@TIME@, fe vce(cluster @ID@)
estimates store m_did
* 平行趋势检验建议: 用 event-study 模板或手动生成期别虚拟变量交互
""",
    },
    "iv": {
        "about": "工具变量 2SLS: y ~ 内生变量=工具变量 (exog 可空)",
        "tokens": ["@Y@", "@EXOG@", "@ENDOG@", "@IV@"],
        "script": """\
@IMPORT@
ivregress 2sls @Y@ @EXOG@ (@ENDOG@ = @IV@), robust
estat firststage
estat endogenous
* firststage 检查弱工具 (F>10); endogenous 检验内生性
""",
    },
    "logit": {
        "about": "Logit/Probit 二值选择 + 边际效应",
        "tokens": ["@Y@", "@X@", "@TYPE@"],
        "script": """\
@IMPORT@
@TYPE@ @Y@ @X@
estimates store m_logit
margins, dydx(*) post
* @TYPE@ 填 logit 或 probit; 上表为比值比/概率比, 边际效应在下方
""",
    },
    "event_study": {
        "about": "事件研究 (动态 DID): 生成事件期虚拟变量后回归并画系数图",
        "tokens": ["@Y@", "@X@", "@ID@", "@TIME@", "@TREAT@", "@EVENT@", "@LEADLAG@"],
        "script": """\
@IMPORT@
xtset @ID@ @TIME@
* 事件期变量: 假定已有 @EVENT@(处理组*相对期数), 相对期数范围 @LEADLAG@
* 示例: 生成各期虚拟变量的方式参考 did 模板; 此处直接做双向固定效应
xtreg @Y@ @X@ i.@EVENT@_grp i.@TIME@, fe vce(cluster @ID@)
* 系数图建议用 coefplot (ssc install coefplot, replace)
""",
    },
    "graph_scatter": {
        "about": "散点+拟合线 并导出 PNG（产物自动拷贝到 stata_outputs）",
        "tokens": ["@Y@", "@X@"],
        "script": """\
@IMPORT@
graph twoway (scatter @Y@ @X@) (lfit @Y@ @X@), title("Scatter @Y@ vs @X@")
graph export "scatter_@Y@_@X@.png", width(900) replace
""",
    },
    "report": {
        "about": "esttab 导出回归结果表 (rtf/xlsx/html)。需先 estimates store",
        "tokens": ["@STORED@", "@FORMAT@"],
        "script": """\
@IMPORT@
capture which esttab
if _rc {
    display "esttab 未安装, 尝试 ssc install estout ..."
    ssc install estout, replace
}
esttab @STORED@ using "report.@FORMAT@", replace \\
    b(3) se(3) star(* 0.10 ** 0.05 *** 0.01) \\
    stats(N r2, labels("N" "R-sq") fmt(0 3)) title("Regression Results")
* @STORED@ 填 m1 m2 ...; @FORMAT@ 填 rtf / html / csv
""",
    },
    "winsor": {
        "about": "连续变量 1%/99% 缩尾后回归 (winsor2, 需 ssc 安装)",
        "tokens": ["@Y@", "@X@", "@WINSOR_VARS@"],
        "script": """\
@IMPORT@
capture which winsor2
if _rc {
    display "winsor2 未安装, 尝试 ssc install ..."
    ssc install winsor2, replace
}
winsor2 @WINSOR_VARS@, suffix(_w) cuts(1 99)
regress @Y@ @X@, robust
""",
    },
}

_IMPORT_STUB = '* [未提供 --data]: 请用真实数据路径替换本行, 例如:\n' \
               '*   use "C:/path/to/data.dta", clear\n' \
               '*   import excel using "C:/path/to/data.xlsx", firstrow clear\n' \
               '*   import delimited "C:/path/to/data.csv", varnames(1) clear\n' \
               'sysuse auto, clear\n* 演示数据(auto)已载入; 系数解释仅为示例'


def render(name: str, kwargs: dict, import_line: str | None = None) -> str:
    if name not in TEMPLATES:
        raise KeyError(f"未知模板 {name!r}; 可选: {list(TEMPLATES)}")
    script = TEMPLATES[name]["script"]
    if import_line is not None:
        script = script.replace("@IMPORT@", import_line)
    else:
        script = script.replace("@IMPORT@", _IMPORT_STUB)
    leftover = set(re.findall(r"@[A-Z_]+@", script))
    for token, value in kwargs.items():
        t = f"@{token.upper()}@"
        if t in script:
            script = script.replace(t, str(value))
            leftover.discard(t)
    if leftover:
        raise ValueError(
            f"模板 {name} 缺少参数: {sorted(leftover)}；已提供: {list(kwargs)}")
    return script


def list_templates() -> list[dict]:
    return [{"name": k, "about": v["about"], "tokens": v["tokens"]}
            for k, v in TEMPLATES.items()]
