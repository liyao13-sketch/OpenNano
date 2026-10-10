"""现场抄读四件（`kb/reading_sheet.py` ＋ `/api/sheet/*`）的判据。

工单 `20261010-数据线-to-工具线-01` 的验收标准之一是"**各有可复现的入口 ＋ 回归用例**"，
本文件就是那份用例。要点：

* **列名契约**（16 列，前 6 列冻结）是硬约束 —— 第一个用例把它钉住。
* **三类非读数行不进待填/一致/不符统计**（工单 §二-1）—— 单独用例，且**与参照实现的口径差异**
  （本实现把 `LoopN 次数` 也算非读数）在用例里写明。
* **比较器判据**三件：数值等价不算差异 · 空值不参与 · 文本大小写不敏感。
* **落库预览只读**：各条拒绝原因逐条验（缺 core_run_id / 量名不在 §三 / 数值不是数 / 只有图片）。

纪律同 `conftest`：不碰真 core、不写数据资产、不靠网络；可写路径一律用 `tmp_path`。
"""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import pytest

from kb import reading_sheet as rsh

SAMPLE = Path(__file__).resolve().parents[2] / "samples" / "readings" / "机台读数_DEMO_run01.csv"


# ─────────────────────────────────────────────────────── 列名契约（冻结）
def test_列名契约_16列_且前6列冻结():
    assert len(rsh.COLUMNS) == 16
    assert rsh.COLUMNS == ["recipe", "machine_step", "field", "parsed_value", "machine_value",
                           "match", "run", "reader", "read_at", "unit", "note", "quantity",
                           "method", "verification", "core_run_id", "sample_id"]
    assert rsh.FROZEN_COLS == rsh.COLUMNS[:6], "前 6 列是冻结列（既有对账逻辑依赖）"


# ─────────────────────────────────────────────────────── 解析 / 分类 / 统计
def test_解析样例表_分类与统计():
    s = rsh.read_sheet_file(SAMPLE)
    assert s["col_ok"] and not s["problems"], f"仓库样例必须是合规 16 列表：{s['problems']}"
    assert s["kinds"]["reading"] == 18, "3 步 × 6 字段"
    assert s["kinds"]["result"] == 2 and s["kinds"]["image"] == 2
    assert s["kinds"]["loop_range"] == 1 and s["kinds"]["loop_cycle"] == 1
    st = s["stats"]
    assert st["total"] == 18, "只有读数行进待填统计"
    assert st["excluded_non_reading"] == 6
    assert st["filled"] == 3 and st["pend"] == 15
    assert st["bad"] == 2, "步 2/3 的 Process time 与表里 parsed_value 不一致 ⇒ 不符"
    assert s["meta"]["core_run_id"] == "DEMO-T1-LDW-0001"
    assert s["meta"]["sample_id"] == "DEMO-T1-01"


def _row(**kw) -> list[str]:
    """按 16 列契约补一行（列序＝契约）。"""
    return [str(kw.get(c, "")) for c in rsh.COLUMNS]


def test_只有非读数行时_待填统计为0():
    text = rsh.to_csv([rsh.COLUMNS,
                       _row(recipe="RCP", machine_step="循环", field="Loop1 区间", parsed_value="1-3"),
                       _row(recipe="RCP", machine_step="循环", field="Loop1 次数", parsed_value="5"),
                       _row(recipe="RCP", machine_step="结果", field="x", machine_value="1"),
                       _row(recipe="RCP", machine_step="图片", field="a.png")])
    st = rsh.parse_sheet(text)["stats"]
    assert st["total"] == 0 and st["pend"] == 0
    assert st["excluded_non_reading"] == 4


def test_六列旧版导出_可读但报缺列():
    text = ("recipe,machine_step,field,parsed_value,machine_value,match\n"
            "RCP,3,MFC1,10,10,\nRCP,循环,Loop1 区间,,3-8,\n")
    s = rsh.parse_sheet(text)
    assert s["col_ok"] is False
    assert any("缺列" in p for p in s["problems"]), s["problems"]
    assert s["kinds"]["reading"] == 1 and s["kinds"]["loop_range"] == 1
    assert s["stats"]["total"] == 1


def test_行分类_五类():
    def row(step, field):
        return {"machine_step": step, "field": field}
    assert rsh.row_kind(row("3", "MFC1")) == "reading"
    assert rsh.row_kind(row("循环", "Loop1 区间")) == "loop_range"
    assert rsh.row_kind(row("循环", "Loop2 次数")) == "loop_cycle"
    assert rsh.row_kind(row("结果", "深度")) == "result"
    assert rsh.row_kind(row("图片", "a.png")) == "image"


