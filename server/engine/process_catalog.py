"""104 种工艺种类库(数据):按 10 大类组织。

- 9 个工艺大类(process kind)+ 1 个表征大类(inspect kind,归 METROLOGY)
- 每个工艺: (名称, 参数模板key, 承接参数inputs, 影响参数outputs, 公式formulas{output:expr})
- param_key 命中 param_defs.DEFAULT_PARAM_DEFS;没命中的用大类通用模板
"""
from __future__ import annotations

# 9 个工艺大类(process)
CATEGORIES = ["graphic", "etch", "deposition", "doping", "bonding",
              "packaging", "wet", "thermal", "assist"]

CATEGORY_LABELS = {
    "graphic": "图形化", "etch": "刻蚀", "deposition": "薄膜沉积",
    "doping": "掺杂", "bonding": "键合", "packaging": "封装",
    "wet": "湿法", "thermal": "热处理", "assist": "辅助",
}

# 工艺大类 -> [(name, param_key, inputs, outputs, formulas)]
# 显影/定影/去胶/划片/裂片 等多处出现的,取单一归属避免重复
PROCESSES: dict[str, list[tuple]] = {
    "graphic": [
        ("Spin Coating", "resist_spin", [], ["膜厚"], {}),
        ("Spray Coating", "graphic", [], ["膜厚"], {}),
        ("EPD Coating", "graphic", [], ["膜厚"], {}),
        ("Soft Bake", "resist_prebake", ["膜厚"], ["膜厚"], {}),
        ("UV Exposure", "exposure", ["膜厚"], ["胶CD", "胶SWA"], {}),
        ("E-beam Litho", "exposure_ebl", [], ["胶CD", "胶SWA"], {}),
        ("Laser Direct Write", "exposure_ldw", [], ["胶CD", "胶SWA"], {}),
        ("X-ray Litho", "exposure", [], ["胶CD", "胶SWA"], {}),
        ("Nanoimprint", "graphic_nil", ["膜厚"], ["胶CD", "胶SWA"], {}),
        ("Micro-contact Print", "graphic", [], ["胶CD"], {}),
        ("PEB", "resist_peb", ["胶CD"], ["胶CD"], {}),
        ("Develop", "resist_develop", ["胶CD"], ["胶CD"], {}),
        ("Hard Bake", "resist_hardbake", ["胶CD"], ["胶CD"], {}),
        ("Gray-scale Litho", "graphic_gray", [], ["胶CD", "胶SWA"], {}),
        ("Double-side Align", "graphic_dalign", ["套刻精度"], ["套刻精度", "胶CD"], {}),
        ("Overlay Exposure", "graphic_dalign", ["套刻精度"], ["套刻精度", "胶CD"], {}),
        ("GDS Layout", "graphic_gds", [], ["胶CD"], {}),
    ],
    "etch": [
        ("RIE", "etch_rie", ["胶CD", "胶SWA", "膜厚"], ["硅CD", "侧壁角", "刻蚀深度", "形貌缺陷"], {}),
        ("ICP Etch", "etch", ["胶CD", "胶SWA", "膜厚"], ["硅CD", "侧壁角", "刻蚀深度", "形貌缺陷"],
         {"硅CD": "胶CD - 2 * bias_nm"}),
        ("DRIE (Bosch)", "etch_drie", ["胶CD", "胶SWA", "膜厚"], ["硅CD", "侧壁角", "刻蚀深度", "scallop", "形貌缺陷"], {}),
        ("RIBE", "etch_ribe", ["胶CD", "膜厚"], ["硅CD", "侧壁角", "刻蚀深度", "形貌缺陷"], {}),
        ("IBE", "etch_ibe", ["胶CD", "膜厚"], ["硅CD", "刻蚀深度", "形貌缺陷"], {}),
        ("FIB Etch", "etch_fib", ["胶CD"], ["硅CD", "侧壁角", "形貌缺陷"], {}),
        ("Wet Etch", "etch_wet", ["胶CD", "膜厚"], ["硅CD", "刻蚀深度", "形貌缺陷"], {}),
        ("Electrochemical Etch", "wet", ["膜厚"], ["刻蚀深度"], {}),
        ("Plasma Strip", "resist_ash", ["胶CD"], ["缺陷密度", "表面脏污"], {}),
        ("Vapor Etch (XeF₂)", "etch", ["膜厚"], ["刻蚀深度"], {}),
        ("Laser Ablation", "etch", ["胶CD"], ["刻蚀深度"], {}),
        ("CMP", "etch", ["膜厚", "粗糙度"], ["粗糙度", "均匀性"], {}),
    ],
    "deposition": [
        ("PECVD", "coating", [], ["膜厚", "应力", "粗糙度"], {}),
        ("LPCVD", "dep_lpcvd", [], ["膜厚", "应力"], {}),
        ("APCVD", "deposition", [], ["膜厚"], {}),
        ("MOCVD", "deposition", [], ["膜厚", "结晶度"], {}),
        ("ALD", "coating_ald", [], ["膜厚", "均匀性"], {}),
        ("E-beam Evap", "coating_ebd", [], ["膜厚", "应力"], {}),
        ("Thermal Evap", "dep_thermalevap", [], ["膜厚", "应力"], {}),
        ("Magnetron Sputter", "coating_sputter", [], ["膜厚", "应力"], {}),
        ("Ion-beam Sputter", "dep_ibs", [], ["膜厚", "应力"], {}),
        ("PLD", "deposition", [], ["膜厚", "结晶度"], {}),
        ("MBE", "dep_mbe", [], ["膜厚", "结晶度"], {}),
        ("Electroplating", "wet_plating", [], ["膜厚"], {}),
        ("Electroless Plating", "wet", [], ["膜厚"], {}),
        ("Sol-Gel", "wet", [], ["膜厚"], {}),
        ("SOG", "wet", [], ["膜厚", "应力"], {}),
    ],
    "doping": [
        ("Ion Implant", "doping_implant", [], ["掺杂浓度"], {}),
        ("Thermal Diffusion", "doping_diff", [], ["掺杂浓度"], {}),
        ("RTA", "doping_rta", ["掺杂浓度"], ["掺杂浓度", "结晶度"], {}),
        ("Laser Annealing", "doping_rta", ["掺杂浓度"], ["掺杂浓度"], {}),
        ("SPE", "doping", [], ["结晶度"], {}),
        ("Plasma Doping", "doping", [], ["掺杂浓度"], {}),
    ],
    "bonding": [
        ("Anodic Bonding", "bonding", [], ["应力"], {}),
        ("Eutectic Bonding", "bonding", [], ["应力"], {}),
        ("Thermocompression", "bonding", [], ["应力"], {}),
        ("Fusion Bonding", "bonding", [], ["应力"], {}),
        ("Polymer Bonding", "bonding", [], ["应力"], {}),
        ("Glass Frit Bonding", "bonding", [], ["应力"], {}),
        ("Hybrid Bonding", "bonding", [], ["应力"], {}),
    ],
    "packaging": [
        ("Dicing", "assist_dicing", [], [], {}),
        ("Cleaving", "assist_cleave", [], [], {}),
        ("Wire Bonding", "assist_wirebond", [], [], {}),
        ("Flip Chip", "packaging", [], [], {}),
        ("WLP", "packaging", [], [], {}),
        ("Fan-out", "packaging", [], [], {}),
        ("TSV", "packaging", [], [], {}),
        ("Hermetic Sealing", "packaging", [], [], {}),
        ("Molding", "packaging", [], [], {}),
    ],
    "wet": [
        ("RCA Clean", "wet_rca", [], ["缺陷密度", "表面脏污"], {}),
        ("Organic Clean", "wet", [], ["缺陷密度", "表面脏污"], {}),
        ("Wet Etch", "etch_wet", ["胶CD", "膜厚"], ["硅CD", "刻蚀深度"], {}),
        ("Lift-off", "wet_liftoff", ["胶CD"], ["硅CD"], {}),
        ("Electroplating", "wet_plating", [], ["膜厚"], {}),
        ("Electroless Plating", "wet", [], ["膜厚"], {}),
        ("Fix", "resist_fix", ["胶CD"], ["胶CD"], {}),
        ("DI Water Clean", "wet", [], ["缺陷密度", "表面脏污"], {}),
        ("Strip", "strip_solvent", ["胶CD"], ["缺陷密度", "表面脏污"], {}),
    ],
    "thermal": [
        ("Oxidation", "thermal", [], ["膜厚", "应力"], {}),
        ("Annealing", "thermal", ["应力"], ["应力", "结晶度"], {}),
        ("RTA", "doping_rta", ["掺杂浓度"], ["掺杂浓度", "结晶度"], {}),
        ("Alloying", "thermal", [], ["电学性能"], {}),
        ("Sintering", "thermal", [], ["结晶度"], {}),
        ("Forming Gas Anneal", "thermal", [], ["电学性能"], {}),
    ],
    "assist": [
        ("Spin Coating", "resist_spin", [], ["膜厚"], {}),
        ("Baking", "thermal", [], ["膜厚"], {}),
        ("Plasma Clean", "resist_ash", [], ["缺陷密度", "表面脏污"], {}),
        ("UV Ozone Clean", "assist", [], ["缺陷密度", "表面脏污"], {}),
        ("Hydrophilic Treat", "assist", [], [], {}),
        ("Hydrophobic Treat", "assist", [], [], {}),
        ("Dicing", "assist_dicing", [], [], {}),
        ("Cleaving", "assist_cleave", [], [], {}),
    ],
}

