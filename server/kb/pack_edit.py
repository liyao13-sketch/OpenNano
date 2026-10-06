"""实验数据包「填数」：在工具里直接读写包内的 `measurements` / `observations`。

为什么需要（现状断点，2026-10-06 owner 点名）：
    `expack.py` 的自述里写着「**卡不会回写画布。现场只允许在 `measurements.csv`/`observations.csv` 填数**」——
    也就是**填数据必须离开工具**：导出 zip → 找到 CSV → 手改 → 再导入回画布。
    本模块把这一步搬进工具：列出包 → 载入两张表 → 表单里填 → **校验后原子写回**。

红线（与数据线协议一致，逐条落在下面的校验里）：
  1. **只写两张表**（`measurements.csv` / `observations.csv`）。`batches/runs/steps/manifest` **一律不动**，
     更**不新建 run**。列**从包内现成表头读**（不另编一份）⇒ 结构上不可能多一列或少一列。
  2. **不推断**：`run_id` 必须已在 `runs.csv` 里，否则报错并列出合法值；**空值行丢弃**（空 ≠ 0）。
  3. **协议 §15.1**：`measurement.run_id` ＝「这个数是在哪次工艺之后测出来的」⇒
     **检测 run（`METROLOGY_STAGES`）上不许挂 measurement**；落在检测 run 上的行一律拒绝，
     并指出应挂到它的 `parent_run_id`（那条被测的工艺 run）。
  4. **不静默**：`meas_id` / `obs_id` / `sample_id` 留空时按包内既有同构规则补
     （`{run_id}.M{nn}` / `{run_id}.O{nn}` / 该 run 自己的 `sample_id`），但**逐条报出来**（`filled`）。
  5. **并发**：`save` 必须带 `revisions`（`load` 时返回的两表 sha256）；与盘上不一致 ⇒ 拒写。
  6. **原子写**：`engine.atomic.write_text_atomic`（临时文件 + `os.replace`），权限 0644（与包内一致）。
  7. **可写范围**：只有**工作区**（`~/.opennano/packs/`，或 `OPENNANO_PACK_ROOTS`）里的包可写；
     仓库样例 `samples/expack/` 与别处都是**只读来源** ⇒ 要填先「复制到工作区」。
  8. **枚举来源**：优先读契约（`form_contract`）；契约不可达（公开 clone 无私有 schema）时，
     **回落到"包内已有取值 + `samples/core/*` 样例语料"** 并注明来源 —— 既不硬编词表，也不让选择框空着。
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import shutil
import zipfile
from datetime import datetime
from pathlib import Path

from engine import atomic                      # `engine/` 与 `kb/` 同级（照 kb 其它模块的导入面）
from .expack import METROLOGY_STAGES, is_metrology_stage
from .menu_reader import _workspace  # noqa: F401  （保留：与其它 kb 模块同一导入面，便于将来加 env 覆盖点）

#: 允许编辑的表（白名单）。别的表一律不碰。
EDITABLE = ("measurements", "observations")
#: 只读、仅供 UI 展示的 run 列
RUN_COLS = ("run_id", "stage", "sample_id", "parent_run_id", "date", "tool", "tool_id", "status")
MANIFEST = "manifest.json"
#: 包内没有该表时的默认列（与 `expack.build_expack` 同构；只用于"新建空表"这一种情形）
DEFAULT_HEADER = {
    "measurements": ["meas_id", "run_id", "sample_id", "quantity", "value", "unit",
                     "method", "loc", "n", "uncertainty", "source_artifact_id",
                     "measured_by", "verification", "note"],
    "observations": ["obs_id", "run_id", "sample_id", "obs_type", "severity",
                     "description", "judgement", "action", "artifact_id",
                     "recorded_by", "date"],
}


class PackError(ValueError):
    """包不可用 / 要写入的数据不合法。消息面向使用者，可直接显示。"""


class PackConflict(PackError):
    """并发冲突：盘上内容在打开之后变了 ⇒ 界面应提示"重新载入"，而不是让用户改输入。"""


# ── 小工具 ────────────────────────────────────────────────────────────────
def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _cell(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def _parse(raw: bytes) -> tuple[list[str], list[dict]]:
    """包内 CSV → (表头, 行)。编码按既有惯例宽松读（`utf-8-sig`）。"""
    if not raw.strip():
        return [], []
    text = raw.decode("utf-8-sig")
    rdr = csv.DictReader(io.StringIO(text, newline=""))
    return list(rdr.fieldnames or []), [dict(r) for r in rdr]


def _dump(header: list[str], rows: list[dict]) -> bytes:
    """与 `expack._csv_bytes` 同构：`\\n` 行尾 · utf-8 **无 BOM**。"""
    if not header:
        return b""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    for r in rows:
        w.writerow([_cell(r.get(c, "")) for c in header])
    return buf.getvalue().encode("utf-8")


# ── 可访问/可写的包根（不让 HTTP 接口变成任意路径读写器）────────────────
def pack_home() -> Path:
    """工具的包工作区（导出/解包/填数都落这里）——**不放 `/tmp`**（红线：数据禁指 tmp）。"""
    env = os.environ.get("OPENNANO_PACK_HOME")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".opennano" / "packs"


def _repo_samples() -> Path:
    return Path(__file__).resolve().parents[2] / "samples" / "expack"


def allowed_roots() -> list[Path]:
    """**可读**的包根：仓库样例 ＋ 工作区（＋ env 追加）。"""
    roots: list[Path] = []
    for chunk in os.environ.get("OPENNANO_PACK_ROOTS", "").split(os.pathsep):
        if chunk.strip():
            roots.append(Path(chunk).expanduser())
    roots += [_repo_samples(), pack_home()]
    out: list[Path] = []
    for r in roots:
        try:
            out.append(r.resolve())
        except OSError:
            continue
    return out


def writable_roots() -> list[Path]:
    """**可写**的包根（没有仓库样例 —— 免得把演示包改脏、误提交）。"""
    roots: list[Path] = []
    for chunk in os.environ.get("OPENNANO_PACK_ROOTS", "").split(os.pathsep):
        if chunk.strip():
            roots.append(Path(chunk).expanduser())
    roots.append(pack_home())
    out: list[Path] = []
    for r in roots:
        try:
            out.append(r.resolve())
        except OSError:
            continue
    return out


def _under(p: Path, roots: list[Path]) -> bool:
    return any(p == r or r in p.parents for r in roots)


def resolve_pack(path: str, *, must_be_dir: bool = False, must_be_writable: bool = False) -> Path:
    """把用户给的路径解析成允许范围内的包（目录或 zip）。越界/不存在 ⇒ `PackError`。"""
    if not (path or "").strip():
        raise PackError("未指定数据包路径")
    p = Path(path).expanduser()
    try:
        p = p.resolve()
    except OSError as e:
        raise PackError(f"路径不可解析：{e}") from e
    if not _under(p, allowed_roots()):
        raise PackError("该路径不在允许的数据包目录内（可用目录：" +
                        "、".join(str(r) for r in allowed_roots()) + "）")
    if not p.exists():
        raise PackError(f"路径不存在：{p}")
    if must_be_writable and not _under(p, writable_roots()):
        raise PackError("该包位于只读来源（如仓库 `samples/`）⇒ 请先「复制到工作区」再填")
    if must_be_dir and not p.is_dir():
        raise PackError("该包是 zip ⇒ 请先「复制到工作区」（zip 不支持原地改写）")
    if p.is_dir():
        if not (p / MANIFEST).exists() or not (p / "runs.csv").exists():
            raise PackError(f"不是实验数据包（缺 {MANIFEST} 或 runs.csv）：{p}")
    elif p.suffix.lower() != ".zip":
        raise PackError(f"只支持『包目录』或『.zip 包』：{p}")
    return p


def _is_pack_zip(p: Path) -> bool:
    try:
        with zipfile.ZipFile(p) as z:
            names = z.namelist()
    except (zipfile.BadZipFile, OSError):
        return False
    return any(n.endswith(MANIFEST) for n in names) and any(n.endswith("runs.csv") for n in names)


# ── 载入 ─────────────────────────────────────────────────────────────────
def _tables(p: Path) -> tuple[dict, dict]:
    """读四张表（runs 只读 + 两张可编辑）＋ manifest。目录与 zip 同一入口。"""
    out: dict = {}
    man: dict = {}
    if p.is_dir():
        for name in ("runs", *EDITABLE):
            f = p / f"{name}.csv"
            raw = f.read_bytes() if f.exists() else b""
            header, rows = _parse(raw)
            out[name] = {"header": header, "rows": rows, "sha256": _sha(raw), "exists": f.exists()}
        mf = p / MANIFEST
        if mf.exists():
            try:
                man = json.loads(mf.read_bytes().decode("utf-8-sig"))
            except (ValueError, OSError) as e:
                man = {"error": f"读 manifest 失败：{e}"}
    else:
        with zipfile.ZipFile(p) as z:
            names = z.namelist()
            for name in ("runs", *EDITABLE):
                cand = [n for n in names if n.endswith(f"{name}.csv")]
                raw = z.read(cand[0]) if cand else b""
                header, rows = _parse(raw)
                out[name] = {"header": header, "rows": rows, "sha256": _sha(raw),
                             "exists": bool(cand)}
            mcand = [n for n in names if n.endswith(MANIFEST)]
            if mcand:
                try:
                    man = json.loads(z.read(mcand[0]).decode("utf-8-sig"))
                except (ValueError, OSError) as e:
                    man = {"error": f"读 manifest 失败：{e}"}
    return out, man


def _brief(p: Path) -> dict:
    """包的摘要（读 manifest ＋ 数行），供列表用；坏了不让整个列表挂掉。"""
    info: dict = {"path": str(p), "kind": "dir" if p.is_dir() else "zip"}
    try:
        tabs, man = _tables(p)
        info["runs_n"] = len(tabs["runs"]["rows"])
        info["measurements_n"] = len(tabs["measurements"]["rows"])
        info["observations_n"] = len(tabs["observations"]["rows"])
        info["batch_id"] = man.get("batch_id") or p.stem
        info["source"] = man.get("source", "")
        info["purpose"] = man.get("purpose", "")
        info["created_at"] = man.get("created_at", "")
    except Exception as e:                                          # noqa: BLE001
        info["error"] = f"读包失败：{e}"
        info.setdefault("batch_id", p.stem)
    try:
        info["writable"] = p.is_dir() and _under(p.resolve(), writable_roots())
    except OSError:
        info["writable"] = False
    info["editable"] = bool(info["writable"])
    return info


def list_packs() -> dict:
    """列可填的包：仓库样例 + 工作区（各做一层浅扫描）。"""
    found: list[dict] = []
    seen: set[str] = set()
    for root in allowed_roots():
        if not root.exists():
            continue
        cands: list[Path] = [root]
        if root.is_dir():
            try:
                cands += sorted(root.iterdir())
            except OSError:
                pass
        for c in cands:
            if not c.exists():
                continue
            try:
                if c.is_dir():
                    if not (c / MANIFEST).exists():
                        continue
                elif not (c.suffix.lower() == ".zip" and _is_pack_zip(c)):
                    continue
                key = str(c.resolve())
            except OSError:
                continue
            if key in seen:
                continue
            seen.add(key)
            found.append(_brief(c))
    found.sort(key=lambda x: (not x.get("editable"), x.get("batch_id") or ""))
    return {"packs": found, "roots": [str(r) for r in allowed_roots()],
            "writable_roots": [str(r) for r in writable_roots()],
            "pack_home": str(pack_home())}


# ── 枚举来源：契约优先，缺失时回落到语料 ─────────────────────────────────
def _corpus(table: str) -> list[dict]:
    """仓库样例语料 `samples/core/<table>.csv`（公开、中性、无真机台）。"""
    p = Path(__file__).resolve().parents[2] / "samples" / "core" / f"{table}.csv"
    if not p.exists():
        return []
    try:
        _, rows = _parse(p.read_bytes())
        return rows
    except OSError:
        return []


def vocab(pack_tables: dict[str, list[dict]] | None = None) -> dict:
    """量名 / 现象词 / 严重度 / 方法 / 可信度 —— 给下拉框用。

    `contract_quantities` 是**契约原样**的集合（判"未受控量名"只能用它，
    不能把语料里的名字混进去当契约，否则等于自己造了一份词表）。
    """
    out: dict = {"quantities": [], "contract_quantities": [], "obs_types": [],
                 "severities": [], "methods": [], "verifications": [], "source": "corpus"}
    try:
        from . import form_contract as fc
        c = fc.contract()
        out["contract_quantities"] = list(c.get("quantities") or [])
        out["obs_types"] = [o.get("obs_type", "") for o in (c.get("observations") or [])]
        out["severities"] = sorted({(o.get("severity") or "") for o in (c.get("observations") or [])} - {""})
        out["methods"] = list(c.get("method") or [])
        out["verifications"] = list(c.get("verification") or [])
        out["source"] = "contract"
    except Exception:                                                # noqa: BLE001
        pass
    q: set[str] = set(out["contract_quantities"])
    o: set[str] = set(out["obs_types"])
    sev: set[str] = set(out["severities"])
    for rows in (pack_tables or {}).values():
        for r in rows:
            for key, bucket in (("quantity", q), ("obs_type", o), ("severity", sev)):
                v = (r.get(key) or "").strip()
                if v:
                    bucket.add(v)
    for tbl, col, bucket in (("measurements", "quantity", q), ("observations", "obs_type", o),
                             ("observations", "severity", sev)):
        for r in _corpus(tbl):
            v = (r.get(col) or "").strip()
            if v:
                bucket.add(v)
    out["quantities"] = sorted(q)
    out["obs_types"] = sorted(o)
    out["severities"] = sorted(sev)
    return out


def load_pack(path: str) -> dict:
    """载入一个包：读出两张可编辑表 ＋ run 清单 ＋ 枚举 ＋ 修订号（并发用）。"""
    p = resolve_pack(path)
    tabs, man = _tables(p)
    runs = tabs["runs"]["rows"]
    by_id = {(r.get("run_id") or "").strip(): r for r in runs if (r.get("run_id") or "").strip()}
    run_list = []
    for rid, r in by_id.items():
        run_list.append({**{c: (r.get(c) or "") for c in RUN_COLS},
                         "is_metrology": is_metrology_stage((r.get("stage") or "").strip()),
                         "has_measurements": any((m.get("run_id") or "").strip() == rid
                                                 for m in tabs["measurements"]["rows"])})
    run_list.sort(key=lambda x: (x.get("date") or "", x.get("run_id") or ""))
    warnings: list[str] = []
    if not p.is_dir():
        warnings.append("这是 zip 包（只读浏览）：要填数请先「复制到工作区」。")
    elif not _under(p.resolve(), writable_roots()):
        warnings.append("这个包在只读来源（仓库样例）里：要填数请先「复制到工作区」。")
    if not tabs["measurements"]["exists"]:
        warnings.append("包内没有 measurements.csv ⇒ 只有你真填了行才会新建这张表。")
    return {
        "path": str(p), "kind": "dir" if p.is_dir() else "zip",
        "writable": p.is_dir() and _under(p.resolve(), writable_roots()),
        "batch_id": man.get("batch_id") or p.stem, "manifest": man,
        "columns": {k: tabs[k]["header"] for k in EDITABLE},
        "rows": {k: tabs[k]["rows"] for k in EDITABLE},
        "runs": run_list,
        "revisions": {k: tabs[k]["sha256"] for k in EDITABLE},
        "vocab": vocab({k: tabs[k]["rows"] for k in EDITABLE}),
        "rules": {
            "editable_tables": list(EDITABLE),
            "metrology_stages": list(METROLOGY_STAGES),
            "detection_rule": ("measurement.run_id ＝「这个数是在哪次工艺之后测出来的」；"
                               "检测 run 上不许挂 measurement ⇒ 请挂到它前面的那条工艺 run"),
            "no_inference": "run 必须已存在；空值行丢弃（空≠0）；不新建 run、不改列、不改其它表",
        },
        "warnings": warnings,
    }


# ── 校验与保存 ───────────────────────────────────────────────────────────
def _norm_rows(rows, header: list[str], table: str) -> list[dict]:
    """把前端来的行对齐到包内表头：**未知列直接报错**（挡住拼写错与 UI 漂移）。"""
    if rows is None:
        return []
    if not isinstance(rows, list):
        raise PackError(f"{table} 必须是行数组")
    out: list[dict] = []
    for i, r in enumerate(rows, 1):
        if not isinstance(r, dict):
            raise PackError(f"{table} 第 {i} 行不是对象")
        bad = [k for k in r if k not in header]
        if bad:
            raise PackError(f"{table} 第 {i} 行含包内没有的列：{'、'.join(bad)}"
                            f"（包内列：{'、'.join(header)}）")
        out.append({c: _cell(r.get(c, "")) for c in header})
    return out


def _next_id(rows: list[dict], col: str, rid: str, tag: str) -> str:
    """按包内既有同构规则补 id：`{run_id}.{tag}{nn}`（与 expack 侧一致）。"""
    n, pre = 0, f"{rid}.{tag}"
    for r in rows:
        v = (r.get(col) or "").strip()
        if v.startswith(pre) and v[len(pre):].isdigit():
            n = max(n, int(v[len(pre):]))
    return f"{pre}{n + 1:02d}"


def _check_runs(rows: list[dict], by_id: dict[str, dict], table: str) -> None:
    # 空 run_id 不算"非法"：那是"这一行还没填"，由各校验器分别处置
    #（全空行 ⇒ 跳过；填了量却没填 run ⇒ 单独报"没填 run_id"）。
    bad = sorted({(r.get("run_id") or "").strip() for r in rows
                  if (r.get("run_id") or "").strip()
                  and (r.get("run_id") or "").strip() not in by_id})
    if bad:
        raise PackError(f"{table} 里有 {len(bad)} 个 run_id 不在本包 runs.csv 中："
                        f"{'、'.join(bad[:8])}{' …' if len(bad) > 8 else ''}"
                        f"（本包合法 run_id 共 {len(by_id)} 个；工具不替你新建 run）")


def _validate_measurements(rows: list[dict], by_id: dict[str, dict],
                           contract_q: set[str]) -> tuple[list[dict], dict]:
    # ⚠️ 先整体查 run_id 合法性：不能等筛完再查 —— 非法行会在下面的 `continue` 里被丢掉，
    #    那样"写了个不存在的 run"就变成**静默丢弃**（2026-10-06 冒烟测试抓到的真 bug）。
    _check_runs(rows, by_id, "measurements")
    clean: list[dict] = []
    filled: list[str] = []
    dropped_empty = 0
    warnings: list[str] = []
    for r in rows:
        rid = (r.get("run_id") or "").strip()
        q = (r.get("quantity") or "").strip()
        val = (r.get("value") or "").strip()
        if not rid and not q and not val:
            continue                                    # 全空行＝没填
        if not rid:
            raise PackError("measurements 有一行没填 run_id")
        run = by_id.get(rid)
        if run is None:
            continue                                    # 交给 _check_runs 一次性报全
        if not q:
            raise PackError(f"measurements 的 run {rid} 有一行没填 quantity")
        if not val:
            dropped_empty += 1                          # 空 ≠ 0
            continue
        stage = (run.get("stage") or "").strip()
        if is_metrology_stage(stage):
            par = (run.get("parent_run_id") or "").strip()
            hint = f"应挂到它前面的工艺 run：{par}" if par else "该检测 run 没有 parent_run_id，无法归位"
            raise PackError(f"协议 §15.1：检测 run 上不许挂 measurement。"
                            f"run {rid}（stage={stage}）是检测 run ⇒ {hint}")
        row = dict(r)
        row["run_id"], row["quantity"], row["value"] = rid, q, val
        if not (row.get("meas_id") or "").strip():
            row["meas_id"] = _next_id(clean, "meas_id", rid, "M")
            filled.append(f"{row['meas_id']}（meas_id 由工具按 {rid}.Mnn 补）")
        if not (row.get("sample_id") or "").strip():
            sid = (run.get("sample_id") or "").strip()
            if sid:
                row["sample_id"] = sid
                filled.append(f"{row['meas_id']} 的 sample_id ← run 自带 {sid}")
        if contract_q and q not in contract_q:
            warnings.append(f"量名 `{q}` 不在契约 §三 ⇒ 任何视图都取不到它；请与数据线确认")
        clean.append(row)
    _check_runs(clean, by_id, "measurements")
    return clean, {"filled": filled, "dropped_empty": dropped_empty, "warnings": warnings}


def _validate_observations(rows: list[dict], by_id: dict[str, dict]) -> tuple[list[dict], dict]:
    _check_runs(rows, by_id, "observations")      # 同上：先整体查，别让非法行被静默丢掉
    clean: list[dict] = []
    filled: list[str] = []
    dropped_empty = 0
    for r in rows:
        rid = (r.get("run_id") or "").strip()
        ot = (r.get("obs_type") or "").strip()
        desc = (r.get("description") or "").strip()
        if not rid and not ot and not desc:
            continue
        if not rid:
            raise PackError("observations 有一行没填 run_id")
        if rid not in by_id:
            continue                                    # 同上，统一报
        if not ot:
            dropped_empty += 1                          # 没选现象类型＝没记
            continue
        row = dict(r)
        row["run_id"], row["obs_type"] = rid, ot
        if not (row.get("obs_id") or "").strip():
            row["obs_id"] = _next_id(clean, "obs_id", rid, "O")
            filled.append(f"{row['obs_id']}（obs_id 由工具按 {rid}.Onn 补）")
        if not (row.get("sample_id") or "").strip():
            sid = (by_id[rid].get("sample_id") or "").strip()
            if sid:
                row["sample_id"] = sid
        clean.append(row)
    _check_runs(clean, by_id, "observations")
    return clean, {"filled": filled, "dropped_empty": dropped_empty, "warnings": []}


def save_pack(path: str, tables: dict, revisions: dict | None = None,
              allow_clear: bool = False) -> dict:
    """把两张表写回包目录（原地、原子、带并发校验）。

    `tables` = `{"measurements": [...], "observations": [...]}`，**缺的表＝不动**。
    `revisions` = `load_pack` 返回的 sha256；任一不符即拒写（防覆盖别人的改动）。
    `allow_clear` = 允许把一张原本**非空**的表写成**0 行**（默认禁止 —— 见下面的清空闸）。
    """
    p = resolve_pack(path, must_be_dir=True, must_be_writable=True)
    want = {k: v for k, v in (tables or {}).items() if k in EDITABLE}
    unknown = [k for k in (tables or {}) if k not in EDITABLE]
    if unknown:
        raise PackError(f"只能写 {'、'.join(EDITABLE)}；收到不可编辑的表：{'、'.join(unknown)}")
    if not want:
        raise PackError(f"没有要写的内容（可写表：{'、'.join(EDITABLE)}）")

    cur, _man = _tables(p)
    by_id = {(r.get("run_id") or "").strip(): r for r in cur["runs"]["rows"]
             if (r.get("run_id") or "").strip()}
    if not by_id:
        raise PackError("包内 runs.csv 为空 ⇒ 没有可挂数据的 run（工具不建 run）")

    for name in want:                                   # 并发闸：写前逐表比 sha256
        got = (revisions or {}).get(name)
        if got and got != cur[name]["sha256"]:
            raise PackConflict(f"{name}.csv 在你打开之后已被改动（revision 不一致）⇒ 拒写；"
                               f"请重新载入包再改，以免覆盖别人的改动")

    vt = vocab({k: cur[k]["rows"] for k in EDITABLE})
    contract_q = set(vt["contract_quantities"])

    saved: list[str] = []
    report: dict = {}
    for name in want:
        header = cur[name]["header"] or list(DEFAULT_HEADER[name])
        # 「漏字段＝保留、空串＝清空」：把盘上同 id 行里**本次没提交的列**补回来。
        # ⚠️ 必须在 `_norm_rows` **之前**做 —— 规范化会把每一列都补成空串，
        #    "没提交"这个信息到那时就丢了（第一版就是这样，测试当场红）。
        id_col = "meas_id" if name == "measurements" else "obs_id"
        prev = {(r.get(id_col) or "").strip(): r for r in cur[name]["rows"]
                if (r.get(id_col) or "").strip()}
        for r in (want[name] or []):
            if not isinstance(r, dict):
                continue                                  # 交给 _norm_rows 报错
            old_row = prev.get((r.get(id_col) or "").strip())
            if not old_row:
                continue
            for c in header:
                if c not in r:
                    r[c] = _cell(old_row.get(c, ""))
        rows = _norm_rows(want[name], header, name)
        clean, rep = (_validate_measurements(rows, by_id, contract_q) if name == "measurements"
                      else _validate_observations(rows, by_id))
        file = p / f"{name}.csv"
        cur_raw = file.read_bytes() if file.exists() else b""
        new_raw = _dump(header, clean)
        before = cur[name]["rows"]
        b_ids = [(r.get(id_col) or "").strip() for r in before]
        c_ids = [(r.get(id_col) or "").strip() for r in clean]
        rep.update({"before": len(before), "after": len(clean),
                    "removed": len([i for i in b_ids if i and i not in c_ids]),
                    "added": len([i for i in c_ids if i and i not in b_ids]),
                    "note": "removed/added 按 id 比对『盘上』与『本次提交』"})
        # 🛡 清空闸：盘上非空、这次却要写成 0 行 ⇒ 拒写（除非显式 allow_clear）。
        #    为什么必须加：2026-10-06 冒烟测试里，"坏 run_id 被静默跳过"那支正是把一张
        #    有 5 行的 measurements.csv 写成了**只有表头** —— 一次静默清空。宁可拦错，不可清错。
        if before and not clean and not allow_clear:
            raise PackError(f"{name}.csv 盘上有 {len(before)} 行，而这次提交是 0 行 ⇒ 拒写。"
                            f"（确认要清空整张表，请显式带上 allow_clear）")
        if new_raw != cur_raw:
            _backup(p, name, cur_raw)                      # 写前留一份（工作区，不在包内）
            atomic.write_text_atomic(file, new_raw.decode("utf-8"), mode=0o644)
            saved.append(f"{name}.csv")
        report[name] = rep

    out = {"path": str(p), "saved": saved, "report": report,
           "saved_at": datetime.now().isoformat(timespec="seconds"),
           "revisions": {k: _sha((p / f"{k}.csv").read_bytes()) for k in EDITABLE
                         if (p / f"{k}.csv").exists()}}
    warn = sorted({w for r in report.values() for w in r.get("warnings", [])})
    if warn:
        out["warnings"] = warn
    return out


def _backup(pack_dir: Path, table: str, raw: bytes, keep: int = 20) -> None:
    """写前把旧表存到**工作区**（不在包内，免得污染包结构 / 被落库程序读到）。

    ⚠️ 为什么值得存：包是"现场唯一记录"，手滑清空/改坏时能拿回来（2026-10-06 那次静默清空
    就是靠冒烟测试发现；有备份的话当场就能还原）。
    """
    if not raw:
        return
    try:
        d = pack_home() / "_backups" / pack_dir.name
        d.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        (d / f"{stamp}-{table}.csv").write_bytes(raw)
        olds = sorted(d.glob(f"*-{table}.csv"))
        for f in olds[:-keep]:
            try:
                f.unlink()
            except OSError:
                pass
    except OSError:
        pass                                              # 备份失败不该挡住正常保存


def copy_to_home(path: str) -> dict:
    """把包**复制到工作区**（zip 解压 / 只读目录拷贝），返回可写的目录包。

    为什么需要：从画布「导出包」得到的是一份 zip（zip 不支持原地改写），
    而仓库样例又是只读来源。复制到工作区后就能填数，再「下载为包」交给数据线。
    """
    src = resolve_pack(path)
    if src.is_dir() and _under(src, writable_roots()):
        return {"path": str(src), "already_writable": True,
                "batch_id": _brief(src).get("batch_id")}
    batch = _brief(src).get("batch_id") or src.stem
    dest = pack_home() / batch
    if dest.exists():
        dest = pack_home() / f"{batch}_{datetime.now().strftime('%H%M%S')}"
    dest.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        for f in sorted(src.rglob("*")):
            if f.is_dir() or f.name.startswith("."):
                continue
            target = dest / f.relative_to(src)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, target)
    else:
        with zipfile.ZipFile(src) as z:
            for n in z.namelist():
                if n.endswith("/") or n.startswith("/") or ".." in Path(n).parts:
                    continue                            # 防 zip 路径穿越
                target = dest / n
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(z.read(n))
    return {"path": str(dest), "from": str(src), "batch_id": batch, "copied": True}


def pack_as_zip(path: str) -> tuple[bytes, str]:
    """把（已填好的）目录包压成 zip，用于「下载成包」交给数据线落库。"""
    p = resolve_pack(path)
    if not p.is_dir():
        return p.read_bytes(), p.name
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(p.rglob("*")):
            if f.is_dir() or f.name.startswith("."):
                continue
            z.write(f, f.relative_to(p).as_posix())
    return buf.getvalue(), f"{_brief(p).get('batch_id') or p.name}.zip"