# ─────────────────────────────────────────────────────── 判据
def test_preview_of_四态():
    assert rsh.preview_of({"machine_value": "", "parsed_value": "10"}) == "pend"
    assert rsh.preview_of({"machine_value": "10", "parsed_value": ""}) == "noval"
    assert rsh.preview_of({"machine_value": "10.0", "parsed_value": "10"}) == "ok"
    assert rsh.preview_of({"machine_value": "12", "parsed_value": "10"}) == "bad"


def test_norm_eq_空值不参与_数值等价():
    assert rsh.norm_eq("", "10") is None and rsh.norm_eq("10", "  ") is None
    assert rsh.norm_eq("1.0", "1") is True
    assert rsh.norm_eq("ON", "on") is True
    assert rsh.norm_eq("1", "2") is False


def test_differs_数值等价不算差异_空值不参与_单个不算差异():
    assert rsh.differs(["1.0", "1", "1.00"]) is False, "数值等价不算差异"
    assert rsh.differs(["", "", "5"]) is False, "只剩一个非空值 ⇒ 不算差异"
    assert rsh.differs(["", "5", "6"]) is True, "空值不参与，其余不同 ⇒ 差异"
    assert rsh.differs(["abc", "ABC"]) is False, "文本大小写不敏感"
    assert rsh.differs([]) is False


def test_guess_unit_后缀优先顺序():
    assert rsh.guess_unit("etch_rate_nm_min") == "nm/min", "_nm_min 必须优先于 _nm"
    assert rsh.guess_unit("depth_nm") == "nm"
    assert rsh.guess_unit("duty_pct") == "%"
    assert rsh.guess_unit("swa_deg") == "°"
    assert rsh.guess_unit("unknown_thing") == ""


# ─────────────────────────────────────────────────────── 命名 / 归档规则
def test_run_tag_零填充与文件名():
    assert rsh.run_tag("21") == "run21" and rsh.run_tag("2") == "run02"
    assert rsh.run_tag("R21") == "run21" and rsh.run_tag("") == ""
    st = {"slot": "DEMO", "run": "2", "recipe": "RCP-1"}
    assert rsh.fname_for(st) == "机台读数_DEMO_run02.csv"
    assert rsh.safe_name("a b/c:d") == "a_b_c_d"


def test_命名规则_占位符_补扩展名_空格转下划线():
    st = {"slot": "G021", "run": "21", "recipe": "RCP-400", "read_at": "2026-10-10T09:00:00"}
    img = {"name": "IMG 001.PNG", "tag": "SEM top"}
    n = rsh.img_final_name(st, img, 0, "{slot}_{run}_{tag}{seq}{ext}")
    assert n == "G021_run21_SEM_top01.png", n
    n2 = rsh.img_final_name(st, img, 1, "{date}_{run}_{tag}")     # 没写扩展名 ⇒ 自动补
    assert n2 == "20261010_run21_SEM_top.png", n2
    assert rsh.apply_name_rule("{a}-{b}", {"a": 1}) == "1-"


def test_批量命名_重名加序号():
    st = {"slot": "X", "run": "1", "recipe": "R"}
    imgs = [{"name": "a.png", "tag": "same"}, {"name": "b.png", "tag": "same"},
            {"name": "c.png", "tag": "same"}]
    out = rsh.finalize_names(st, imgs, "{slot}_{run}_{tag}{ext}")
    names = [x["final"] for x in out]
    assert names[0] == "X_run01_same.png"
    assert len(set(names)) == 3, f"重名必须加序号：{names}"


# ─────────────────────────────────────────────────────── 比较器
def _sheet(run: str, step2_value: str, result: str) -> dict:
    rows = []
    for step, val in (("1", "10"), ("2", step2_value)):
        rows.append({"machine_step": step, "field": "MFC1", "parsed_value": "10",
                     "machine_value": val})
    rows.append({"machine_step": "结果", "field": "深度", "machine_value": result,
                 "quantity": "depth_nm"})
    return rows


