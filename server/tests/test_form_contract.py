"""表单/参数契约（`kb/form_contract.py`）—— 回归网。

契约的真源是 `schema_v0.1.md`（§三/§四/§十一/§十三）+ `现象受控词表.csv`。
⚠️ 这两份在**数据资产**里（CI 上不存在）⇒ 词表类断言在本机跑；
**逻辑类**断言（`check_steps` / `check_measurements` / `check_eq_state`）作为纯函数测，不依赖真源。
"""
from __future__ import annotations

import pytest


# ------------------------------------------------------------------ 纯逻辑（不依赖真源）
def test_非法键名一律算错误_不分严格模式(contract_source):
    """`Step type` 这种**带空格**的原始列名混进来必须报错（历史与新增都不许）。"""
    from kb.form_contract import _check_steps_with, check_steps, errors_only
    issues = check_steps([{"step_order": 3, "param_json": {"Step type": 1, "apc1_press": 2}}],
                         strict=False, stage="DRIE")
    assert errors_only(issues) == ["[错误] 步骤 3: 非法键名 `Step type`（须 snake_case 规范键）"]


def test_gvv_缺失按_stage_判_而不是无条件要求(contract_source):
    """`gvv1/gvv2` 是**旁通阀**开关量：DRIE/ICP/RIE 必须有（0 也要显式提交）；
    PECVD/曝光这类工序没有这两个阀 ⇒ **不许**误报缺键。
    ⚠️ 这条是回归网抓出的真 bug：以前传了 `stage="PECVD"` 仍然报缺 gvv。
    """
    from kb.form_contract import _check_steps_with, check_steps, errors_only
    keys = {"apc1_press": {}, "gvv1": {}, "gvv2": {}}     # 注入键表 ⇒ 离线可跑
    step = [{"step_order": 1, "param_json": {"apc1_press": 2}}]
    assert len(errors_only(_check_steps_with(step, keys, strict=True, stage="DRIE"))) == 2
    assert errors_only(_check_steps_with(step, keys, strict=True, stage="PECVD")) == []
    assert errors_only(_check_steps_with(step, keys, strict=True, stage="MA6")) == []
    # 没给 stage：步骤里有 gvv 痕迹 ⇒ 按"该有"要求；完全没痕迹就不猜
    gvv_step = [{"step_order": 1, "param_json": {"gvv1": 0}}]
    assert len(errors_only(_check_steps_with(gvv_step, keys, strict=True, stage=""))) == 1   # 缺 gvv2
    assert errors_only(_check_steps_with(step, keys, strict=True, stage="")) == []
    ok = [{"step_order": 1, "param_json": {"gvv1": 0, "gvv2": 0}}]           # 0 也算在位
    assert errors_only(_check_steps_with(ok, keys, strict=True, stage="ICP")) == []
    # 入口 `check_steps` 在没有键表时必须**明说"未校验键名"**，而不是静默通过
    got = check_steps(step, strict=True, stage="DRIE")
    assert len(errors_only(got)) == 2


def test_非契约键_严格是提示_历史模式换措辞(contract_source):
    from kb.form_contract import _check_steps_with
    s = [{"step_order": 2, "param_json": {"some_new_col": 1}}]
    assert "§13.2 兜底允许" in _check_steps_with(s, {}, strict=True, stage="PECVD")[0]
    assert "历史遗留键" in _check_steps_with(s, {}, strict=False, stage="PECVD")[0]


def test_测量_值只许数字或留空(contract_source):
    """零号铁律的落点：没测**留空**；`0` / `-` / `N/A` 都不许（0 是"测到 0"≠"没测"）。"""
    from kb.form_contract import check_measurements
    errs = check_measurements([
        {"quantity": "cd_top_nm", "value": ""},                # 未测 ⇒ 合法
        {"quantity": "cd_top_nm", "value": "512", "verification": "已核实"},
        {"quantity": "cd_top_nm", "value": "N/A"},
        {"quantity": "cd_top_nm", "value": "-"},
        {"quantity": "cd_top_nm", "value": "12nm"},            # 带单位 ⇒ 非法（单位另列）
        {"value": "1"},                                        # 缺 quantity
        {"quantity": "cd_top_nm", "value": "1", "verification": "大概吧"},
    ])
    assert not any("测量 1" in e for e in errs) and not any("测量 2" in e for e in errs)
    assert any("测量 3" in e and "不是数字" in e for e in errs)
    assert any("测量 4" in e for e in errs)
    assert any("测量 5" in e for e in errs)
    assert any("测量 6" in e and "缺 quantity" in e for e in errs)
    assert any("测量 7" in e and "verification" in e for e in errs)


