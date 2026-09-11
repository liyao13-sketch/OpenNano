"""工艺参数默认定义(可被用户在界面上自定义)。

每个子类型的默认参数;用户可在此基础上增删改,定义存到 Module.param_defs。
参数定义格式: key -> {label, unit, default, min, max}
"""
from __future__ import annotations

import copy

# 各工艺子类型的默认参数定义
DEFAULT_PARAM_DEFS: dict[str, dict[str, dict]] = {
    "bonding": {
        "temp_c": {"label":"Temp","unit":"°C","default":400,"min":0,"max":1200},
        "force":  {"label":"Force","unit":"N","default":500,"min":0,"max":10000},
        "time_s": {"label":"Time","unit":"s","default":600,"min":0,"max":7200},
        "voltage": {"label":"Voltage","unit":"V","default":800,"min":0,"max":2000},
    },
    "packaging": {
        "temp_c": {"label":"Temp","unit":"°C","default":200,"min":0,"max":500},
        "pressure": {"label":"Pressure","unit":"MPa","default":10,"min":0,"max":200},
        "time_s": {"label":"Time","unit":"s","default":300,"min":0,"max":7200},
    },
    "thermal": {
        "temp_c": {"label":"Temp","unit":"°C","default":1000,"min":0,"max":1200},
        "time_s": {"label":"Time","unit":"s","default":3600,"min":0,"max":28800},
        "ramp": {"label":"Ramp Rate","unit":"°C/s","default":10,"min":0,"max":500},
    },
    "graphic_gds": {
        "pitch":      {"label": "Pitch", "unit": "nm", "default": 1000, "min": 0, "max": 100000},
        "duty_cycle": {"label": "Duty Cycle", "unit": "%", "default": 50, "min": 0, "max": 100},
        "size_nm":    {"label": "Feature Size", "unit": "nm", "default": 500, "min": 0, "max": 100000},
        "bias_nm":    {"label": "GDS Bias", "unit": "nm", "default": 0, "min": -10000, "max": 10000},
    },
    "etch_sim": {
        "mask_bias": {"label": "Mask Bias", "unit": "nm", "default": 0, "min": -10000, "max": 10000},
        "profile_pts": {"label": "Profile Points", "unit": "", "default": 200, "min": 4, "max": 10000},
    },
    # ---- 通用大类兜底(面板"内置默认") ----
    "graphic":    {"dose": {"label":"Dose","unit":"mJ/cm²","default":100,"min":0,"max":5000},
                   "time": {"label":"Time","unit":"s","default":60,"min":0,"max":3600}},
    "deposition": {"temp_c": {"label":"Temp","unit":"°C","default":300,"min":0,"max":800},
                   "pressure": {"label":"Pressure","unit":"Torr","default":0.5,"min":0,"max":10},
                   "time": {"label":"Time","unit":"s","default":300,"min":0,"max":7200}},
    "wet":        {"temp_c": {"label":"Temp","unit":"°C","default":60,"min":0,"max":200},
                   "time_s": {"label":"Time","unit":"s","default":300,"min":0,"max":7200}},
    "doping":     {"dose": {"label":"Dose","unit":"ions/cm²","default":1e13,"min":0,"max":1e16},
                   "energy": {"label":"Energy","unit":"keV","default":50,"min":0,"max":1000},
                   "temp_c": {"label":"Temp","unit":"°C","default":900,"min":0,"max":1200}},
    "assist":     {"temp_c": {"label":"Temp","unit":"°C","default":200,"min":0,"max":1200},
                   "time_s": {"label":"Time","unit":"s","default":300,"min":0,"max":7200}},
    # ---- 刻蚀扩展 ----
    "etch_ibe": {
        "beam_voltage": {"label":"Beam Voltage","unit":"V","default":500,"min":0,"max":2000},
        "beam_current": {"label":"Beam Current","unit":"mA","default":20,"min":0,"max":200},
        "pressure": {"label":"Pressure","unit":"mTorr","default":0.5,"min":0,"max":10},
        "time": {"label":"Etch Time","unit":"s","default":300,"min":0,"max":7200},
        "gas_Ar": {"label":"Ar Flow","unit":"sccm","default":5,"min":0,"max":50},
    },
    "etch_wet": {
        "temp_c": {"label":"Etch Temp","unit":"°C","default":25,"min":0,"max":150},
        "time_s": {"label":"Etch Time","unit":"s","default":120,"min":0,"max":3600},
    },
    "etch_fib": {
        "beam_voltage": {"label":"Beam Voltage","unit":"kV","default":30,"min":0,"max":100},
        "beam_current": {"label":"Beam Current","unit":"pA","default":50,"min":0,"max":10000},
        "dose": {"label":"Dose","unit":"ions/cm²","default":1e16,"min":0,"max":1e18},
        "time": {"label":"Time","unit":"s","default":60,"min":0,"max":3600},
    },
    # ---- 图形化扩展 ----
    "graphic_gray": {
        "dose": {"label":"Dose","unit":"mJ/cm²","default":100,"min":0,"max":5000},
        "dose_step": {"label":"Dose Step","unit":"mJ/cm²","default":10,"min":0,"max":500},
        "pitch": {"label":"Pitch","unit":"nm","default":1000,"min":0,"max":100000},
    },
    "graphic_nil": {
        "temp_c": {"label":"Imprint Temp","unit":"°C","default":150,"min":0,"max":300},
        "pressure": {"label":"Pressure","unit":"bar","default":20,"min":0,"max":100},
        "time_s": {"label":"Imprint Time","unit":"s","default":120,"min":0,"max":3600},
        "depth_nm": {"label":"Imprint Depth","unit":"nm","default":200,"min":0,"max":2000},
    },
    "graphic_dalign": {
        "align_dx": {"label":"Align Offset X","unit":"nm","default":0,"min":-10000,"max":10000},
        "align_dy": {"label":"Align Offset Y","unit":"nm","default":0,"min":-10000,"max":10000},
        "dose": {"label":"Dose","unit":"mJ/cm²","default":100,"min":0,"max":5000},
    },
    # ---- 沉积扩展 ----
    "dep_lpcvd": {
        "temp_c": {"label":"Temp","unit":"°C","default":800,"min":300,"max":1000},
        "pressure": {"label":"Pressure","unit":"mTorr","default":200,"min":0,"max":1000},
        "gas_SiH4": {"label":"SiH₄ Flow","unit":"sccm","default":50,"min":0,"max":500},
        "gas_N2O": {"label":"N₂O Flow","unit":"sccm","default":100,"min":0,"max":500},
        "time": {"label":"Time","unit":"s","default":1800,"min":0,"max":14400},
    },
    "dep_thermalevap": {
        "source_temp": {"label":"Source Temp","unit":"°C","default":1500,"min":0,"max":3000},
        "pressure": {"label":"Pressure","unit":"Torr","default":1e-6,"min":0,"max":1e-3},
        "rate": {"label":"Dep Rate","unit":"nm/s","default":1,"min":0,"max":50},
        "thickness": {"label":"Target Thk","unit":"nm","default":100,"min":0,"max":10000},
    },
    "dep_ibs": {
        "beam_voltage": {"label":"Beam Voltage","unit":"V","default":1000,"min":0,"max":2000},
        "beam_current": {"label":"Beam Current","unit":"mA","default":20,"min":0,"max":200},
        "pressure": {"label":"Pressure","unit":"mTorr","default":0.5,"min":0,"max":10},
        "time": {"label":"Time","unit":"s","default":600,"min":0,"max":7200},
        "gas_Ar": {"label":"Ar Flow","unit":"sccm","default":10,"min":0,"max":100},
    },
    "dep_mbe": {
        "sub_temp": {"label":"Substrate Temp","unit":"°C","default":600,"min":0,"max":1200},
        "pressure": {"label":"Pressure","unit":"Torr","default":1e-9,"min":0,"max":1e-5},
        "rate": {"label":"Growth Rate","unit":"nm/s","default":0.1,"min":0,"max":10},
        "thickness": {"label":"Target Thk","unit":"nm","default":100,"min":0,"max":10000},
    },
    # ---- 湿法扩展 ----
    "wet_rca": {
        "temp_c": {"label":"Bath Temp","unit":"°C","default":80,"min":0,"max":200},
        "time_s": {"label":"Clean Time","unit":"s","default":600,"min":0,"max":3600},
    },
    "wet_liftoff": {
        "temp_c": {"label":"Solvent Temp","unit":"°C","default":60,"min":0,"max":150},
        "time_s": {"label":"Soak Time","unit":"s","default":600,"min":0,"max":7200},
    },
    "wet_plating": {
        "current_density": {"label":"Current Density","unit":"mA/cm²","default":10,"min":0,"max":1000},
        "time_s": {"label":"Plate Time","unit":"s","default":600,"min":0,"max":7200},
        "temp_c": {"label":"Bath Temp","unit":"°C","default":25,"min":0,"max":100},
        "thickness": {"label":"Target Thk","unit":"nm","default":1000,"min":0,"max":100000},
    },
    # ---- 掺杂 ----
    "doping_implant": {
        "dose": {"label":"Dose","unit":"ions/cm²","default":1e13,"min":0,"max":1e16},
        "energy": {"label":"Energy","unit":"keV","default":50,"min":0,"max":1000},
        "tilt": {"label":"Tilt Angle","unit":"°","default":7,"min":0,"max":45},
    },
    "doping_diff": {
        "temp_c": {"label":"Temp","unit":"°C","default":1000,"min":500,"max":1200},
        "time_s": {"label":"Time","unit":"s","default":3600,"min":0,"max":28800},
        "gas_flow": {"label":"Gas Flow","unit":"sccm","default":100,"min":0,"max":500},
    },
    "doping_rta": {
        "temp_c": {"label":"Temp","unit":"°C","default":1000,"min":200,"max":1200},
        "time_s": {"label":"Time","unit":"s","default":30,"min":0,"max":600},
        "ramp": {"label":"Ramp Rate","unit":"°C/s","default":50,"min":0,"max":500},
    },
    # ---- 辅助 ----
    "assist_dicing": {
        "blade_speed": {"label":"Blade Speed","unit":"rpm","default":30000,"min":0,"max":60000},
        "feed_rate": {"label":"Feed Rate","unit":"mm/s","default":5,"min":0,"max":50},
        "depth": {"label":"Cut Depth","unit":"µm","default":300,"min":0,"max":2000},
    },
    "assist_cleave": {
        "score_depth": {"label":"Score Depth","unit":"µm","default":10,"min":0,"max":500},
    },
    "assist_wirebond": {
        "temp_c": {"label":"Stage Temp","unit":"°C","default":150,"min":0,"max":400},
        "force": {"label":"Bond Force","unit":"g","default":20,"min":0,"max":200},
        "power": {"label":"Ultrasonic Power","unit":"W","default":1,"min":0,"max":10},
        "time_s": {"label":"Bond Time","unit":"s","default":0.05,"min":0,"max":5},
    },
    "assist_anneal": {
        "temp_c": {"label":"Temp","unit":"°C","default":400,"min":0,"max":1200},
        "time_s": {"label":"Time","unit":"s","default":3600,"min":0,"max":28800},
        "ramp": {"label":"Ramp Rate","unit":"°C/s","default":10,"min":0,"max":500},
    },
    "resist": {
        "temp_c": {"label": "Temperature", "unit": "°C", "default": 100, "min": 0, "max": 300},
        "time_s": {"label": "Time", "unit": "s", "default": 60, "min": 0, "max": 3600},
    },
    "resist_spin": {
        "rpm":           {"label": "Spin Speed", "unit": "rpm", "default": 3000, "min": 0, "max": 10000},
        "spin_time_s":   {"label": "Spin Time", "unit": "s", "default": 30, "min": 0, "max": 300},
        "target_thk_nm": {"label": "Target Thk", "unit": "nm", "default": 500, "min": 0, "max": 5000},
        "bilayer":       {"label": "Bilayer (0/1)", "unit": "", "default": 0, "min": 0, "max": 1},
    },
    "resist_prebake": {
        "temp_c": {"label": "Bake Temp", "unit": "°C", "default": 95, "min": 0, "max": 300},
        "time_s": {"label": "Bake Time", "unit": "s", "default": 60, "min": 0, "max": 3600},
    },
    "resist_peb": {
        "temp_c": {"label": "PEB Temp", "unit": "°C", "default": 110, "min": 0, "max": 300},
        "time_s": {"label": "PEB Time", "unit": "s", "default": 60, "min": 0, "max": 3600},
    },
    "resist_develop": {
        "time_s": {"label": "Develop Time", "unit": "s", "default": 60, "min": 0, "max": 600},
        "temp_c": {"label": "Develop Temp", "unit": "°C", "default": 23, "min": 0, "max": 100},
    },
    "resist_fix": {
        "time_s": {"label": "Fix Time", "unit": "s", "default": 30, "min": 0, "max": 600},
        "temp_c": {"label": "Fix Temp", "unit": "°C", "default": 23, "min": 0, "max": 100},
    },
    "resist_hardbake": {
        "temp_c": {"label": "Hard-bake Temp", "unit": "°C", "default": 120, "min": 0, "max": 300},
        "time_s": {"label": "Hard-bake Time", "unit": "s", "default": 300, "min": 0, "max": 7200},
    },
    "resist_ash": {
        "power_w":       {"label": "Power", "unit": "W", "default": 300, "min": 0, "max": 3000},
        "time_s":        {"label": "Ash Time", "unit": "s", "default": 60, "min": 0, "max": 3600},
        "gas_O2_sccm":   {"label": "O₂ Flow", "unit": "sccm", "default": 50, "min": 0, "max": 500},
        "pressure_mt":   {"label": "Pressure", "unit": "mTorr", "default": 300, "min": 0, "max": 2000},
    },
    "strip": {
        "temp_c": {"label": "Temperature", "unit": "°C", "default": 80, "min": 0, "max": 200},
        "time_s": {"label": "Time", "unit": "s", "default": 300, "min": 0, "max": 7200},
    },
    "strip_solvent": {
        "temp_c": {"label": "Solvent Temp", "unit": "°C", "default": 60, "min": 0, "max": 150},
        "time_s": {"label": "Strip Time", "unit": "s", "default": 600, "min": 0, "max": 7200},
    },
    "strip_plasma": {
        "power_w":     {"label": "Power", "unit": "W", "default": 300, "min": 0, "max": 3000},
        "time_s":      {"label": "Strip Time", "unit": "s", "default": 120, "min": 0, "max": 3600},
        "gas_O2_sccm": {"label": "O₂ Flow", "unit": "sccm", "default": 50, "min": 0, "max": 500},
        "pressure_mt": {"label": "Pressure", "unit": "mTorr", "default": 300, "min": 0, "max": 2000},
    },
    # Bosch 三步骤循环(2026-09-10 内部访谈):钝化 → 刻蚀钝化 → 刻蚀硅,每步独立参数
    "etch_drie": {
        # --- 全局 ---
        "cycles":            {"label": "循环次数", "unit": "", "default": 100, "min": 1, "max": 10000},
        "temp":              {"label": "温度", "unit": "°C", "default": 10, "min": -10, "max": 200},
        # --- 步骤1 钝化(C₄F₈ 侧壁保护) ---
        "pass_gas_C4F8":     {"label": "钝化-C₄F₈流量", "unit": "sccm", "default": 80, "min": 0, "max": 300},
        "pass_gas_O2":       {"label": "钝化-O₂流量", "unit": "sccm", "default": 5, "min": 0, "max": 100},
        "pass_source_power": {"label": "钝化-Source功率", "unit": "W", "default": 1200, "min": 0, "max": 3000},
        "pass_bias_power":   {"label": "钝化-Bias功率", "unit": "W", "default": 0, "min": 0, "max": 500},
        "pass_pressure":     {"label": "钝化-压强", "unit": "mTorr", "default": 20, "min": 0, "max": 200},
        "pass_time_s":       {"label": "钝化-时间", "unit": "s", "default": 3, "min": 0, "max": 60},
        # --- 步骤2 刻蚀钝化(打掉底部钝化层,决定各向异性) ---
        "brk_gas_SF6":       {"label": "刻蚀钝化-SF₆流量", "unit": "sccm", "default": 50, "min": 0, "max": 500},
        "brk_source_power":  {"label": "刻蚀钝化-Source功率", "unit": "W", "default": 800, "min": 0, "max": 3000},
        "brk_bias_power":    {"label": "刻蚀钝化-Bias功率", "unit": "W", "default": 10, "min": 0, "max": 500},
        "brk_pressure":      {"label": "刻蚀钝化-压强", "unit": "mTorr", "default": 20, "min": 0, "max": 200},
        "brk_time_s":        {"label": "刻蚀钝化-时间", "unit": "s", "default": 1.5, "min": 0, "max": 60},
        # --- 步骤3 刻蚀硅(主刻蚀) ---
        "etch_gas_SF6":      {"label": "刻蚀硅-SF₆流量", "unit": "sccm", "default": 120, "min": 0, "max": 500},
        "etch_source_power": {"label": "刻蚀硅-Source功率", "unit": "W", "default": 1200, "min": 0, "max": 3000},
        "etch_bias_power":   {"label": "刻蚀硅-Bias功率", "unit": "W", "default": 40, "min": 0, "max": 500},
        "etch_pressure":     {"label": "刻蚀硅-压强", "unit": "mTorr", "default": 30, "min": 0, "max": 200},
        "etch_time_s":       {"label": "刻蚀硅-时间", "unit": "s", "default": 7, "min": 0, "max": 60},
    },
    "exposure_ebl": {
        "beam_voltage":      {"label": "Beam Voltage", "unit": "kV", "default": 100, "min": 0, "max": 200},
        "beam_current":      {"label": "Beam Current", "unit": "nA", "default": 2, "min": 0, "max": 100},
        "dose":              {"label": "Dose", "unit": "µC/cm²", "default": 500, "min": 0, "max": 100000},
        "write_field":       {"label": "Write Field", "unit": "µm", "default": 500, "min": 0, "max": 5000},
        "step_size":         {"label": "Step Size", "unit": "nm", "default": 20, "min": 0, "max": 200},
        "resist_thickness":  {"label": "Resist Thk", "unit": "nm", "default": 300, "min": 0, "max": 5000},
        "pitch":             {"label": "Pitch", "unit": "nm", "default": 500, "min": 0, "max": 100000},
    },
    "exposure": {
        "dose":             {"label": "Dose", "unit": "mJ/cm²", "default": 100, "min": 0, "max": 5000},
        "time":             {"label": "Expose Time", "unit": "s", "default": 10, "min": 0, "max": 600},
        "resist_thickness": {"label": "Resist Thk", "unit": "nm", "default": 500, "min": 0, "max": 5000},
        "pitch":            {"label": "Pitch", "unit": "nm", "default": 1000, "min": 0, "max": 100000},
        "dose_frac":        {"label": "Dose Fraction", "unit": "", "default": 1.0, "min": 0, "max": 1.0},
    },
    "etch": {
        "source_power": {"label": "Source Power", "unit": "W", "default": 800, "min": 0, "max": 3000},
        "bias_nm": {"label": "CD Bias", "unit": "nm", "default": 100, "min": 0, "max": 10000},
        "bias_power":   {"label": "Bias Power", "unit": "W", "default": 200, "min": 0, "max": 1000},
        "pressure":     {"label": "Pressure", "unit": "mTorr", "default": 10, "min": 0, "max": 200},
        "temp":         {"label": "Temperature", "unit": "°C", "default": 20, "min": -10, "max": 200},
        "time":         {"label": "Etch Time", "unit": "s", "default": 120, "min": 0, "max": 3600},
        "gas_SF6":      {"label": "SF₆ Flow", "unit": "sccm", "default": 30, "min": 0, "max": 200},
        "gas_O2":       {"label": "O₂ Flow", "unit": "sccm", "default": 5, "min": 0, "max": 100},
        "gas_Ar":       {"label": "Ar Flow", "unit": "sccm", "default": 20, "min": 0, "max": 200},
        "gas_CHF3":     {"label": "CHF₃ Flow", "unit": "sccm", "default": 0, "min": 0, "max": 100},
    },
    "etch_rie": {
        "rf_power":  {"label": "RF Power", "unit": "W", "default": 300, "min": 0, "max": 1000},
        "pressure":  {"label": "Pressure", "unit": "mTorr", "default": 50, "min": 0, "max": 500},
        "temp":      {"label": "Temperature", "unit": "°C", "default": 20, "min": -10, "max": 200},
        "time":      {"label": "Etch Time", "unit": "s", "default": 120, "min": 0, "max": 3600},
        "gas_SF6":   {"label": "SF₆ Flow", "unit": "sccm", "default": 30, "min": 0, "max": 200},
        "gas_CF4":   {"label": "CF₄ Flow", "unit": "sccm", "default": 20, "min": 0, "max": 200},
        "gas_O2":    {"label": "O₂ Flow", "unit": "sccm", "default": 5, "min": 0, "max": 100},
        "gas_Ar":    {"label": "Ar Flow", "unit": "sccm", "default": 20, "min": 0, "max": 200},
        "gas_CHF3":  {"label": "CHF₃ Flow", "unit": "sccm", "default": 0, "min": 0, "max": 100},
    },
    "etch_ribe": {
        "beam_voltage": {"label": "Beam Voltage", "unit": "V", "default": 500, "min": 0, "max": 2000},
        "beam_current": {"label": "Beam Current", "unit": "mA", "default": 10, "min": 0, "max": 200},
        "pressure":     {"label": "Pressure", "unit": "mTorr", "default": 0.5, "min": 0, "max": 10},
        "temp":         {"label": "Temperature", "unit": "°C", "default": 20, "min": -10, "max": 200},
        "time":         {"label": "Etch Time", "unit": "s", "default": 300, "min": 0, "max": 7200},
        "gas_Cl2":      {"label": "Cl₂ Flow", "unit": "sccm", "default": 5, "min": 0, "max": 50},
        "gas_Ar":       {"label": "Ar Flow", "unit": "sccm", "default": 5, "min": 0, "max": 50},
    },
    "exposure_ldw": {
        "laser_power":     {"label": "Laser Power", "unit": "mW", "default": 20, "min": 0, "max": 500},
        "write_speed":     {"label": "Write Speed", "unit": "mm/s", "default": 100, "min": 0, "max": 5000},
        "passes":          {"label": "Passes", "unit": "", "default": 1, "min": 1, "max": 50},
        "pitch":           {"label": "Pitch", "unit": "nm", "default": 1000, "min": 0, "max": 100000},
        "resist_thickness": {"label": "Resist Thk", "unit": "nm", "default": 500, "min": 0, "max": 5000},
    },
    "coating_sputter": {
        "power":     {"label": "Power", "unit": "W", "default": 300, "min": 0, "max": 3000},
        "pressure":  {"label": "Pressure", "unit": "mTorr", "default": 3, "min": 0, "max": 50},
        "gas_Ar":    {"label": "Ar Flow", "unit": "sccm", "default": 30, "min": 0, "max": 200},
        "temp":      {"label": "Temperature", "unit": "°C", "default": 25, "min": 0, "max": 400},
        "time":      {"label": "Sputter Time", "unit": "s", "default": 600, "min": 0, "max": 14400},
    },
    "coating_ald": {
        "temp":      {"label": "Temperature", "unit": "°C", "default": 200, "min": 25, "max": 400},
        "pulse_a":   {"label": "Precursor A Pulse", "unit": "s", "default": 0.1, "min": 0, "max": 10},
        "purge_a":   {"label": "Purge A", "unit": "s", "default": 5, "min": 0, "max": 60},
        "pulse_b":   {"label": "Precursor B Pulse", "unit": "s", "default": 0.1, "min": 0, "max": 10},
        "purge_b":   {"label": "Purge B", "unit": "s", "default": 5, "min": 0, "max": 60},
        "cycles":    {"label": "Cycles", "unit": "", "default": 200, "min": 1, "max": 10000},
        "pressure":  {"label": "Pressure", "unit": "Torr", "default": 0.2, "min": 0, "max": 10},
    },
    "coating_ebd": {
        "rate":      {"label": "Dep Rate", "unit": "nm/s", "default": 0.5, "min": 0, "max": 50},
        "thickness": {"label": "Target Thk", "unit": "nm", "default": 100, "min": 0, "max": 10000},
        "pressure":  {"label": "Pressure", "unit": "Torr", "default": 1e-6, "min": 0, "max": 1e-3},
        "temp":      {"label": "Temperature", "unit": "°C", "default": 25, "min": 0, "max": 400},
    },
    "coating": {
        "rf_power": {"label": "RF Power", "unit": "W", "default": 200, "min": 0, "max": 2000},
        "temp":     {"label": "Temperature", "unit": "°C", "default": 300, "min": 0, "max": 500},
        "pressure": {"label": "Pressure", "unit": "Torr", "default": 0.5, "min": 0, "max": 10},
        "time":     {"label": "Dep Time", "unit": "s", "default": 300, "min": 0, "max": 7200},
        "gas_SiH4": {"label": "SiH₄ Flow", "unit": "sccm", "default": 50, "min": 0, "max": 500},
        "gas_N2O":  {"label": "N₂O Flow", "unit": "sccm", "default": 100, "min": 0, "max": 500},
        "gas_NH3":  {"label": "NH₃ Flow", "unit": "sccm", "default": 0, "min": 0, "max": 500},
    },
}


def defaults_for(subtype: str) -> dict[str, dict]:
    """返回某子类型的默认参数定义的深拷贝。"""
    return {k: dict(v) for k, v in copy.deepcopy(DEFAULT_PARAM_DEFS.get(subtype, {})).items()}