def test_比较器_run升序_差异标记_结果并排():
    a = {"label": "机台读数_X_run10.csv", "rows": _sheet("10", "10.0", "100"),
         "results": rsh.rows_to_results(_sheet("10", "10.0", "100"))}
    b = {"label": "机台读数_X_run2.csv", "rows": _sheet("2", "12", "120"),
         "results": rsh.rows_to_results(_sheet("2", "12", "120"))}
    cmp = rsh.build_compare([a, b])
    assert [r["no"] for r in cmp["runs"]] == [2, 10], "必须按 run 号升序，而不是输入顺序"
    step2 = [p for p in cmp["params"] if p["step"] == "2"][0]
    assert step2["differ"] is True and step2["vals"] == ["12", "10.0"]
    step1 = [p for p in cmp["params"] if p["step"] == "1"][0]
    assert step1["differ"] is False, "10 与 10 等价"
    assert cmp["diff_count"] == 1
    depth = [r for r in cmp["results"] if r["name"] == "深度"][0]
    assert depth["vals"] == ["120", "100"] and depth["differ"] is True
    csv_text = rsh.compare_to_csv(cmp)
    assert csv_text.startswith("差异,machine_step,field,label")
    assert "*" in csv_text


# ─────────────────────────────────────────────────────── 结果 / 模板
def test_结果模板_同名保留已填数值():
    tpl = [{"name": "深度", "unit": "nm", "quantity": "depth_nm", "method": "SEM读图"},
           {"name": "CD", "unit": "nm", "quantity": "final_cd_nm", "method": "SEM读图"}]
    cur = [{"name": "深度", "value": "205", "note": "上次"}, {"name": "旧项", "value": "9"}]
    out = rsh.results_from_tpl(tpl, cur)
    assert out[0]["value"] == "205" and out[0]["note"] == "上次", "同名项要保留已填值"
    assert out[1]["value"] == ""
    assert all(x["verification"] == rsh.UNVERIFIED for x in out), "默认未核实"
    back = rsh.tpl_from_results(out)
    assert [x["name"] for x in back] == ["深度", "CD"]


