"""用户全局资产库:工艺(设备/子步骤) / 参数注册表 / 参数依赖边 + 默认设置。

持久化到 ~/.opennano/library.json,跨工程共享。

三层模型:
- equipment:  工艺种类(7 模块),每个含参数模板 + 参数接口 inputs/outputs
- params:     全局参数注册表(5 类关键参数,用户可增)
- param_links:参数依赖边(from 影响 to)= "受什么影响 / 影响什么"
"""
from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

from opennano_config import LIBRARY_PATH

from .process_catalog import CATEGORIES, PROCESSES  # 104 种工艺目录(数据)

CATEGORY_BY_SUBTYPE = {c: c for c in CATEGORIES}

#: 资产库默认路径（可用 `OPENNANO_LIBRARY` 覆盖）—— 2026-09-16 审计：原来写死在这里，
#: 测试/部署都改不动（与 `opennano_config` 文件头"不许写死"的教训冲突）。
DEFAULT_PATH = LIBRARY_PATH


def file_rev(path: Path) -> str:
    """文件内容指纹（sha1 前 16 位）。不存在 → 空串。**用来判"盘上变了没有"，不用于安全。**"""
    try:
        return hashlib.sha1(Path(path).read_bytes()).hexdigest()[:16]
    except OSError:
        return ""

# 参数注册表种子(5 类关键参数;工艺条件类=设备参数,不重复入注册表)
PARAMS_SEED = {
    "胶CD":   {"unit": "nm", "category": "尺寸"},
    "胶SWA":  {"unit": "°", "category": "尺寸"},
    "硅CD":   {"unit": "nm", "category": "尺寸"},
    "侧壁角": {"unit": "°", "category": "尺寸"},
    "scallop": {"unit": "nm", "category": "尺寸"},   # Bosch 侧壁扇贝,传给下游的关键量
    "LWR":    {"unit": "nm", "category": "尺寸"},
    "套刻精度": {"unit": "nm", "category": "尺寸"},
    "均匀性": {"unit": "%", "category": "尺寸"},
    "膜厚":   {"unit": "nm", "category": "膜厚"},
    "刻蚀深度": {"unit": "nm", "category": "膜厚"},
    "选择比": {"unit": "", "category": "膜厚"},
    "应力":   {"unit": "MPa", "category": "材料"},
    "掺杂浓度": {"unit": "cm⁻³", "category": "材料"},
    "结晶度": {"unit": "%", "category": "材料"},
    "粗糙度": {"unit": "nm", "category": "材料"},
    "缺陷密度": {"unit": "cm⁻²", "category": "质量"},
    "电学性能": {"unit": "Ω/sq", "category": "质量"},
    "实测CD": {"unit": "nm", "category": "尺寸"},
}

# 参数依赖边种子: from 影响 to
PARAM_LINKS_SEED = [
    {"from": "胶CD", "to": "硅CD"},
    {"from": "胶SWA", "to": "侧壁角"},
    {"from": "膜厚", "to": "刻蚀深度"},
    {"from": "硅CD", "to": "实测CD"},
    {"from": "胶CD", "to": "LWR"},
]


class LibraryConflict(Exception):
    """库文件在**本进程之外**被改过 ⇒ 拒绝覆盖（团队化的第一道保护）。

    来历（2026-09-15，真实踩到）：内网服务器形态下，`library.json`（实测 ~90 KB）是**整份覆盖**写的。
    我在会话里手改了 4 台机台的 `tool_id`，而**运行中的服务进程**内存里还是旧版本 ——
    它下一次任何保存（加个参数、改台机）就会把我的手改**整份抹掉**，而且**没有任何声音**。
    多人协同时这条更致命：两个人的改动必然有一个被静默丢弃。
    ⇒ 加载时记住文件的 sha1，保存前比对；变了就**拒写并报出**（宁可这次不落盘，也不覆盖别人的改动）。
    """



def category_for_subtype(subtype: str) -> str | None:
    return CATEGORY_BY_SUBTYPE.get(subtype)