# 表征大类(inspect kind): (subtype, name, desc)
METROLOGY = [
    ("sem", "扫描电镜（SEM）", "形貌/CD"),
    ("tem", "透射电镜（TEM）", "截面/晶格"),
    ("afm", "原子力显微镜（AFM）", "粗糙度/形貌"),
    ("xrd", "X射线衍射（XRD）", "晶体结构/应力"),
    ("xps", "X射线光电子能谱（XPS）", "表面成分"),
    ("aes", "俄歇能谱（AES）", "表面成分"),
    ("sims", "二次离子质谱（SIMS）", "成分深度剖析"),
    ("ellip", "椭偏仪", "膜厚/光学常数"),
    ("profilo", "台阶仪", "台阶/膜厚"),
    ("stress", "应力仪", "薄膜应力(曲率法)"),
    ("fourpp", "四探针", "方块电阻"),
    ("hall", "霍尔测试（Hall）", "载流子浓度/迁移率"),
    ("cv", "电容-电压（C-V）", "掺杂浓度分布"),
    ("om", "光学显微镜", "形貌/缺陷"),
    ("fluor", "荧光检测", "缺陷检测"),
    ("ir", "红外热成像", "热分布"),
]

# 表征手段 → 它测出并传给下游的接口参数(让表征节点也参与流程传递)
METROLOGY_OUTPUTS: dict[str, list[str]] = {
    "sem":     ["硅CD", "侧壁角", "形貌缺陷"],
    "tem":     ["膜厚"],
    "afm":     ["粗糙度", "形貌缺陷"],
    "xrd":     ["应力", "结晶度"],
    "xps":     ["表面脏污"],
    "aes":     ["表面脏污"],
    "sims":    ["掺杂浓度"],
    "ellip":   ["膜厚"],
    "profilo": ["膜厚", "刻蚀深度"],
    "stress":  ["应力"],
    "fourpp":  ["电学性能"],
    "hall":    ["电学性能"],
    "cv":      ["掺杂浓度"],
    "om":      ["形貌缺陷"],
    "fluor":   ["缺陷密度"],
    "ir":      [],
}