def test_模板存取删(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENNANO_SHEET_TEMPLATES", str(tmp_path / "tpl.json"))
    assert rsh.load_templates() == {}
    rsh.save_template("T1", [{"name": "深度", "quantity": "depth_nm"}])
    assert rsh.load_templates()["T1"][0]["name"] == "深度"
    rsh.delete_template("T1")
    assert rsh.load_templates() == {}
    with pytest.raises(ValueError):
        rsh.save_template("", [])


# ─────────────────────────────────────────────────────── 导出（16 列）
def test_导出16列_顺序与各类型行内容():
    state = {"recipe": "RCP-1", "run": "7", "reader": "me", "read_at": "2026-10-10T09:00",
             "core_run_id": "DEMO-T1-LDW-0001", "sample_id": "DEMO-T1-01"}
    rows = [{"machine_step": "3", "field": "MFC1", "parsed_value": "10", "machine_value": "10"},
            {"machine_step": "循环", "field": "Loop1 区间", "parsed_value": "", "machine_value": ""}]
    results = [{"name": "深度", "value": "205", "unit": "nm", "quantity": "depth_nm",
                "method": "SEM读图", "verification": "未核实"}]
    imgs = [{"name": "IMG_0001.png", "tag": "SEM_top"}]
    out = rsh.export_rows(state, rows, {1: {"a": "3", "b": "8"}}, results, imgs)
    assert out[0] == rsh.COLUMNS
    assert out[1][:6] == ["RCP-1", "3", "MFC1", "10", "10", ""]
    loop = [r for r in out if r[2] == "Loop1 区间"][0]
    assert loop[4] == "3-8", "Loop 区间由界面设定值覆写"
    res = [r for r in out if r[1] == rsh.RESULT_STEP][0]
    assert res[11:14] == ["depth_nm", "SEM读图", "未核实"]
    img = [r for r in out if r[1] == rsh.IMG_STEP][0]
    assert img[9] == "SEM_top" and img[10] == "IMG_0001.png", "图片行：unit=tag、note=原名"
    assert img[2].endswith(".png"), "图片行 field＝最终文件名"


# ─────────────────────────────────────────────────────── 落库预览（只读）
def test_落库预览_各条拒绝原因与默认值():
    rows = [
        {"machine_step": "结果", "field": "深度", "machine_value": "205", "unit": "nm",
         "quantity": "depth_nm", "method": "SEM读图", "core_run_id": "R1", "sample_id": "S1"},
        {"machine_step": "结果", "field": "野量", "machine_value": "1", "quantity": "not_in_vocab",
         "core_run_id": "R1"},
        {"machine_step": "结果", "field": "非数", "machine_value": "abc", "quantity": "depth_nm",
         "core_run_id": "R1"},
        {"machine_step": "图片", "field": "a.png", "core_run_id": "R1"},
    ]
    lp = rsh.landing_preview(rows, {"depth_nm"}, {"SEM读图"})
    assert lp["count"] == 1 and lp["accepted"][0]["meas_id"] == "R1.MR01"
    assert "a.png" in lp["accepted"][0]["note"], "图片并进该件的 note"
    why = " ".join(x["why"] for x in lp["rejected"])
    assert "不在契约" in why and "不是数" in why
    assert lp["note"].startswith("预览只读")


def test_落库预览_缺core_run_id整件跳过_且verification默认未核实():
    rows = [{"machine_step": "结果", "field": "深度", "machine_value": "1", "quantity": "depth_nm"}]
    lp = rsh.landing_preview(rows, {"depth_nm"}, None)
    assert lp["count"] == 0 and "core_run_id" in lp["rejected"][0]["why"]
    lp2 = rsh.landing_preview([{**rows[0], "core_run_id": "R"}], {"depth_nm"}, {"别的"})
    assert lp2["accepted"][0]["verification"] == rsh.UNVERIFIED
    assert lp2["accepted"][0]["method"] == "记录给出", "method 不在词表 ⇒ 与解析器一致地退成「记录给出」"


def test_落库预览_只有图片没有结果时点出来():
    rows = [{"machine_step": "图片", "field": "a.png", "core_run_id": "R"}]
    lp = rsh.landing_preview(rows, None, None)
    assert lp["count"] == 0 and any("图片" in x["why"] for x in lp["rejected"])


# ─────────────────────────────────────────────────────── 外置注解（公开层零指纹）
def test_外置字段注解_缺失时中性(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENNANO_READING_FIELDS", str(tmp_path / "none.json"))
    assert rsh.field_label("MFC1") == "MFC1", "注解缺失 ⇒ 标签＝字段名（公开仓不带实验室语义）"
    assert rsh.field_hint("MFC1") == "" and rsh.ignored_fields() == []
    assert rsh.is_bit_field("GVV1") is False
    rows = [{"field": "A"}, {"field": "B"}]
    assert len(rsh.drop_ignored(rows)) == 2, "没有忽略清单 ⇒ 一行不剔"


def test_外置字段注解_有文件时生效(tmp_path, monkeypatch):
    f = tmp_path / "fields.json"
    f.write_text(json.dumps({"fields": {"MFC1": {"label": "GAS-A", "hint": "流量"}},
                             "ignored": ["He Press"], "bits": ["GVV1"]}, ensure_ascii=False),
                 encoding="utf-8")
    monkeypatch.setenv("OPENNANO_READING_FIELDS", str(f))
    assert rsh.field_label("MFC1") == "GAS-A" and rsh.field_hint("MFC1") == "流量"
    assert rsh.is_bit_field("GVV1") is True
    assert [r["field"] for r in rsh.drop_ignored([{"field": "He Press ESC"}, {"field": "MFC1"}])] == ["MFC1"]


# ─────────────────────────────────────────────────────── 服务端：图片上传 / 归档
def test_上传与改名归档(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENNANO_READINGS_HOME", str(tmp_path / "readings"))
    st = {"slot": "DEMO", "run": "2", "recipe": "RCP-1", "read_at": "2026-10-10T09:00"}
    a = rsh.save_upload(st, "IMG 001.png", b"\x89PNG-fake")
    b = rsh.save_upload(st, "IMG 001.png", b"\x89PNG-fake2")
    assert a["name"] == "IMG_001.png" and b["name"] == "IMG_001_01.png", "同名不覆盖"
    out = rsh.archive_images(st, [{"name": a["name"], "tag": "SEM top"}], "{slot}_{run}_{tag}{seq}{ext}")
    assert out["archived"][0]["final"] == "DEMO_run02_SEM_top01.png"
    assert Path(out["archived"][0]["path"]).exists()
    assert out["missing"] == []


def test_列出抄读表_含仓库中性样例():
    d = rsh.list_sheets()
    names = [s["name"] for s in d["sheets"]]
    assert any("DEMO" in n for n in names), f"仓库样例应可被列出（fresh clone 也能试）：{names}"
    demo = [s for s in d["sheets"] if "DEMO" in s["name"]][0]
    assert demo["full16"] is True and demo["frozen6_ok"] is True


# ─────────────────────────────────────────────────────── API 层
@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENNANO_READINGS_HOME", str(tmp_path / "readings"))
    monkeypatch.setenv("OPENNANO_SHEET_TEMPLATES", str(tmp_path / "tpl.json"))
    from fastapi.testclient import TestClient
    import main
    return TestClient(main.app)


def test_api_列表_解析_预览(client):
    r = client.get("/api/sheet/list")
    assert r.status_code == 200 and r.json()["columns"] == rsh.COLUMNS
    p = client.post("/api/sheet/parse", json={"path": str(SAMPLE)})
    assert p.status_code == 200
    d = p.json()
    assert d["stats"]["total"] == 18 and d["col_ok"] is True
    assert d["vocab"]["quantities"], "量名下拉不许空（真源不可达时要回落到样例语料）"
    assert "quantity_gate" in d["vocab"] and "quantity_source" in d["vocab"]
    assert d["field_meta"][0]["label"], "字段标签走外置注解（缺失则＝字段名）"
    # 粘贴正文也走同一条路
    assert client.post("/api/sheet/parse", json={"text": SAMPLE.read_text(encoding="utf-8")}).status_code == 200
    assert client.post("/api/sheet/parse", json={"path": "/nope/x.csv"}).status_code == 400
    pv = client.post("/api/sheet/preview", json={"rows": d["rows"]})
    assert pv.status_code == 200 and pv.json()["count"] == 2


def test_api_比较器与导出(client, tmp_path):
    # 造第二张表：run=2、把 step2 的 Process time 抄成 12.0（与表里 10.0 不符）
    rows = list(csv.DictReader(SAMPLE.open(encoding="utf-8-sig")))
    for r in rows:
        r["run"] = "2"
        if r["machine_step"] == "2" and r["field"] == "Process time sec.":
            r["machine_value"] = "11.0"      # 样例里本来是 12.0 ⇒ 造出一处真差异
    p2 = tmp_path / "机台读数_DEMO_run02.csv"
    with p2.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()), lineterminator="\r\n")
        w.writeheader()
        w.writerows(rows)
    r = client.post("/api/sheet/compare", json={"paths": [str(SAMPLE), str(p2)]})
    assert r.status_code == 200
    cmp = r.json()
    assert [x["no"] for x in cmp["runs"]] == [1, 2]
    assert cmp["diff_count"] >= 1 and "\r\n" in cmp["csv"]
    assert client.post("/api/sheet/compare", json={"paths": [str(SAMPLE)]}).status_code == 400
    # 导出：CSV 头就是 16 列契约
    sh = client.post("/api/sheet/parse", json={"path": str(SAMPLE)}).json()
    out = client.post("/api/sheet/export", json={"state": sh["meta"], "rows": sh["rows"],
                                                 "results": rsh.rows_to_results(sh["rows"]),
                                                 "images": [{"name": "IMG_1.png", "tag": "top"}]})
    assert out.status_code == 200
    head = out.text.splitlines()[0].split(",")
    assert head == rsh.COLUMNS
    assert "attachment" in out.headers["content-disposition"]


def test_api_模板与上传改名(client):
    assert client.get("/api/sheet/templates").json()["templates"] == {}
    sv = client.post("/api/sheet/templates", json={"name": "T1", "items": [{"name": "深度"}]})
    assert sv.status_code == 200 and "T1" in sv.json()["templates"]
    assert client.post("/api/sheet/templates/delete", json={"name": "T1"}).json()["templates"] == []
    import base64
    up = client.post("/api/sheet/upload", json={"state": {"slot": "D", "run": "3"},
                                                "name": "a.png",
                                                "data_b64": base64.b64encode(b"xx").decode()})
    assert up.status_code == 200 and up.json()["name"] == "a.png"
    bad = client.post("/api/sheet/upload", json={"state": {}, "name": "a", "data_b64": "!!"})
    assert bad.status_code == 400
    rn = client.post("/api/sheet/rename", json={"state": {"slot": "D", "run": "3"},
                                                "images": [{"name": "a.png", "tag": "top"}],
                                                "rule": "{slot}_{run}_{tag}{seq}{ext}"})
    assert rn.status_code == 200
    assert rn.json()["archived"][0]["final"] == "D_run03_top01.png"


def test_比较器_认不出run号时报null而不是魔数():
    """旧版 6 列导出既没有 `run` 列、文件名也没有 `runNN`（实测 NAS 上 G021/G031 就是）
    ⇒ 必须**如实报 null** 并在 `unknown_run` 里点出来，不许回 2147483647 这种魔数。"""
    rows = [{"machine_step": "3", "field": "MFC1", "machine_value": "10"}]
    a = {"label": "机台读数_G021.csv", "rows": rows}
    b = {"label": "机台读数_G031.csv", "rows": rows}
    cmp = rsh.build_compare([a, b])
    assert [r["no"] for r in cmp["runs"]] == [None, None]
    assert sorted(cmp["unknown_run"]) == ["机台读数_G021.csv", "机台读数_G031.csv"]
    # 有 run 号的仍按升序排在前
    c = {"label": "机台读数_X_run2.csv", "rows": rows}
    cmp2 = rsh.build_compare([a, c])
    assert [r["no"] for r in cmp2["runs"]] == [2, None]