class LibraryStore:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else DEFAULT_PATH
        self.data = {
            "version": 1,
            "equipment": {}, "materials": {"resist": []}, "recipes": {},
            "defaults": {"equipment": {}, "resist": ""},
            "film_props": {}, "params": {}, "param_links": [],
            "influence_rules": [],
        }
        #: 库文件读不动时的**可读原因**（空 = 正常）。给界面看，不是给日志看。
        self.load_error = ""
        #: 损坏文件的留档路径（原文件改名保留，绝不删）
        self.corrupt_backup = ""
        #: 本次运行**禁止写库**（见 `_save`）
        self.save_blocked = False
        #: 加载/上次保存时的文件指纹（见 `LibraryConflict`）
        self.loaded_rev = ""
        self._load()

    def _load(self):
        if self.path.exists():
            self.loaded_rev = file_rev(self.path)
            try:
                self.data.update(json.loads(self.path.read_text(encoding="utf-8")))
            except Exception as e:  # noqa: BLE001
                # ⚠️ 2026-09-13 审计：这里原来是**静默 `pass`**，而 `_save()` 是无条件覆盖 ——
                #    于是"库文件损坏"的后果不是报警，而是**下次启动把用户的机台 / 参数注册表 /
                #    影响规则整份换成默认值**（真丢资产）。改成三步：
                #    ①损坏文件**原样改名留档**（不删、可救回）②本次运行**禁止写库**
                #    ③原因挂到 `load_error`，由 API/界面显式告知。
                self.load_error = f"{type(e).__name__}: {e}"
                self._quarantine_corrupt()
        self._migrate()

    def _migrate(self):
        """播种 + 一次性迁移链，在 `_load()` **末尾**调用（新库/老库都要走）。

        ⚠️ **只在确实改动过时才写盘**（2026-09-16 实测踩到）：末尾原来是无条件 `_save()`，
        而 `main.LIB = LibraryStore()` 用的是**真库路径** ⇒ **跑一趟 pytest 就把主人的
        library.json 写了一遍**（实测：机器版本号被测试进程改掉、文件 mtime 变化）。
        没事不改盘，改过才写。

        ⚠️ 2026-09-16 审计（P0）：这个链原来**整段落在 `_quarantine_corrupt()` 的末尾**
        （缩进事故）⇒ 只有"库文件损坏"那条路才会执行它。后果：
          · **全新安装**（新同事机器 / CI / 新服务器）拿到的是一份**空骨架** —— 没有 104 种工艺目录、
            没有机台、没有默认参数、没有影响规则，而且**静默**（界面上就是一片空）；
          · **老库**的迁移（含机台 `tool_id` 回填）**永远不会跑**。
        修法：把整段搬进 `_migrate()`，`_load()` 末尾无条件调用；`_quarantine_corrupt()` 仍会调用它
        （损坏后重建内存副本的既有行为不变）。
        """

        _before = json.dumps(self.data, ensure_ascii=False, sort_keys=True)
        if self.data.get("seed_version", 0) < 7:
            # 用 104 种工艺目录重建设备库:清空 9 类 + 删除旧分类键 + 重置失效默认
            for c in CATEGORIES:
                self.data["equipment"][c] = []
            for legacy in list(self.data["equipment"].keys()):
                if legacy not in CATEGORIES:
                    del self.data["equipment"][legacy]
            self.data["defaults"]["equipment"] = {}
            self._seed_equipment()
            self._seed_params()
            self.data["seed_version"] = 7
        if not self.data.get("film_props"):
            self.data["film_props"] = {"SiO₂": -700.0, "SiN": -400.0, "a-Si": -500.0}
        if self.data.get("rules_version", 0) < 1:
            # 一次性迁移:film_props + param_links → influence_rules
            from .rules import migrate
            self.data["influence_rules"] = (self.data.get("influence_rules")
                                            or []) + migrate(self.data.get("film_props"),
                                                             self.data.get("param_links"))
            self.data["rules_version"] = 1
        if self.data.get("bosch_version", 0) < 2:
            # 补丁 v2:Bosch 三步循环模板 + scallop 输出/参数注册/影响规则
            # (只动 DRIE (Bosch) 这一台设备与新增项,不重置设备库;幂等)
            self._patch_bosch_equipment()
            self.data["bosch_version"] = 2
        if self.data.get("surface_version", 0) < 1:
            # 补丁:表面脏污/形貌缺陷作为下游影响参数(清洗类输出脏污,刻蚀类输出缺陷)
            self._patch_surface_defects()
            self.data["surface_version"] = 1
        if self.data.get("machines_version", 0) < 1:
            # 播种机台(来自内部设备清单;型号/编号留空由实验室按实际填)
            self._seed_machines()
            self.data["machines_version"] = 1
        if self.data.get("machines_version", 0) < 2:
            # 补播种:表征设备(架构文档 §十一:CD-SEM + 椭偏仪 + 应力仪,共用)
            self._seed_metrology_machines()
            self.data["machines_version"] = 2
        if self.data.get("machines_version", 0) < 4:
            # 按**外部清单**补全型号/厂家/能力（2026-09-26 · C6：原内建表已外置）；无清单 ⇒ 无操作。
            # ⚠️ 原先这里还有一条**按真机台名强改状态**（在役与否待确认）—— 已随外置删除：
            #    状态现在由清单里的 `status` 自带（播种/补全时填入），不再在代码里点名某台机。
            self._enrich_machines()
            self.data["machines_version"] = 4

        if self.data.get("machines_version", 0) < 5:
            # 备注改用外部清单原文（早期是按文档转述,不够准）
            self._apply_equipment_list_notes()
            self.data["machines_version"] = 5

        if self.data.get("machines_version", 0) < 6:
            # 机台补 core tool_id(数据域权威机台标识,实验包/查询用它对齐) —— 值取自外部清单
            idx = self._catalog_index()
            for m in self.data.get("machines", []):
                tid = (idx.get(m.get("name")) or {}).get("tool_id")
                if tid and not m.get("tool_id"):
                    m["tool_id"] = tid
            self.data["machines_version"] = 6

        if self.data.get("machines_version", 0) < 7:
            # 补做（2026-09-16）：修复"迁移链不可达 + 顺序 bug"期间落下的库 ——
            # 它们已是 v6 但 `_enrich_machines()` 从未跑过（型号/厂家/最大片寸缺）。
            # `_enrich_machines` 只填**空**字段，绝不覆盖已填/手改值。
            self._enrich_machines()
            self.data["machines_version"] = 7
        if json.dumps(self.data, ensure_ascii=False, sort_keys=True) != _before:
            self._save()

    def _quarantine_corrupt(self) -> None:
        """把损坏的库文件改名留档，并禁止本次写盘（宁可这次不落盘，也不覆盖可能救得回的文件）。"""
        from datetime import datetime
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        dest = self.path.with_name(f"{self.path.stem}.corrupt-{stamp}{self.path.suffix}")
        try:
            self.path.replace(dest)
            self.corrupt_backup = str(dest)
        except OSError:
            self.corrupt_backup = ""      # 改名失败也不写：宁留一个读不动的文件，也不覆盖它
        self.save_blocked = True
        for key in ("equipment", "materials", "recipes", "defaults",
                    "film_props", "params"):
            self.data.setdefault(key, {})
        for key in ("param_links", "influence_rules", "machines"):
            self.data.setdefault(key, [])
        self.data.setdefault("param_categories",
                             ["尺寸", "膜厚", "材料", "质量"])
        # 常驻参数增补(幂等;老库也能补上,不动用户已有项)
        for pname, pdef in (("scallop", {"unit": "nm", "category": "尺寸"}),
                            ("侧壁粗糙度", {"unit": "nm", "category": "尺寸"}),
                            ("表面脏污", {"unit": "", "category": "质量"}),
                            ("形貌缺陷", {"unit": "", "category": "质量"}),
                            ("侧壁角_光栅", {"unit": "°", "category": "尺寸"}),
                            ("侧壁角_方块", {"unit": "°", "category": "尺寸"}),
                            ("深度均匀性", {"unit": "%", "category": "尺寸"}),
                            ("掩膜剩余", {"unit": "nm", "category": "膜厚"}),
                            ("掩膜消耗", {"unit": "nm", "category": "膜厚"})):
            self.data.setdefault("params", {}).setdefault(pname, dict(pdef))
        self._migrate()

    def _apply_equipment_list_notes(self):
        """按**外部清单**刷新机台备注（2026-09-26 · C6：原来这里的真机台备注整表已外置）。

        只对清单里**明确给了 `notes`** 的机台写入；没有清单 ⇒ 什么都不做（中性）。
        """
        idx = self._catalog_index()
        if not idx:
            return
        for m in self.data.get("machines", []):
            note = (idx.get(m.get("name")) or {}).get("notes")
            if note:
                m["notes"] = note

    def _template_ids(self) -> dict:
        """工艺模板名 → `equipment_id`（播种与升级都用它反查；模板 id 是**每库一份**的，不写进清单）。"""
        tmpl: dict = {}
        for cat in CATEGORIES:
            for eq in self.data["equipment"].get(cat, []):
                tmpl.setdefault(eq.get("name", ""), eq.get("id", ""))
        return tmpl

    def _enrich_machines(self):
        """按**外部清单**补全机台字段(只填当前为空的,不覆盖已填)。

        2026-09-26 · C6：原内建表（厂名/型号/最大片寸/备注）整表外置；无清单 ⇒ 无操作。
        顺带把 `equipment_template → equipment_id` 也补上（**老库升级路径**原先不反查，
        只有"空库播种"才反查 ⇒ 新装正常、老库那台机的模板 id 一直空着）。
        """
        idx = self._catalog_index()
        if not idx:
            return
        tmpl = self._template_ids()
        for m in self.data.get("machines", []):
            patch = idx.get(m.get("name")) or {}
            for k in ("vendor", "model", "max_sample", "status", "location", "serial"):
                v = patch.get(k)
                if v and not m.get(k):        # 只填空
                    m[k] = v
            if not m.get("equipment_id"):
                tname = patch.get("equipment_template") or ""
                m["equipment_id"] = tmpl.get(tname, "")

    def _seed_metrology_machines(self):
        """表征设备(共用,不挂工艺模板)。缺失才补,不动已有。"""
        have = {m.get("name") for m in self.data.get("machines", [])}
        for name, note in (("CD-SEM", "表征设备(共用):CD/侧壁形貌"),
                           ("椭偏仪", "表征设备(共用):膜厚 n/k"),
                           ("应力仪", "表征设备(共用):薄膜应力(曲率法)")):
            if name in have:
                continue
            self.data.setdefault("machines", []).append({
                "id": f"mc_{uuid.uuid4().hex[:8]}", "name": name, "model": "",
                "serial": "", "equipment_id": "", "location": "",
                "status": "active", "notes": note,
            })

    def _catalog_index(self) -> dict:
        """外部机台清单 → `{机台名: 档案}`；**没有清单返回空 dict**（读不动/坏 ⇒ `machine_catalog` 抛错出声）。

        ⚠️ 2026-09-26（工单 `20260915-助手线-to-兼-01` B2-残C 的 C6）：机台名/厂名/型号
        **整批外置成数据**（本机 `~/.opennano/machines.json`，或 `OPENNANO_MACHINES`）——
        代码里从此**零真机台/厂名**（公开仓库只有中性 demo `kb/machines.demo.json`）。
        """
        from kb import machine_catalog as mc
        ms = mc.load()                            # 不存在 → None；坏了 → 抛（不静默回退）
        return {m.get("name"): m for m in (ms or []) if m.get("name")}

    def _seed_machines(self):
        """播种机台：**外部清单**（本机真机台）→ **仓库中性 demo**（公开 clone / CI）。

        ⚠️ 2026-09-16 起是「有清单就用」，2026-09-26（C6）把**内建真机台表整个删掉**：
        它曾经是公开仓库里最扎眼的指纹（厂名+型号+内部备注）。现在：
          · 有本机清单（`~/.opennano/machines.json`）⇒ 用真的（与以前逐字相同，见 §五-5 前后对比）；
          · 没有 ⇒ 用仓库里的**中性 demo**（`kb/machines.demo.json`），演示与回归照样跑得通；
          · 清单**存在但坏** ⇒ 抛错出声（不静默回退，免得主人以为清单生效了）。
        """
        if self.data.get("machines"):
            return
        from kb import machine_catalog as mc
        tmpl = {}
        for cat in CATEGORIES:
            for eq in self.data["equipment"].get(cat, []):
                tmpl.setdefault(eq.get("name", ""), eq.get("id", ""))
        external = self._catalog_index()
        if not external:
            # 仓库内置中性样例（**零真机台**）—— 公开 clone / CI 用
            demo = Path(mc.__file__).resolve().parent / "machines.demo.json"
            try:
                raw = json.loads(demo.read_text(encoding="utf-8"))
                external = {m.get("name"): m for m in raw.get("machines", []) if m.get("name")}
            except (OSError, ValueError):
                external = {}
        self.data.setdefault("machines", [])
        for name, m in external.items():
            rec = {k: v for k, v in m.items()
                   if k in mc.KNOWN_FIELDS and k != "equipment_template"}
            rec.setdefault("id", f"mc_{uuid.uuid4().hex[:8]}")
            if not rec.get("equipment_id"):
                rec["equipment_id"] = tmpl.get(m.get("equipment_template", ""), "")
            self.data["machines"].append(rec)

    def _patch_bosch_equipment(self):
        """把 DRIE (Bosch) 的参数模板换成三步骤(钝化/刻蚀钝化/刻蚀硅),输出补 scallop。"""
        from .param_defs import DEFAULT_PARAM_DEFS as D
        template = {k: dict(v) for k, v in D.get("etch_drie", {}).items()}
        if not template:
            return
        for cat in CATEGORIES:
            for eq in self.data["equipment"].get(cat, []):
                if eq.get("name") != "DRIE (Bosch)":
                    continue
                eq["params"] = template
                outs = list(eq.get("outputs") or [])
                if "scallop" not in outs:
                    outs.append("scallop")
                eq["outputs"] = outs
        # 参数注册表 + 影响规则:scallop 是 Bosch 传给下游的关键量(2026-09-10 内部访谈)
        self.data.setdefault("params", {}).setdefault(
            "侧壁粗糙度", {"unit": "nm", "category": "尺寸"})
        rules = self.data.setdefault("influence_rules", [])
        if not any(r.get("from") == "scallop" for r in rules):
            rules.append({
                "id": "ir-scallop-rough", "from": "scallop", "to": "侧壁粗糙度",
                "when": "", "expr": "", "sign": "+",
                "mechanism": "Bosch 钝化/刻蚀交替形成侧壁扇贝,scallop 幅度直接决定侧壁粗糙度,"
                             "并传递影响下游保形性与器件性能",
                "scope": "global", "source": "内部访谈 2026-09-10",
                "reliability_score": 4, "enabled": True,
            })

    def _patch_surface_defects(self):
        """表面脏污/形貌缺陷 → 下游影响参数(2026-09-10 内部访谈)。

        - 清洗/去胶类设备输出「表面脏污」;刻蚀类设备输出「形貌缺陷」。
        - 影响规则(定性):脏污→缺陷密度/侧壁粗糙度;形貌缺陷→电学性能(负)。
        幂等:已存在同名输出/同 from→to 规则则不重复加。
        """
        clean_kw = ("Clean", "Strip", "RCA", "Ozone", "DI Water", "Organic", "清洗", "去胶")
        etch_kw = ("RIE", "ICP", "DRIE", "RIBE", "IBE", "FIB", "Etch", "刻蚀", "Bosch")
        for cat in CATEGORIES:
            for eq in self.data["equipment"].get(cat, []):
                name = eq.get("name", "")
                outs = list(eq.get("outputs") or [])
                if any(k in name for k in clean_kw) and "表面脏污" not in outs:
                    outs.append("表面脏污")
                if any(k in name for k in etch_kw) and "形貌缺陷" not in outs:
                    outs.append("形貌缺陷")
                eq["outputs"] = outs

        rules = self.data.setdefault("influence_rules", [])
        existing = {(r.get("from"), r.get("to")) for r in rules}
        for frm, to, sign, mech in (
            ("表面脏污", "缺陷密度", "+",
             "表面残留/聚合物/颗粒在后续刻蚀或沉积中形成微掩膜,直接抬高缺陷密度(grass/黑硅等)"),
            ("表面脏污", "侧壁粗糙度", "+",
             "残留物沿侧壁再沉积,恶化侧壁粗糙度与下游保形性"),
            ("形貌缺陷", "电学性能", "-",
             "形貌缺陷(缺口/微掩膜残留/扇贝)使器件电学性能劣化(漏电/击穿风险)"),
        ):
            if (frm, to) in existing:
                continue
            rules.append({
                "id": f"ir-{frm}-{to}", "from": frm, "to": to,
                "when": "", "expr": "", "sign": sign, "mechanism": mech,
                "scope": "global", "source": "内部访谈 2026-09-10",
                "reliability_score": 4, "enabled": True,
            })

    # ---- 播种 ----
    def _seed_params(self):
        if not self.data.get("params"):
            self.data["params"] = {k: dict(v) for k, v in PARAMS_SEED.items()}
        if not self.data.get("param_links"):
            self.data["param_links"] = [dict(l) for l in PARAM_LINKS_SEED]

    def _seed_equipment(self):
        from .param_defs import DEFAULT_PARAM_DEFS as D

        def cp(sub):
            return {k: dict(v) for k, v in D.get(sub, {}).items()}

        for cat, items in PROCESSES.items():
            existing = {e.get("name") for e in self.data["equipment"].get(cat, [])}
            for name, sub, inputs, outputs, formulas in items:
                if name in existing:
                    continue
                eq = {"id": f"eq_{uuid.uuid4().hex[:8]}", "name": name,
                      "params": cp(sub), "inputs": list(inputs),
                      "outputs": list(outputs), "formulas": dict(formulas)}
                self.data["equipment"].setdefault(cat, []).append(eq)
            lst = self.data["equipment"].get(cat, [])
            if lst and not self.data["defaults"]["equipment"].get(cat):
                self.data["defaults"]["equipment"][cat] = lst[0]["id"]

    def _save(self):
        # 库文件读过但读不动时**不写盘**：此刻 `self.data` 只有默认值 + 播种，写下去就是
        # 把用户的资产换成默认值（而损坏文件已留档，等用户处置）。带外说明见 `load_error`。
        if self.save_blocked:
            return
        # 版本守卫（2026-09-15，见 `LibraryConflict`）：盘上变了就**拒写**，绝不整份覆盖别人的改动。
        if self.loaded_rev and self.path.exists() and file_rev(self.path) != self.loaded_rev:
            raise LibraryConflict(
                "库文件在别处被改过（本进程加载后又有人保存/手改）⇒ 本次**拒绝写盘**，"
                "以免覆盖对方的改动。请先 `POST /api/library/reload` 重新载入（或确认后重做本次修改）。")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # ⚠️ **原子写**（2026-09-16 审计 P1）：原来 `write_text` 整份覆盖 —— 崩在写一半，
        #    盘上就是半截 JSON，整个共享资产库报废（rev 守卫只防"别人改过"，防不了这个）。
        from . import atomic
        atomic.write_json_atomic(self.path, self.data)
        self.loaded_rev = file_rev(self.path)

    def reload(self) -> str:
        """丢弃内存副本、重新从盘上读（版本冲突后由用户显式触发）。"""
        self.data = {
            "version": 1,
            "equipment": {}, "materials": {"resist": []}, "recipes": {},
            "defaults": {"equipment": {}, "resist": ""},
            "film_props": {}, "params": {}, "param_links": [],
            "influence_rules": [],
        }
        self.load_error = ""
        self.corrupt_backup = ""
        self.save_blocked = False
        self.loaded_rev = ""
        self._load()
        return self.loaded_rev

    # ---- 设备 ----
    def equipment_list(self, category: str) -> list[dict]:
        return list(self.data["equipment"].get(category, []))

    def get_equipment(self, eid: str) -> dict | None:
        for cat in CATEGORIES:
            for eq in self.data["equipment"].get(cat, []):
                if eq.get("id") == eid:
                    return eq
        return None

    def add_equipment(self, category, name, params, inputs=None, outputs=None) -> str:
        eq = {"id": f"eq_{uuid.uuid4().hex[:8]}", "name": name, "params": params,
              "inputs": list(inputs or []), "outputs": list(outputs or [])}
        self.data["equipment"].setdefault(category, []).append(eq)
        self._save()
        return eq["id"]

    def update_equipment(self, eid, name=None, params=None, inputs=None,
                         outputs=None, formulas=None):
        eq = self.get_equipment(eid)
        if not eq:
            return
        if name is not None:
            eq["name"] = name
        if params is not None:
            eq["params"] = params
        if inputs is not None:
            eq["inputs"] = list(inputs)
        if outputs is not None:
            eq["outputs"] = list(outputs)
        if formulas is not None:
            eq["formulas"] = dict(formulas)
        self._save()

    def remove_equipment(self, eid):
        for cat in CATEGORIES:
            self.data["equipment"][cat] = [e for e in self.data["equipment"].get(cat, [])
                                           if e.get("id") != eid]
        for k, v in list(self.data["defaults"]["equipment"].items()):
            if v == eid:
                self.data["defaults"]["equipment"][k] = ""
        self._save()

    # ---- 默认设置 ----
    def default_equipment_id(self, category): 
        return self.data["defaults"]["equipment"].get(category) or None

    def set_default_equipment(self, category, eid):
        self.data["defaults"]["equipment"][category] = eid or ""
        self._save()

    def default_params(self, category):
        eid = self.default_equipment_id(category)
        if not eid:
            return None
        eq = self.get_equipment(eid)
        return eq.get("params") if eq else None

    # ---- 材料属性 ----
    def film_props(self): 
        return dict(self.data.get("film_props", {}))

    def set_film_props(self, props):
        self.data["film_props"] = {str(k): float(v) for k, v in props.items()}
        self._save()

    def film_bias(self, film):
        return self.data.get("film_props", {}).get(film)

    # ---- 参数注册表(含作用域: global / process:<模板id> / machine:<机台id>) ----
    def params(self) -> dict:
        return dict(self.data.get("params", {}))

    def set_param(self, name, unit="", category="", scope=None):
        """增改参数。scope 省略时保留原值(默认 global)。"""
        cur = dict(self.data.setdefault("params", {}).get(name) or {})
        cur["unit"] = unit
        cur["category"] = category
        cur.setdefault("scope", "global")
        if scope is not None:
            cur["scope"] = scope or "global"
        self.data["params"][name] = cur
        self._save()

    def remove_param(self, name):
        self.data.setdefault("params", {}).pop(name, None)
        self._save()

    def param_categories(self) -> list[str]:
        """语义类别(可自定义增删)。"""
        return list(self.data.get("param_categories") or [])

    def add_param_category(self, name: str):
        name = (name or "").strip()
        if not name:
            return
        cats = self.data.setdefault("param_categories", [])
        if name not in cats:
            cats.append(name)
            self._save()

    def remove_param_category(self, name: str):
        cats = self.data.setdefault("param_categories", [])
        self.data["param_categories"] = [c for c in cats if c != name]
        self._save()

    # ---- 机台(真实设备实例:型号/编号/别名/位置/备注,挂在某个工艺模板下) ----
    def machines(self) -> list[dict]:
        return list(self.data.get("machines", []))

    def get_machine(self, mid: str) -> dict | None:
        return next((m for m in self.data.get("machines", []) if m.get("id") == mid), None)

    def add_machine(self, name: str, model: str = "", serial: str = "",
                    equipment_id: str = "", location: str = "",
                    status: str = "active", notes: str = "",
                    vendor: str = "", max_sample: str = "", tool_id: str = "") -> str:
        mid = f"mc_{uuid.uuid4().hex[:8]}"
        self.data.setdefault("machines", []).append({
            "id": mid, "name": name.strip(), "model": model, "serial": serial,
            "equipment_id": equipment_id, "location": location,
            "status": status, "notes": notes,
            "vendor": vendor, "max_sample": max_sample, "tool_id": tool_id,
        })
        self._save()
        return mid

    def update_machine(self, mid: str, **patch):
        m = self.get_machine(mid)
        if not m:
            return
        for k in ("name", "model", "serial", "equipment_id", "location", "status",
                  "notes", "vendor", "max_sample", "tool_id"):
            if k in patch and patch[k] is not None:
                m[k] = patch[k]
        self._save()

    def remove_machine(self, mid: str):
        self.data["machines"] = [m for m in self.data.get("machines", [])
                                 if m.get("id") != mid]
        self._save()

    def machine_by_model(self, model: str) -> dict | None:
        """按型号/名称/编号模糊匹配机台(用于数据入库时追溯机台)。"""
        if not model:
            return None
        s = str(model).strip().lower()
        for m in self.data.get("machines", []):
            for f in ("model", "name", "serial"):
                v = str(m.get(f) or "").strip().lower()
                if v and (v == s or v in s or s in v):
                    return m
        return None

    # ---- 参数依赖边(旧,已迁移为影响规则;接口保留兼容) ----
    def param_links(self) -> list[dict]:
        return list(self.data.get("param_links", []))

    def set_param_links(self, links):
        self.data["param_links"] = [dict(l) for l in links]
        self._save()

    # ---- 影响规则(参数/属性 → 参数,定性+定量统一) ----
    def influence_rules(self) -> list[dict]:
        return list(self.data.get("influence_rules", []))

    def set_influence_rules(self, rules):
        self.data["influence_rules"] = [dict(r) for r in rules]
        self._save()
