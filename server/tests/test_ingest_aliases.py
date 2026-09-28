"""`kb/ingest.py` 别名表的**口径判据** —— 别名目标必须落在 `schema §三` 受控量名内（2026-09-28 工单）。

## 来历（数据线用**真执行表列头**核出）

数据线用 `DOE_Si_BBD_执行表.xlsx` 的**真列头** × `schema §三` 逐项核对，查出 `RESULT_ALIASES`
有 **5 处**目标量名未登记（`sidewall_angle_deg` / `scallop` / `uniformity_pct` / `cd_bot_nm` / `mask_remain_nm`）。
本侧**逐条复核后实为 9 处**（另 4 处：`swa_square_deg` · `depth_target_nm` · `mask_loss_nm` · `resist_residue`），
并顺带查出两件同族问题：① `scallop_pitch_nm` 被更短的 `scallop` 键**抢先前缀匹配**成 `scallop_nm`（错量名）；
② 真表还有**没进别名表**的列名会原样落草稿（`CD_grat_top_nm` / `占空比` / `Linewidth_nm` …）。

⚠️ **判据自己也要验**：`test_判据在负向样例上真报错` 用**注入的坏别名**跑一遍 —— 若判据是恒真的，这条会红。

口径：别名只许**照词表**（"不新增量名"）；真表里那批带**结构/工序限定词**的列（grat vs sq / after_dev）
本 ingester 的扁平 `results` 装不下 ⇒ 属**待数据线裁**项，钉在 `_PENDING_DATALINE` 里（**新出现的未登记名照样让判据红**）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from kb import ingest as ing
from kb.form_contract import quantities

#: 真表里**已上报数据线、待口径**的未登记列（不是"允许"，是"已登记在案"）：
#: 起因＝本 ingester 的 `results` 是扁平 dict，装不下"结构（grat/sq）"与"工序（显影后）"限定词，
#: 若都硬映到 `cd_top_nm`/`cd_bottom_nm` 会**互相覆盖**（＝丢数据）⇒ 不推断，交数据线裁。
_PENDING_DATALINE = (
    "CD_grat_top_nm", "CD_grat_bot_nm", "CD_sq_top_nm", "CD_sq_bot_nm",     # 结构限定（Ta 表，现全空）
    "Linewidth_nm", "CD_after_dev_top_nm", "CD_after_dev_bot_nm",           # 工序限定（曝光表，**已有值**）
    "占空比",                                                                # 设计参数（文本值，暂不落）
)


def _schema_quantities():
    """§三 受控量名（真源不在本机 ⇒ 如实跳过，不假通过）。"""
    q = quantities()
    if not q:
        pytest.skip("本机没有数据线的 schema_v0.1.md（评测/CI）⇒ 判据未执行（跳过 ≠ 通过）")
    return set(q)


# ---------------------------------------------------------------- 判据本体

def test_别名目标必须全在_schema_三_内():
    """**本单要求的那条判据**：`set(RESULT_ALIASES.values()) ⊆ §三 受控量名`。

    仍能抓住：别名表被写进一个"自造量名"（草稿会带未登记名 ⇒ 落 core 前必须人工再映一次）；
    也能抓住有人把 §三 里的量名**改名**而别名表没跟（跨线漂移）。
    """
    q = _schema_quantities()
    bad = ing.unregistered_alias_targets(q)
    assert bad == [], ("别名表里有未登记的量名（别名只许照 §三 词表）：" + "、".join(bad))


def test_代码侧可达名归一后也必须登记():
    """**更强的判据**：别名**键** ＋ `_XLSX_RESULTS`（真表列名候选）过一遍归一路径 ⇒ 仍须落 §三。

    为什么需要它：只查"别名表的值"会漏掉**没进别名表**的名字（它们会原样落草稿）。
    仍能抓住：`_XLSX_RESULTS` 里塞回 legacy 名（原先是 `sidewall_angle_deg`）。
    """
    q = _schema_quantities()
    bad = ing.unregistered_reachable_names(q)
    assert bad == [], "这些结果列名归一后仍不在 §三 内：" + "；".join(bad)


def test_判据在负向样例上真报错():
    """**判据自验**（防恒真）：合成输入上，自造量名必须报、合法量名不许报。

    这是"判据自己也会说谎"的那条纪律：只在本机绿、喂坏数据也不响的判据等于没有。
    """
    q = {"good_nm", "another_nm"}
    assert ing.unregistered_alias_targets(q, {"合法列": "good_nm"}) == []
    assert ing.unregistered_alias_targets(q, {"自造列": "invented_nm"}) == ["invented_nm"]
    # 混合：只报坏的那个（不误伤合法的）
    assert ing.unregistered_alias_targets(
        q, {"A": "good_nm", "B": "invented_nm", "C": "another_nm"}) == ["invented_nm"]


def test_真表判据能被注入的坏值抓到(monkeypatch):
    """在**真表**上做一次反向自证：注入自造目标 ⇒ 判据红；注入已登记目标 ⇒ 不红。

    仍能抓住：判据被写成"恒真"（例如受控量名表读成空集时反而全过 —— 这里用真表钉住）。
    """
    q = _schema_quantities()
    monkeypatch.setitem(ing.RESULT_ALIASES, "注入的坏列", "invented_nm")
    assert "invented_nm" in ing.unregistered_alias_targets(q), "注入的坏目标没被抓到 ⇒ 判据恒真"
    monkeypatch.setitem(ing.RESULT_ALIASES, "注入的好列", "swa_deg")
    assert "swa_deg" not in ing.unregistered_alias_targets(q), "合法目标被误报"


def test_非量测列一律不进_results():
    """现象 / 可信度 / 设计元数据 / **计划列** ⇒ 归一为空串（不进 `results`，也就不会变成量名）。

    仍能抓住：有人把 `残胶`（现象，属 `obs_type`）或 `Δd_target_nm`（计划）映成某个"量"——
    那会把现象/计划当实测写进 measurements（违三铁律）。
    """
    for h in ("残胶", "可靠性", "可靠性标注", "点类型", "图形", "显影质量", "Δd_target_nm"):
        assert ing._norm_result_key(h) == "", f"{h} 被当成了量测：{ing._norm_result_key(h)!r}"


def test_前缀匹配长键优先_扇贝周期不被扇贝抢先():
    """**实测踩过的 bug**：按插入序前缀匹配时 `scallop_pitch_nm` 会被更短的 `scallop` 抢先
    映成 `scallop_nm`（§三 里明明有 `scallop_pitch_nm`）⇒ 错量名。长键优先即修。
    """
    assert ing._norm_result_key("scallop_pitch_nm") == "scallop_pitch_nm"
    assert ing._norm_result_key("scallop_nm") == "scallop_nm"
    assert ing._norm_result_key("scallop") == "scallop_nm"       # 裸中文/英文短名仍归到扇贝深度


def test_单里的五处已逐条对齐():
    """数据线点名的 5 处 —— 每处都对齐到 §三（**逐条**，免得只修了总数）。"""
    q = _schema_quantities()
    for key, want in (("SWA_deg", "swa_deg"), ("scallop_nm", "scallop_nm"),
                      ("均匀性", "nu_pct"), ("CD_bot_nm", "cd_bottom_nm"),
                      ("掩膜剩余_nm", "mask_remaining_nm")):
        got = ing.RESULT_ALIASES.get(key)
        assert got == want, f"{key} → {got!r}（应为 {want!r}）"
        assert want in q, f"{want} 不在 §三 里"


# ---------------------------------------------------------------- 真表（只读 · 别处自动跳过）

def _xlsx_dir():
    d = Path(ing.XLSX_DIR)
    if not d.is_dir() or not list(d.glob("*.xlsx")):
        pytest.skip(f"本机没有 DOE 执行表：{d}（评测/CI 跳过是预期行为）")
    return d


def test_真表的结果列全部已登记_或已登记在案():
    """拿**真执行表**扫一遍：归一后的量名必须落 §三；`_PENDING_DATALINE` 里的属"已上报待裁"。

    ⚠️ 这条是**棘轮**：`_PENDING_DATALINE` 之外的任何新未登记名 ⇒ 红（不许悄悄多出来）。
    仍能抓住：新增列但没补别名表；或别名表改了却没覆盖真表里的既有列名。
    """
    q = _schema_quantities()
    d = _xlsx_dir()
    pending = set(_PENDING_DATALINE)
    worst: dict[str, list[str]] = {}
    for p in sorted(d.glob("*.xlsx")):
        if p.name.startswith("~$"):
            continue
        keys = ing.xlsx_columns(p)["result_keys"]
        bad = [h for h, k in keys.items() if k not in q and h not in pending]
        if bad:
            worst[p.name] = bad
    assert worst == {}, f"真表里出现**新的**未登记量名（补别名表或走数据线裁）：{worst}"
