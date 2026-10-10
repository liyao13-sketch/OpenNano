"""现场菜单抄读表：解析 · 结果录入 · 多表比较 · 图片命名归档（工单 `20261010-数据线-to-工具线-01`）。

## 这是什么

数据线做了一个**单文件离线 HTML 填表器**（NAS `共享/25_现场记录/菜单抄读/填表器_机台菜单抄读.html`，v12），
用于现场抄机台菜单参数、录刻蚀结果与结果图片。本模块把它的**纯函数区**（`/*__PURE_START__*/…`
那一 260 行、无 DOM 依赖）搬到工具侧，让 OpenNano 具备同样能力：

| 工单条目 | 本模块对应 |
|---|---|
| ① 导入填表器导出件（16 列，识别三类非读数行） | `parse_sheet` / `row_kind` / `stats` |
| ② 结果录入（受控量名 · method 词表 · 默认未核实 · 单位自动带出 · 模板存取） | `results_to_rows` / `rows_to_results` / `tpl_from_results` / `results_from_tpl` / `guess_unit` / `load_templates` / `save_template` / `delete_template` |
| ③ 比较器（run 升序 · 参数×run · 差异标红 · 数值等价不算差异 · 空值不参与） | `build_compare` / `differs` / `norm_eq` / `run_no_of` |
| ④ 图片管理（tag · 自定义命名规则 · 改名归档 · 索引随表导出） | `img_final_name` / `finalize_names` / `apply_name_rule` / `run_tag` / `safe_name` / `export_rows` |
| ⑤ 落库对接（不另建通道，只做预览/校验） | `landing_preview`（规则与 `ingest/datasets_results.py` 一致） |

## 三条不可越的线（工单 §三）

1. **不新开落库通道**：本模块**只读表、只出预览**，绝不写 core/CSV/db。core 的写仍归数据线
   （解析 → 复核 → `build_core`）。
2. **16 列列名契约**：列序即契约，**前 6 列不得改**（既有一致/不符判定依赖它）。改动前必须跨线通知数据线。
3. **菜单/量名词不进公开仓库**：字段标签/忽略列/位域列（如 `MFC1` 对应哪种气体）与量名词表都在**私域**。
   本模块走既有「**外置清单 ＋ 加载器**」模式：读 `~/.opennano/reading_fields.json`（`OPENNANO_READING_FIELDS`
   可覆盖），**缺文件时退回中性默认**（标签＝字段名、无提示、不忽略任何列）⇒ 公开仓零实验室指纹。
   量名词表则**运行时**从契约（`form_contract`）读，读不到就回落到"包内/样例语料里出现过的量名"。

## 与参照实现的一处**口径差异**（已写进回执）

参照实现 `stats()` 只把 `Loop[12] 区间` 当元数据；**本实现把 `Loop[12] 区间` 与 `Loop[12] 次数` 一并**
算作非读数行 —— 因为两者都由**界面/配方**设定（样例表里 `次数` 的 80/260 来自菜单 `parsed_value`，
不是现场抄读值），计进"待填"会把待办数虚增。分类照旧全部暴露（`stats()["kinds"]`），不隐藏。
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
from datetime import datetime
from pathlib import Path

# ── 契约常量 ──────────────────────────────────────────────────────────────
#: 16 列列名契约（**顺序即契约**，见工单 §二-5）。前 6 列不得改。
COLUMNS = ["recipe", "machine_step", "field", "parsed_value", "machine_value", "match",
           "run", "reader", "read_at", "unit", "note", "quantity", "method", "verification",
           "core_run_id", "sample_id"]
#: 前 6 列＝冻结列（`match`/`match` 判定与既有对账逻辑依赖它们）
FROZEN_COLS = COLUMNS[:6]

RESULT_STEP = "结果"
IMG_STEP = "图片"
LOOP_STEP = "循环"
LOOP_FIELDS = ("Loop1 区间", "Loop1 次数", "Loop2 区间", "Loop2 次数")
UNVERIFIED = "未核实"
DEFAULT_NAME_RULE = "{slot}_{run}_{tag}{seq}{ext}"

#: 单位后缀 → 单位（`guess_unit`）。**通用**规则，不含实验室专属信息。
#: 长的后缀必须排在前面（`_nm_min` 优先于 `_nm`）。
UNIT_BY_SUFFIX = (("_nm_min", "nm/min"), ("_nm", "nm"), ("_pct", "%"), ("_deg", "°"),
                  ("_mpa", "MPa"), ("_pa", "Pa"), ("_c", "°C"), ("_w", "W"))


# ── 外置字段注解（公开仓零指纹）────────────────────────────────────────────
def annotations_path() -> Path:
    env = os.environ.get("OPENNANO_READING_FIELDS")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".opennano" / "reading_fields.json"


def annotations() -> dict:
    """读字段注解（标签/提示/忽略列/位域列）。缺文件 ⇒ 中性默认。"""
    neutral = {"fields": {}, "ignored": [], "bits": [], "source": "neutral"}
    p = annotations_path()
    if not p.exists():
        return neutral
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {**neutral, "error": f"注解文件读不了：{p}"}
    return {"fields": d.get("fields") or {}, "ignored": list(d.get("ignored") or []),
            "bits": list(d.get("bits") or []), "source": str(p)}


def field_label(field: str) -> str:
    f = annotations()["fields"].get(field) or {}
    return f.get("label") or field


def field_hint(field: str) -> str:
    f = annotations()["fields"].get(field) or {}
    return f.get("hint") or ""


def is_bit_field(field: str) -> bool:
    return field in (annotations()["bits"] or [])


def ignored_fields() -> list[str]:
    return list(annotations()["ignored"] or [])


def drop_ignored(rows: list[dict]) -> list[dict]:
    """按外置注解剔掉"固定不看"的列（如某些 He/ESC 相关列）。注解缺失 ⇒ 一行不剔。"""
    ign = ignored_fields()
    if not ign:
        return list(rows)
    return [r for r in rows if not any(str(r.get("field") or "").startswith(p) for p in ign)]


# ── CSV 读写（与填表器同构）───────────────────────────────────────────────
def parse_csv(text: str) -> list[list[str]]:
    text = text.lstrip("\ufeff")
    rows = list(csv.reader(io.StringIO(text, newline="")))
    return [r for r in rows if any(str(c).strip() for c in r)]


def to_csv(rows: list[list]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")      # 与填表器一致（CRLF）
    for r in rows:
        w.writerow(["" if v is None else str(v) for v in r])
    return buf.getvalue()


# ── 行分类（工单 §二-1 的核心）────────────────────────────────────────────
def is_loop_range(field: str) -> bool:
    return bool(re.fullmatch(r"Loop[12] 区间", str(field or "")))


def is_loop_cycle(field: str) -> bool:
    return bool(re.fullmatch(r"Loop[12] 次数", str(field or "")))


def is_loop_meta(field: str) -> bool:
    """参照实现只认 `区间`；本实现把 `次数` 也算界面/配方设定值（见模块 docstring 的口径差异）。"""
    return is_loop_range(field) or is_loop_cycle(field)


def is_result_row(r: dict) -> bool:
    return str((r or {}).get("machine_step") or "") == RESULT_STEP


def is_image_row(r: dict) -> bool:
    return str((r or {}).get("machine_step") or "") == IMG_STEP


def row_kind(r: dict) -> str:
    """`reading`｜`loop_range`｜`loop_cycle`｜`result`｜`image`。"""
    if is_result_row(r):
        return "result"
    if is_image_row(r):
        return "image"
    f = r.get("field")
    if is_loop_range(f):
        return "loop_range"
    if is_loop_cycle(f):
        return "loop_cycle"
    return "reading"


def cell_of(r: dict) -> str:
    """一格的值：机台抄到的优先，否则用表里带来的解析值（参照实现同此）。"""
    return str((r.get("machine_value") or "")).strip() or str((r.get("parsed_value") or "")).strip()


# ── 比较判据 ──────────────────────────────────────────────────────────────
def norm_eq(a, b):
    """等价判定：空值 ⇒ `None`（**不参与**）；能转数 ⇒ 按数值（`1.0`≡`1`）；否则忽略大小写。"""
    A, B = str(a if a is not None else "").strip(), str(b if b is not None else "").strip()
    if A == "" or B == "":
        return None
    try:
        return float(A) == float(B)
    except ValueError:
        return A.lower() == B.lower()


def differs(vals: list) -> bool:
    seen = [str(v if v is not None else "").strip() for v in (vals or [])]
    seen = [v for v in seen if v != ""]
    if len(seen) < 2:
        return False
    return any(norm_eq(seen[0], v) is False for v in seen[1:])


def preview_of(r: dict) -> str:
    """一格的状态：`pend`（没抄）｜`noval`（抄了但表里没给值）｜`ok`｜`bad`。"""
    has_m = bool(str(r.get("machine_value") or "").strip())
    has_p = bool(str(r.get("parsed_value") or "").strip())
    if not has_m:
        return "pend"
    if not has_p:
        return "noval"
    return "ok" if norm_eq(r.get("parsed_value"), r.get("machine_value")) else "bad"


# ── 统计（**三类非读数行不进待填/一致/不符**）──────────────────────────────
def stats(rows: list[dict]) -> dict:
    counts = {"reading": 0, "loop_range": 0, "loop_cycle": 0, "result": 0, "image": 0}
    live: list[dict] = []
    for r in rows or []:
        k = row_kind(r)
        counts[k] = counts.get(k, 0) + 1
        if k == "reading":
            live.append(r)
    filled = sum(1 for r in live if str(r.get("machine_value") or "").strip())
    bad = sum(1 for r in live if preview_of(r) in ("bad", "noval"))
    return {"total": len(live), "filled": filled, "pend": len(live) - filled, "bad": bad,
            "kinds": counts, "excluded_non_reading": sum(v for k, v in counts.items() if k != "reading")}


# ── 矩阵（行＝字段，列＝步）───────────────────────────────────────────────
def build_matrix(rows: list[dict]) -> dict:
    steps: list[str] = []
    fields: list[str] = []
    for r in rows or []:
        s = str(r.get("machine_step") or "")
        if s not in steps:
            steps.append(s)
        if r.get("field") not in fields:
            fields.append(r.get("field"))

    def sort_key(s: str):
        t = s.strip()
        numeric = t != "" and re.fullmatch(r"-?\d+(\.\d+)?", t) is not None
        return (1, float(t), "") if numeric else (0, 0.0, t)   # 非数字步（如「循环」）排最前

    steps.sort(key=sort_key)
    idx = {(str(r.get("machine_step") or ""), r.get("field")): r for r in rows or []}
    return {"steps": steps, "fields": fields,
            "rows": [[idx.get((s, f)) for s in steps] for f in fields]}


# ── run 号与比较器 ────────────────────────────────────────────────────────
#: run 号认不出时的哨兵（旧版 6 列导出既没有 `run` 列、文件名也没有 `runNN` ⇒ 不硬编一个数）
_RUN_NO_UNKNOWN = 2 ** 31 - 1


def run_no_of(label: str, rows: list[dict]) -> int:
    for r in rows or []:
        v = str(r.get("run") or "")
        m = re.search(r"(\d+)", v)
        if m:
            return int(m.group(1))
    m = re.search(r"run[_-]?(\d+)", str(label or ""), re.I)
    return int(m.group(1)) if m else _RUN_NO_UNKNOWN


def build_compare(tables: list[dict]) -> dict:
    """`tables` = `[{label, rows, results?}]` ⇒ 按 run 升序的「参数×run」矩阵 ＋ 结果并排。"""
    runs = sorted(({"label": t.get("label", ""), "no": run_no_of(t.get("label", ""), t.get("rows") or []),
                    "rows": t.get("rows") or [], "results": t.get("results") or [],
                    "stats": stats(t.get("rows") or [])} for t in (tables or [])),
                  key=lambda x: (x["no"], str(x["label"])))
    params: dict[str, dict] = {}
    for i, rn in enumerate(runs):
        for r in rn["rows"]:
            if row_kind(r) != "reading":
                continue
            key = f"{r.get('machine_step')}|{r.get('field')}"
            if key not in params:
                params[key] = {"step": str(r.get("machine_step") or ""), "field": r.get("field"),
                               "label": field_label(str(r.get("field") or "")),
                               "vals": [""] * len(runs)}
            params[key]["vals"][i] = cell_of(r)
    plist = []
    for p in params.values():
        p["differ"] = differs(p["vals"])
        plist.append(p)
    plist.sort(key=lambda p: (not p["differ"],
                              float(p["step"]) if re.fullmatch(r"-?\d+(\.\d+)?", p["step"]) else -1,
                              str(p["field"])))
    names: list[str] = []
    for rn in runs:
        for x in rn["results"]:
            n = str(x.get("name") or "").strip()
            if n and n not in names:
                names.append(n)
    results = [{"name": n,
                "vals": [next((str(x.get("value") or "") for x in rn["results"]
                               if str(x.get("name") or "").strip() == n), "") for rn in runs]}
               for n in names]
    for row in results:
        row["differ"] = differs(row["vals"])
    unknown = [r["label"] for r in runs if r["no"] >= _RUN_NO_UNKNOWN]
    return {"runs": [{"label": r["label"],
                      "no": None if r["no"] >= _RUN_NO_UNKNOWN else r["no"],
                      "stats": r["stats"]} for r in runs],
            "params": plist, "results": results,
            "unknown_run": unknown,
            "diff_count": sum(1 for p in plist if p["differ"])}


def compare_to_csv(cmp: dict) -> str:
    """对照表 CSV：参数×run ＋ 结果并排（差异行加 `*` 前缀便于 Excel 里筛）。"""
    head = ["差异", "machine_step", "field", "label"] + [r["label"] for r in cmp["runs"]]
    out = [head]
    for p in cmp["params"]:
        out.append(["*" if p["differ"] else "", p["step"], p["field"], p["label"], *p["vals"]])
    out.append([])
    out.append(["差异", "结果项", "", ""] + [r["label"] for r in cmp["runs"]])
    for r in cmp["results"]:
        out.append(["*" if r["differ"] else "", r["name"], "", "", *r["vals"]])
    return to_csv(out)


# ── 单位 / 命名 ───────────────────────────────────────────────────────────
def guess_unit(quantity: str) -> str:
    s = str(quantity or "")
    for suf, unit in UNIT_BY_SUFFIX:
        if s.endswith(suf):
            return unit
    return ""


def safe_name(s) -> str:
    return re.sub(r"_+", "_", re.sub(r"[/\\:*?\"<>|\s]+", "_", str(s if s is not None else "").strip())).strip("_")


def run_tag(run) -> str:
    """`run` 号置文件名末位并零填充 ⇒ 目录里按名字排序＝按 run 顺序（run02 < run10 < run21）。"""
    raw = str(run if run is not None else "").strip()
    if not raw:
        return ""
    m = re.search(r"\d+", raw)
    return "run" + str(int(m.group(0))).zfill(2) if m else "run" + safe_name(raw)


def ext_of(name: str) -> str:
    m = re.search(r"\.([A-Za-z0-9]+)$", str(name or ""))
    return "." + m.group(1).lower() if m else ""


def apply_name_rule(rule: str, ctx: dict) -> str:
    return re.sub(r"\{(\w+)\}", lambda m: "" if ctx.get(m.group(1)) is None else str(ctx.get(m.group(1))),
                  str(rule or ""))


def img_final_name(state: dict, img: dict, idx: int = 0, rule: str = "") -> str:
    """按命名规则算最终名：占位符 `{slot} {run} {recipe} {tag} {seq} {date} {ext} {orig}`。"""
    ctx = {"slot": safe_name(state.get("slot")), "run": run_tag(state.get("run")),
           "recipe": safe_name(state.get("recipe")), "tag": safe_name(img.get("tag") or "img"),
           "seq": str((idx or 0) + 1).zfill(2), "date": str(state.get("read_at") or "")[:10].replace("-", ""),
           "ext": ext_of(img.get("name")), "orig": img.get("name") or ""}
    n = apply_name_rule(rule or DEFAULT_NAME_RULE, ctx)
    if not re.search(r"\.[A-Za-z0-9]+$", n):
        n += ctx["ext"]                              # 规则没写扩展名 ⇒ 自动补
    return re.sub(r"\s+", "_", n)


def finalize_names(state: dict, imgs: list[dict], rule: str = "") -> list[dict]:
    """批量算最终名：**重名加序号**（同批内），返回带 `final` 的新列表。"""
    used: set[str] = set()
    out = []
    for i, img in enumerate(imgs or []):
        base = img_final_name(state, img, i, rule)
        if not img.get("name"):
            continue
        name = base
        n = 1
        while name.lower() in used:
            stem, ext = (name.rsplit(".", 1) + [""])[:2] if "." in name else (name, "")
            name = f"{stem}_{n:02d}" + (f".{ext}" if ext else "")
            n += 1
        used.add(name.lower())
        out.append({**img, "final": name, "rule": rule or DEFAULT_NAME_RULE})
    return out


def fname_for(state: dict) -> str:
    slot = safe_name(state.get("slot")) or "X"
    tag = run_tag(state.get("run"))
    return f"机台读数_{slot}" + (f"_{tag}" if tag else "") + ".csv"


# ── 结果 ↔ 行 ↔ 模板 ──────────────────────────────────────────────────────
def results_to_rows(results: list[dict]) -> list[dict]:
    return [{"machine_step": RESULT_STEP, "field": str(x.get("name")).strip(), "parsed_value": "",
             "machine_value": "" if x.get("value") is None else str(x.get("value")),
             "unit": x.get("unit") or "", "note": x.get("note") or "",
             "quantity": x.get("quantity") or "", "method": x.get("method") or "",
             "verification": x.get("verification") or ""}
            for x in (results or []) if str(x.get("name") or "").strip()]


def rows_to_results(rows: list[dict]) -> list[dict]:
    return [{"name": r.get("field") or "",
             "value": "" if r.get("machine_value") is None else str(r.get("machine_value")),
             "unit": r.get("unit") or "", "note": r.get("note") or "",
             "quantity": r.get("quantity") or "", "method": r.get("method") or "",
             "verification": r.get("verification") or ""}
            for r in (rows or []) if is_result_row(r)]


def tpl_from_results(results: list[dict]) -> list[dict]:
    return [{"name": str(x.get("name")).strip(), "unit": x.get("unit") or "",
             "quantity": x.get("quantity") or "", "method": x.get("method") or "",
             "verification": x.get("verification") or ""}
            for x in (results or []) if str(x.get("name") or "").strip()]


def results_from_tpl(items: list[dict], current: list[dict] | None = None) -> list[dict]:
    """套用模板 ⇒ 结果项。**同名项目保留已填数值**（工单 §二-2）。"""
    have = {str(x.get("name") or "").strip(): x for x in (current or [])}
    out = []
    for x in items or []:
        name = str(x.get("name") or "").strip()
        old = have.get(name) or {}
        out.append({"name": name, "value": old.get("value", ""), "unit": x.get("unit") or old.get("unit") or "",
                    "note": old.get("note", ""), "quantity": x.get("quantity") or "",
                    "method": x.get("method") or "", "verification": x.get("verification") or UNVERIFIED})
    return out


# ── 模板存取（工作区 JSON，不进公开仓）─────────────────────────────────────
def templates_path() -> Path:
    env = os.environ.get("OPENNANO_SHEET_TEMPLATES")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".opennano" / "sheet_templates.json"


def load_templates() -> dict:
    p = templates_path()
    if not p.exists():
        return {}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_template(name: str, items: list[dict]) -> dict:
    name = str(name or "").strip()
    if not name:
        raise ValueError("模板名不能为空")
    d = load_templates()
    d[name] = tpl_from_results(items)
    p = templates_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"ok": True, "name": name, "count": len(d[name]), "templates": sorted(d)}


def delete_template(name: str) -> dict:
    d = load_templates()
    d.pop(str(name or ""), None)
    p = templates_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"ok": True, "templates": sorted(d)}


# ── 导出（16 列契约）──────────────────────────────────────────────────────
def export_rows(state: dict, rows: list[dict], ranges: dict | None = None,
                results: list[dict] | None = None, imgs: list[dict] | None = None) -> list[list]:
    """按 16 列契约出表（列序＝契约）。`ranges` 给 Loop 区间覆写值（界面上设定）。"""
    ranges = ranges or {}
    out: list[list] = [list(COLUMNS)]
    for r in rows or []:
        mv = r.get("machine_value") or ""
        m = re.fullmatch(r"Loop([12]) 区间", str(r.get("field") or ""))
        if m:
            g = ranges.get(int(m.group(1))) or ranges.get(str(m.group(1))) or {}
            v = f"{g.get('a')}-{g.get('b')}" if g.get("a") and g.get("b") else ""
            if v:
                mv = v
        out.append([state.get("recipe", ""), r.get("machine_step"), r.get("field"),
                    r.get("parsed_value") or "", mv, "",
                    state.get("run", ""), state.get("reader", ""), state.get("read_at", ""),
                    r.get("unit") or "", r.get("note") or "", "", "", "",
                    state.get("core_run_id", ""), state.get("sample_id", "")])
    for r in results_to_rows(results or []):
        out.append([state.get("recipe", ""), r["machine_step"], r["field"], "", r["machine_value"], "",
                    state.get("run", ""), state.get("reader", ""), state.get("read_at", ""),
                    r["unit"], r["note"], r["quantity"], r["method"], r["verification"],
                    state.get("core_run_id", ""), state.get("sample_id", "")])
    for x in (imgs or []):
        if not str(x.get("name") or "").strip():
            continue
        fin = x.get("final") or img_final_name(state, x, 0, x.get("rule") or DEFAULT_NAME_RULE)
        out.append([state.get("recipe", ""), IMG_STEP, fin, "", "", "",
                    state.get("run", ""), state.get("reader", ""), state.get("read_at", ""),
                    x.get("tag") or "", x.get("name") or "", "", "", "",
                    state.get("core_run_id", ""), state.get("sample_id", "")])
    return out


# ── 落库预览（与 `ingest/datasets_results.py` 同一套规矩，只报不写）────────
def landing_preview(rows: list[dict], quantities: set[str] | None = None,
                    methods: set[str] | None = None) -> dict:
    """逐条回答"这行会被落库接受吗"。**只读**，不写任何东西（工单 §二-5）。"""
    core_run_id = next((str(r.get("core_run_id") or "").strip() for r in rows or []
                        if str(r.get("core_run_id") or "").strip()), "")
    imgs = [str(r.get("field") or "").strip() for r in rows or [] if is_image_row(r)]
    accepted, rejected = [], []
    n = 0
    for r in rows or []:
        if not is_result_row(r):
            continue
        item, q = str(r.get("field") or "").strip(), str(r.get("quantity") or "").strip()
        raw = str(r.get("machine_value") or "").strip()
        if not core_run_id:
            rejected.append({"item": item, "why": "整件缺 core_run_id（现场 Run 号 ≠ core run_id）⇒ 全部跳过"})
            continue
        if not item:
            rejected.append({"item": "(未命名)", "why": "结果行缺项目名"})
            continue
        if quantities is not None and q not in quantities:
            rejected.append({"item": item, "why": f"量名 `{q or '(空)'}` 不在契约 §三 ⇒ 落库会跳过"})
            continue
        try:
            float(raw)
        except ValueError:
            rejected.append({"item": item, "why": f"数值不是数（{raw!r}）"})
            continue
        n += 1
        method = str(r.get("method") or "").strip()
        note = str(r.get("note") or "").strip()
        accepted.append({"meas_id": f"{core_run_id}.MR{n:02d}", "item": item, "quantity": q,
                         "value": raw, "unit": str(r.get("unit") or "").strip(),
                         "method": method if (methods is None or method in methods) else "记录给出",
                         "verification": str(r.get("verification") or "").strip() or UNVERIFIED,
                         "note": (note + "；" if note else "") + ("图：" + " · ".join(imgs) if imgs else "")})
    if not accepted and imgs:
        rejected.append({"item": "(图片)", "why": "只有图片没有结果数值 ⇒ 图片无处挂（先录结果数值）"})
    return {"core_run_id": core_run_id, "accepted": accepted, "rejected": rejected,
            "images": imgs, "count": len(accepted),
            "note": "预览只读：落库仍走 解析 → 数据线复核 → build_core（本工具不写 core）"}


# ── 解析整表 ──────────────────────────────────────────────────────────────
def parse_sheet(text: str) -> dict:
    """一张抄读表 → `{head, rows, meta, stats, matrix, kinds, col_ok}`。"""
    raw = parse_csv(text)
    if not raw:
        return {"head": [], "rows": [], "meta": {}, "stats": stats([]), "matrix": build_matrix([]),
                "col_ok": False, "problems": ["空文件"]}
    head = [str(h).strip() for h in raw[0]]
    rows = [{h: (r[i] if i < len(r) else "") for i, h in enumerate(head)} for r in raw[1:]]
    problems = []
    if head[:6] != FROZEN_COLS:
        problems.append(f"前 6 列与契约不符：表里是 {head[:6]}；契约是 {FROZEN_COLS}")
    missing = [c for c in COLUMNS if c not in head]
    if missing:
        problems.append(f"缺列：{missing}（旧版导出可能只有前 6 列）")
    extra = [c for c in head if c not in COLUMNS]
    if extra:
        problems.append(f"多出契约外的列：{extra}")

    def first(col: str) -> str:
        return next((str(r.get(col) or "").strip() for r in rows if str(r.get(col) or "").strip()), "")

    meta = {"recipe": first("recipe"), "run": first("run"), "reader": first("reader"),
            "read_at": first("read_at"), "core_run_id": first("core_run_id"),
            "sample_id": first("sample_id")}
    kinds = {"reading": 0, "loop_range": 0, "loop_cycle": 0, "result": 0, "image": 0}
    for r in rows:
        k = row_kind(r)
        kinds[k] = kinds.get(k, 0) + 1
    return {"head": head, "rows": rows, "meta": meta, "stats": stats(rows),
            "matrix": build_matrix(rows), "kinds": kinds, "col_ok": not problems,
            "problems": problems}


def read_sheet_file(path: str | Path) -> dict:
    p = Path(path).expanduser()
    d = parse_sheet(p.read_text(encoding="utf-8-sig"))
    d["path"] = str(p)
    d["name"] = p.name
    return d


def sheet_dirs() -> list[Path]:
    """可扫的抄读表目录：`OPENNANO_RESULTS_DIR` → NAS 现场夹 → 工作区。"""
    out = []
    for cand in (os.environ.get("OPENNANO_RESULTS_DIR"), "/Volumes/共享/25_现场记录/菜单抄读",
                 str(Path(__file__).resolve().parents[2] / "samples" / "readings"),
                 str(Path.home() / ".opennano" / "readings")):
        if cand:
            out.append(Path(cand).expanduser())
    return out


def list_sheets() -> dict:
    """列出可载入的抄读表（各目录一层浅扫 `机台读数_*.csv` 与任意 csv）。"""
    found, seen = [], set()
    for d in sheet_dirs():
        if not d.is_dir():
            continue
        for p in sorted(d.glob("*.csv")):
            if p.name.startswith(".") or str(p.resolve()) in seen:
                continue
            seen.add(str(p.resolve()))
            try:
                head = p.read_text(encoding="utf-8-sig").splitlines()[:1]
                cols = [c.strip() for c in (head[0].split(",") if head else [])]
            except OSError:
                continue
            if "machine_step" not in cols:
                continue                                  # 不是抄读表，别混进来
            found.append({"path": str(p), "name": p.name, "dir": str(d),
                          "cols": len(cols), "frozen6_ok": cols[:6] == FROZEN_COLS,
                          "full16": cols == COLUMNS})
    return {"sheets": found, "dirs": [str(d) for d in sheet_dirs()]}


# ── 图片上传/改名归档（服务端）────────────────────────────────────────────
def readings_home() -> Path:
    env = os.environ.get("OPENNANO_READINGS_HOME")
    return Path(env).expanduser() if env else Path.home() / ".opennano" / "readings"


def image_dir(state: dict) -> Path:
    slot = safe_name(state.get("slot")) or "X"
    tag = run_tag(state.get("run")) or "run00"
    return readings_home() / f"{slot}_{tag}" / "images"


def save_upload(state: dict, filename: str, blob: bytes) -> dict:
    """把一张图片存到该抄读件的 images 目录（浏览器只能上传，落盘由服务端做）。"""
    d = image_dir(state)
    d.mkdir(parents=True, exist_ok=True)
    name = safe_name(Path(filename or "img").name) or "img"
    target = d / name
    i = 1
    while target.exists():                            # 同名不覆盖，加序号
        stem, ext = (target.stem, target.suffix)
        target = d / f"{stem}_{i:02d}{ext}"
        i += 1
    target.write_bytes(blob)
    return {"ok": True, "path": str(target), "name": target.name, "dir": str(d),
            "size": len(blob)}


def archive_images(state: dict, imgs: list[dict], rule: str = "", target_dir: str = "") -> dict:
    """按命名规则**改名并归档**：`images/` → `archive/`（或指定目录）。返回映射表。"""
    src_dir = image_dir(state)
    dst = Path(target_dir).expanduser() if target_dir else (src_dir.parent / "archive")
    dst.mkdir(parents=True, exist_ok=True)
    done, missing = [], []
    for x in finalize_names(state, imgs, rule):
        src = src_dir / str(x.get("name") or "")
        if not src.exists():
            missing.append(x.get("name"))
            continue
        target = dst / x["final"]
        i = 1
        while target.exists():
            stem, ext = target.stem, target.suffix
            target = dst / f"{stem}_{i:02d}{ext}"
            i += 1
        src.replace(target)
        done.append({"orig": x.get("name"), "final": target.name, "tag": x.get("tag") or "",
                     "path": str(target)})
    return {"ok": True, "archived": done, "missing": missing, "dir": str(dst),
            "archive_dir": str(dst), "images_dir": str(src_dir)}


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")

# ── 数据线侧的 method 词表（**运行时读他们的解析器**，不另编一份）──────────
def parser_path() -> Path:
    env = os.environ.get("OPENNANO_RESULTS_PARSER")
    if env:
        return Path(env).expanduser()
    try:
        from .menu_reader import _workspace
        return _workspace() / "个人空间/32_工艺数据资产/03_实验数据/ingest/datasets_results.py"
    except Exception:                                            # noqa: BLE001
        return Path.home() / "nonexistent-datasets_results.py"


def parser_methods() -> set[str] | None:
    """从 `datasets_results.py` 里抠出 `METHODS = {...}`。读不到 ⇒ `None`（预览时跳过词表检查并注明）。"""
    p = parser_path()
    if not p.exists():
        return None
    try:
        txt = p.read_text(encoding="utf-8")
    except OSError:
        return None
    m = re.search(r"METHODS\s*=\s*\{(.*?)\}", txt, re.S)
    if not m:
        return None
    return set(re.findall(r'"([^"]+)"', m.group(1))) or None


def parser_quantities() -> set[str] | None:
    """**照解析器的抠法**读 `schema §三` 受控量名（它才是落库的闸）。

    为什么不用 `form_contract.quantities()`：两者抠的区间/正则不同（实测 51 vs 48），
    预览若用宽的那份就会**少报**"这行会被拒"。这里与 `datasets_results.py` 对齐：
    `## 三、`…`## 四、` 之间、`` `snake_case` `` 形式、去掉表格词。
    """
    try:
        from .menu_reader import _workspace
        schema = _workspace() / "个人空间/32_工艺数据资产/03_实验数据/schema_v0.1.md"
    except Exception:                                            # noqa: BLE001
        return None
    if not schema.exists():
        return None
    try:
        txt = schema.read_text(encoding="utf-8")
    except OSError:
        return None
    if "## 三、" not in txt or "## 四、" not in txt:
        return None
    sec = txt.split("## 三、")[1].split("## 四、")[0]
    names = set(re.findall(r"`([a-z][a-z0-9_]*)`", sec))
    return names - {"quantity", "metrics", "snake_case"}


def corpus_quantities() -> list[str]:
    """公开 clone 没有私有 schema 时的兜底：**仓库样例语料里出现过的量名**。

    为什么需要：Neo 上 `git clone` 只有 `samples/`，契约不可达 ⇒ 选择框会空。
    兜底**不新增一份词表**（只读 `samples/core`，那是仓库里已有的中性样例），
    并让 `quantity_gate=False` —— 此时预览**不当闸**（不拿样例语料去判"不在 §三"）。
    """
    p = Path(__file__).resolve().parents[2] / "samples" / "core" / "measurements.csv"
    if not p.exists():
        return []
    try:
        with p.open(newline="", encoding="utf-8-sig") as f:
            return sorted({(r.get("quantity") or "").strip() for r in csv.DictReader(f)} - {""})
    except OSError:
        return []


def corpus_methods() -> list[str]:
    """同上（method 列）：公开 clone 时给选择框兜底。"""
    p = Path(__file__).resolve().parents[2] / "samples" / "core" / "measurements.csv"
    if not p.exists():
        return []
    try:
        with p.open(newline="", encoding="utf-8-sig") as f:
            return sorted({(r.get("method") or "").strip() for r in csv.DictReader(f)} - {""})
    except OSError:
        return []


def landing_vocab() -> dict:
    """给界面用的词表 ＋ **能不能当闸**的标志（`*_gate`）。

    三层来源：解析器（`schema §三` / `datasets_results.METHODS`，**唯一权威**）
    → 契约（`form_contract`）→ 语料兜底（`samples/core`，只够填选择框，**不当闸**）。
    """
    q, qsrc, qgate = None, "unavailable", False
    q = parser_quantities()
    if q:
        qsrc, qgate = "parser(schema §三)", True
    else:
        try:
            from . import form_contract as fc
            q, qsrc, qgate = set(fc.quantities()), "contract", True
        except Exception:                                        # noqa: BLE001
            pass
    if not q:
        q, qsrc, qgate = set(corpus_quantities()), "corpus(samples)", False
    m, msrc, mgate = parser_methods(), "unavailable", False
    if m:
        msrc, mgate = str(parser_path()), True
    else:
        try:
            from . import form_contract as fc
            m, msrc, mgate = set(fc.method or []), "contract", True
        except Exception:                                        # noqa: BLE001
            pass
    if not m:
        m, msrc, mgate = set(corpus_methods()), "corpus(samples)", False
    return {"quantities": sorted(q), "quantity_source": qsrc, "quantity_gate": qgate,
            "methods": sorted(m), "method_source": msrc, "method_gate": mgate}


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")

# ── 数据线侧的 method 词表（**运行时读他们的解析器**，不另编一份）──────────
def parser_path() -> Path:
    env = os.environ.get("OPENNANO_RESULTS_PARSER")
    if env:
        return Path(env).expanduser()
    try:
        from .menu_reader import _workspace
        return _workspace() / "个人空间/32_工艺数据资产/03_实验数据/ingest/datasets_results.py"
    except Exception:                                            # noqa: BLE001
        return Path.home() / "nonexistent-datasets_results.py"


def parser_methods() -> set[str] | None:
    """从 `datasets_results.py` 里抠出 `METHODS = {...}`。读不到 ⇒ `None`（预览时跳过词表检查并注明）。"""
    p = parser_path()
    if not p.exists():
        return None
    try:
        txt = p.read_text(encoding="utf-8")
    except OSError:
        return None
    m = re.search(r"METHODS\s*=\s*\{(.*?)\}", txt, re.S)
    if not m:
        return None
    return set(re.findall(r'"([^"]+)"', m.group(1))) or None


def parser_quantities() -> set[str] | None:
    """**照解析器的抠法**读 `schema §三` 受控量名（它才是落库的闸）。

    为什么不用 `form_contract.quantities()`：两者抠的区间/正则不同（实测 51 vs 48），
    预览若用宽的那份就会**少报**"这行会被拒"。这里与 `datasets_results.py` 对齐：
    `## 三、`…`## 四、` 之间、`` `snake_case` `` 形式、去掉表格词。
    """
    try:
        from .menu_reader import _workspace
        schema = _workspace() / "个人空间/32_工艺数据资产/03_实验数据/schema_v0.1.md"
    except Exception:                                            # noqa: BLE001
        return None
    if not schema.exists():
        return None
    try:
        txt = schema.read_text(encoding="utf-8")
    except OSError:
        return None
    if "## 三、" not in txt or "## 四、" not in txt:
        return None
    sec = txt.split("## 三、")[1].split("## 四、")[0]
    names = set(re.findall(r"`([a-z][a-z0-9_]*)`", sec))
    return names - {"quantity", "metrics", "snake_case"}