def test_eq_state_超量程留空但行保留(contract_source):
    """环境记录宁可缺，不许错：超量程/非数字 ⇒ **留空 + 报警**（绝不是截断到边界）。"""
    from kb.form_contract import check_eq_state
    out, warns = check_eq_state({"date": "2026-09-12", "tool": "RIE-400iPB",
                                "env_temp_c": "23", "env_rh_pct": "150"})
    assert out["env_temp_c"] == 23 and "env_rh_pct" not in out
    assert any("超量程" in w for w in warns)
    out, warns = check_eq_state({"date": "2026-9-12", "env_temp_c": "abc"})
    assert "date" not in out and out["tool"] == "(环境)"        # 认不出的日期 ⇒ 不写
    assert any("不是 YYYY-MM-DD" in w for w in warns)
    assert any("非数字" in w for w in warns)


def test_eq_state_清洗完成只认是_否(contract_source):
    from kb.form_contract import check_eq_state
    for raw, want in (("是", "是"), ("Y", "是"), ("true", "是"), ("否", "否"), ("0", "否")):
        out, _ = check_eq_state({"date": "2026-09-12", "clean_done": raw})
        assert out["clean_done"] == want, raw
    out, _ = check_eq_state({"date": "2026-09-12", "clean_done": "差不多"})
    assert "clean_done" not in out                              # 认不出就不写


# ------------------------------------------------------------------ 词表（真源优先；没真源用合成夹具）
def test_现象受控词表_只认表内词(contract_source):
    """现象类型必须走受控词表（自由文本会毁掉后续统计）。"""
    from kb.form_contract import check_observations, observations
    vocab = observations()
    want = 32 if contract_source == "real" else 3
    assert len(vocab) == want, [o["obs_type"] for o in vocab]
    assert all(o.get("obs_type") for o in vocab)
    errs = check_observations([{"obs_type": vocab[0]["obs_type"]},
                               {"obs_type": "我自己编的现象"},
                               {"obs_type": ""}])
    assert any("我自己编的现象" in e for e in errs)
    assert any("缺 obs_type" in e for e in errs)


def test_量名词与参数键来自_契约真源(contract_source):
    """量名词/参数键**只能来自 schema/解析器**（工具不硬编码副本 —— 否则必然漂移）。

    ⚠️ `param_keys()` 依赖数据线 `datasets_menu.py` 的映射表；没装载数据资产时
    应当抛 `ContractUnavailable`（**不是**静默返回空表），接口层据此给 503。
    """
    from kb.form_contract import ContractUnavailable, contract, param_keys, quantities
    qs = quantities()
    assert len(qs) > 30 and all(isinstance(q, str) and q for q in qs)
    assert "cd_top_nm" in qs and "线宽" not in qs         # 量名词是英文规范名，不是口语
    try:
        kk = param_keys()
    except ContractUnavailable as e:
        pytest.skip(f"菜单解析器不可达（本机未装载数据资产）：{e}")
    assert len(kk) > 30
    assert all({"from", "keep_zero"} <= set(v) for v in kk.values()), kk
    c = contract()
    assert c["quantities"] == qs
    assert list(c["status"]) == ["planned", "running", "done", "aborted"]
    assert list(c["verification"]) == ["已核实", "未核实", "存疑"]
    assert "设备遥测" in c["method"]                       # 机台 log 与口述要能区分来源
    assert c["switch_required"] == ["gvv1", "gvv2"]        # 旁通阀是**必需**开关量
    assert len(c["observations"]) == (32 if contract_source == "real" else 3)
