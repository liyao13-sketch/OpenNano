"""结果字段注册表:标准键 → 中文名 + 单位。

知识条目的 results 是自由字典(键=字段,值=数值);本表给标准键配中文名与单位,
供 UI 展示与优化目标下拉使用。未知列(用户表里的自定义指标)保持原表头,无单位。
"""
from __future__ import annotations

RESULT_FIELDS: dict[str, dict] = {
    # 速率类
    "er_nm_min":        {"label": "刻蚀速率", "unit": "nm/min"},
    "selectivity":      {"label": "选择比", "unit": ""},
    # 形貌类
    "sidewall_angle_deg": {"label": "侧壁角(光栅)", "unit": "°"},
    "swa_square_deg":   {"label": "侧壁角(方块)", "unit": "°"},
    "scallop":          {"label": "侧壁扇贝", "unit": "nm"},
    "roughness_nm":     {"label": "粗糙度", "unit": "nm"},
    "ler_nm":           {"label": "线边缘粗糙度 LER", "unit": "nm"},
    "lwr_nm":           {"label": "线宽粗糙度 LWR", "unit": "nm"},
    # 尺寸类
    "depth_nm":         {"label": "刻蚀深度 Δd", "unit": "nm"},
    "depth_target_nm":  {"label": "刻蚀深度(材料)", "unit": "nm"},
    "depth_center_nm":  {"label": "刻蚀深度(中心)", "unit": "nm"},
    "depth_top_nm":     {"label": "刻蚀深度(上)", "unit": "nm"},
    "depth_bottom_nm":  {"label": "刻蚀深度(下)", "unit": "nm"},
    "depth_left_nm":    {"label": "刻蚀深度(左)", "unit": "nm"},
    "depth_right_nm":   {"label": "刻蚀深度(右)", "unit": "nm"},
    "final_cd_nm":      {"label": "刻蚀后 CD", "unit": "nm"},
    "cd_top_nm":        {"label": "CD(顶)", "unit": "nm"},
    "cd_bot_nm":        {"label": "CD(底)", "unit": "nm"},
    "cd_loss_nm":       {"label": "CD 损失", "unit": "nm"},
    # 掩膜类(口径:掩膜消耗统一用 mask_loss / mask_remain 表述)
    "mask_loss_nm":     {"label": "掩膜消耗 Δd(掩膜)", "unit": "nm"},
    "mask_remain_nm":   {"label": "掩膜剩余 resist remain", "unit": "nm"},
    "resist_residue":   {"label": "残胶(分级)", "unit": ""},
    # 均匀性/其它
    "uniformity_pct":   {"label": "均匀性", "unit": "%"},
    "n2_pct":           {"label": "N₂ 占比", "unit": "%"},
}


def field_meta(key: str) -> dict:
    """标准键 → {label, unit};未知键回退为键名自身、无单位。"""
    m = RESULT_FIELDS.get(key)
    if m:
        return dict(m, key=key, known=True)
    return {"key": key, "label": key, "unit": "", "known": False}
