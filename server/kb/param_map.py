"""接口参数 ↔ 结果字段对照表(2026-09-10 实验室定)。

两层命名:
- 接口参数: 画布上"承接/影响"的槽位(中文名,如 硅CD/刻蚀深度/scallop)
- 结果字段: 知识条目 results 里的标准键(英文,如 final_cd_nm/depth_center_nm)

本表把两者显式绑定,用于:
1. Opt 目标下拉里标注该字段对应画布上的哪个参数;
2. 将来实测结果回填到画布节点的"影响→"槽位;
3. Agent 解析口语("刻蚀深度怎么样")时知道该查哪个字段。
"""
from __future__ import annotations

# 接口参数 → 结果字段(主值在前;一个接口参数可能由多列派生,见 DERIVED)
PARAM_TO_FIELDS: dict[str, list[str]] = {
    "硅CD":        ["final_cd_nm"],
    "刻蚀深度":     ["depth_center_nm"],
    "深度均匀性":    ["depth_top_nm", "depth_bottom_nm", "depth_left_nm", "depth_right_nm"],
    "侧壁角_光栅":   ["sidewall_angle_deg"],
    "侧壁角_方块":   ["swa_square_deg"],
    "侧壁角":       ["sidewall_angle_deg"],          # 旧名(兼容已在用的工程)
    "选择比":       ["selectivity"],
    "粗糙度":       ["roughness_nm"],
    "侧壁粗糙度":    ["roughness_nm"],
    "scallop":     ["scallop"],
    "均匀性":       ["uniformity_pct"],
    "LWR":         ["lwr_nm"],
    "掩膜剩余":      ["mask_remain_nm"],
    "掩膜消耗":      ["mask_loss_nm"],
    "膜厚":         ["depth_nm"],
    "胶CD":        [],        # 曝光/显影结果,待曝光数据入库后补
    "胶SWA":       [],
}

# 结果字段 → 接口参数(反向;多对一时取第一个)
FIELD_TO_PARAM: dict[str, str] = {}
for _p, _fs in PARAM_TO_FIELDS.items():
    for _f in _fs:
        FIELD_TO_PARAM.setdefault(_f, _p)

# 组合接口参数:由多个结果字段派生
DERIVED_PARAMS = {"深度均匀性"}


def param_for_field(field: str) -> str | None:
    """结果字段 → 画布接口参数。"""
    return FIELD_TO_PARAM.get(field)


def fields_for_param(param: str) -> list[str]:
    return list(PARAM_TO_FIELDS.get(param, []))


def derive_param_value(param: str, results: dict) -> float | None:
    """从一条 results 派生组合接口参数值(目前支持 深度均匀性)。"""
    if param == "深度均匀性":
        vals = [results[f] for f in PARAM_TO_FIELDS["深度均匀性"] if isinstance(results.get(f), (int, float))]
        if len(vals) < 2:
            return None
        mean = sum(vals) / len(vals)
        if not mean:
            return None
        return round((max(vals) - min(vals)) / mean * 100, 3)
    fields = PARAM_TO_FIELDS.get(param) or []
    for f in fields:
        v = results.get(f)
        if isinstance(v, (int, float)):
            return float(v)
    return None


def interface_values(results: dict) -> dict:
    """一条 results → 可回填到画布节点的接口参数值 {接口参数: 数值}。"""
    out: dict[str, float] = {}
    for param in PARAM_TO_FIELDS:
        v = derive_param_value(param, results)
        if v is not None:
            out[param] = v
    return out