# ---- 工艺族(用于画布节点配色/区分) ----
FAMILY_LABELS = {
    "resist": "Resist", "expose": "Exposure", "etch": "Etch", "dep": "Deposition",
    "dope": "Doping", "bond": "Bonding", "pack": "Packaging", "wet": "Wet",
    "thermal": "Thermal", "assist": "Assist", "metro": "Metrology",
    # 表征四族(按测量性质分色,同工艺族机制)
    "metro_form": "形貌表征", "metro_comp": "成分分析",
    "metro_opt": "光学物性", "metro_elec": "电学测试",
}

# 表征 subtype → 族
METRO_FAMILY = {
    "sem": "metro_form", "tem": "metro_form", "afm": "metro_form",
    "om": "metro_form", "profilo": "metro_form",
    "xps": "metro_comp", "aes": "metro_comp", "sims": "metro_comp",
    "xrd": "metro_comp",
    "ellip": "metro_opt", "fluor": "metro_opt", "stress": "metro_opt",
    "ir": "metro_opt",
    "fourpp": "metro_elec", "hall": "metro_elec", "cv": "metro_elec",
}

# graphic 大类内部细分:操作光刻胶 vs 曝光
_RESIST_NAMES = {"Spin Coating", "Spray Coating", "EPD Coating", "Soft Bake",
                 "PEB", "Develop", "Hard Bake"}
_EXPOSE_NAMES = {"UV Exposure", "E-beam Litho", "Laser Direct Write", "X-ray Litho",
                 "Nanoimprint", "Micro-contact Print", "Gray-scale Litho",
                 "Double-side Align", "Overlay Exposure", "GDS Layout"}

_CAT_TO_FAMILY = {"etch": "etch", "deposition": "dep", "doping": "dope",
                  "bonding": "bond", "packaging": "pack", "wet": "wet",
                  "thermal": "thermal", "assist": "assist"}


def family_for(name: str, category: str) -> str:
    """工艺 → 族(resist/expose/etch/dep/...)。"""
    if category == "graphic":
        if name in _RESIST_NAMES:
            return "resist"
        return "expose"
    if category == "metrology":
        return "metro"
    return _CAT_TO_FAMILY.get(category, category)


def family_label(family: str) -> str:
    return FAMILY_LABELS.get(family, family)
