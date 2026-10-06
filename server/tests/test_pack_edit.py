"""包内填数（`kb/pack_edit.py`）的判据 —— 「在工具里直接填数据，不必手改 CSV」。

**这一版是被一个真 bug 逼出来的**（2026-10-06 冒烟测试当场撞上）：
    第一版里"坏 run_id"那一支是先 `continue` 跳过、再查合法性 ⇒ 非法行被丢掉、`clean` 为空，
    于是**把一张有 5 行的 `measurements.csv` 写成了只有表头** —— 一次**静默清空**。
所以下面不只测"该拦的拦住了"，还**逐条断言"被拦下时盘上文件一字未动"**（`_sha` 前后比对）。
`test_清空闸` 是那道防线的回归网。

纪律（与 `conftest` 一致）：不碰真 core、不写数据资产、不靠网络；包全部落在 `tmp_path`，
可写根用 `OPENNANO_PACK_HOME` 指过去 —— 仓库样例 `samples/expack/` 本身是**只读来源**。
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from kb import pack_edit as pe


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """把"工作区"（可写根）指到 tmp_path，避免污染 `~/.opennano/packs`。"""
    h = tmp_path / "packs"
    monkeypatch.setenv("OPENNANO_PACK_HOME", str(h))
    return h


@pytest.fixture()
def pack(home):
    """样例包（仓库内，只读）复制到工作区后的可写副本。"""
    src = pe._repo_samples() / "DEMO-T1"
    assert src.exists(), "仓库样例包缺失：samples/expack/DEMO-T1"
    return Path(pe.copy_to_home(str(src))["path"])


# ---------------------------------------------------------------- 列表 / 复制 / 载入

def test_样例包在列表里且是只读来源(home):
    pl = pe.list_packs()
    demo = [p for p in pl["packs"] if p["batch_id"] == "DEMO-T1"]
    assert demo, "样例包应出现在列表里（Neo 上 fresh clone 就靠它看效果）"
    assert demo[0]["writable"] is False, "仓库样例必须是只读来源（否则会把演示包改脏）"
    assert demo[0]["editable"] is False
    assert str(home.resolve()) in pl["writable_roots"]


def test_复制到工作区之后可写(pack, home):
    d = pe.load_pack(str(pack))
    assert d["writable"] is True
    assert str(pack.resolve()).startswith(str(home.resolve()))
    # zip / 只读样例复制后不应再是"只读"提示
    assert not any("只读" in w for w in d["warnings"])


def test_载入返回表_修订号_枚举_与run清单(pack):
    d = pe.load_pack(str(pack))
    assert d["batch_id"] == "DEMO-T1"
    assert set(d["columns"]) == {"measurements", "observations"}
    # 列**取自包内表头**（不另编一份）⇒ 与盘上文件逐列一致
    for name in ("measurements", "observations"):
        head = (pack / f"{name}.csv").read_text(encoding="utf-8").splitlines()[0].split(",")
        assert d["columns"][name] == head
    assert set(d["revisions"]) == {"measurements", "observations"}
    assert len(d["revisions"]["measurements"]) == 64
    # 枚举：契约可达时用契约；公开 clone 无私有 schema ⇒ 回落语料。两种都必须**非空**。
    v = d["vocab"]
    assert v["source"] in ("contract", "corpus")
    assert v["quantities"], "量名下拉不能是空的（契约缺失时要回落到包内/样例语料）"
    assert v["obs_types"]
    # run 清单带检测身份（画布/协议 §15.1 的判据只有 METROLOGY_STAGES 一处）
    by = {r["run_id"]: r for r in d["runs"]}
    assert by["DEMO-T1-SEM-0001"]["is_metrology"] is True
    assert by["DEMO-T1-ICP-0003"]["is_metrology"] is False
    assert d["rules"]["editable_tables"] == ["measurements", "observations"]


# ---------------------------------------------------------------- 正常写入

def test_新增测量_自动补id与sample_且只动这一张表(pack):
    d = pe.load_pack(str(pack))
    obs_before, runs_before = _sha(pack / "observations.csv"), _sha(pack / "runs.csv")
    before = len(d["rows"]["measurements"])
    out = pe.save_pack(str(pack), {"measurements": d["rows"]["measurements"] + [
        {"run_id": "DEMO-T1-ICP-0003", "quantity": "depth_center_nm", "value": "520",
         "unit": "nm", "method": "SEM读图", "measured_by": "tester"}]},
        d["revisions"])
    assert out["saved"] == ["measurements.csv"]
    assert out["report"]["measurements"]["after"] == before + 1
    assert _sha(pack / "observations.csv") == obs_before, "没提交的表不该被重写"
    assert _sha(pack / "runs.csv") == runs_before, "runs.csv 永不被本功能改动"
    rows = pe.load_pack(str(pack))["rows"]["measurements"]
    new = rows[-1]
    assert new["meas_id"].startswith("DEMO-T1-ICP-0003.M"), "meas_id 应按包内同构规则补"
    assert new["sample_id"] == "DEMO-T1-01", "sample_id 应取自该 run 自带的样品"
    assert new["value"] == "520"
    assert out["report"]["measurements"]["filled"], "工具替你补的字段必须报出来（不静默）"


def test_空值行被丢弃而非写成0(pack):
    d = pe.load_pack(str(pack))
    n0 = len(d["rows"]["measurements"])
    out = pe.save_pack(str(pack), {"measurements": d["rows"]["measurements"] + [
        {"run_id": "DEMO-T1-ICP-0003", "quantity": "final_cd_nm", "value": "   "}]},
        d["revisions"])
    assert out["report"]["measurements"]["dropped_empty"] == 1
    assert out["report"]["measurements"]["after"] == n0, "空值 ≠ 0，不该新增行"
    assert "final_cd_nm,,," not in (pack / "measurements.csv").read_text(encoding="utf-8")


def test_写前留备份(pack, home):
    d = pe.load_pack(str(pack))
    pe.save_pack(str(pack), {"measurements": d["rows"]["measurements"] + [
        {"run_id": "DEMO-T1-ICP-0003", "quantity": "depth_center_nm", "value": "1"}]},
        d["revisions"])
    bdir = home / "_backups" / "DEMO-T1"
    assert bdir.exists() and list(bdir.glob("*-measurements.csv")), "改表之前必须留一份旧表"


def test_传空表等于不改(pack):
    d = pe.load_pack(str(pack))
    before = {n: _sha(pack / f"{n}.csv") for n in ("measurements", "observations")}
    out = pe.save_pack(str(pack), {"observations": d["rows"]["observations"]}, d["revisions"])
    assert out["saved"] == [], "内容没变就不该写盘（免得改 mtime 引噪音）"
    assert {n: _sha(pack / f"{n}.csv") for n in before} == before


# ---------------------------------------------------------------- 拦下：每一条都要求"盘上不动"

def _assert_rejected(pack, tables, revisions=None, *, msg_kw="", allow_clear=False):
    before = {n: _sha(pack / f"{n}.csv") for n in ("runs", "measurements", "observations")}
    with pytest.raises(pe.PackError) as ei:
        pe.save_pack(str(pack), tables, revisions, allow_clear=allow_clear)
    after = {n: _sha(pack / f"{n}.csv") for n in before}
    assert after == before, "被拦下的保存**必须一字未动**（这正是那次静默清空的反面）"
    if msg_kw:
        assert msg_kw in str(ei.value)
    return str(ei.value)


def test_未知run_id被拒且文件未动(pack):
    """★ 回归网：第一版就是这一支把 measurements.csv 清空的（非法行被跳过后只剩表头）。"""
    msg = _assert_rejected(pack, {"measurements": [
        {"run_id": "DEMO-T1-NOPE-0001", "quantity": "final_cd_nm", "value": "1"}]},
        pe.load_pack(str(pack))["revisions"], msg_kw="不在本包 runs.csv")
    assert "不替你新建 run" in msg


def test_检测run上挂测量被拒(pack):
    """协议 §15.1：`measurement.run_id` ＝「这个数是在哪次工艺之后测出来的」。"""
    d = pe.load_pack(str(pack))
    msg = _assert_rejected(pack, {"measurements": d["rows"]["measurements"] + [
        {"run_id": "DEMO-T1-SEM-0001", "quantity": "final_cd_nm", "value": "500"}]},
        d["revisions"], msg_kw="检测 run 上不许挂 measurement")
    assert "DEMO-T1-ICP-0003" in msg, "应指出该挂到哪条工艺 run"


def test_未知列被拒(pack):
    _assert_rejected(pack, {"measurements": [
        {"run_id": "DEMO-T1-ICP-0003", "quantity": "final_cd_nm", "value": "1", "bogus": "y"}]},
        pe.load_pack(str(pack))["revisions"], msg_kw="包内没有的列")


def test_填了值却没填run被拒(pack):
    _assert_rejected(pack, {"measurements": [{"quantity": "final_cd_nm", "value": "1"}]},
                     pe.load_pack(str(pack))["revisions"], msg_kw="没填 run_id")


def test_不可编辑的表被拒(pack):
    _assert_rejected(pack, {"runs": [{"run_id": "x"}]},
                     pe.load_pack(str(pack))["revisions"], msg_kw="只能写")


def test_并发revision冲突被拒(pack):
    d = pe.load_pack(str(pack))
    with pytest.raises(pe.PackConflict) as ei:
        pe.save_pack(str(pack), {"measurements": d["rows"]["measurements"]},
                     {"measurements": "0" * 64, "observations": d["revisions"]["observations"]})
    assert "重新载入" in str(ei.value)


def test_清空闸_不允许静默清空(pack):
    """★ 防线：盘上非空、这次交 0 行 ⇒ 拒写；要清空必须显式 allow_clear。"""
    d = pe.load_pack(str(pack))
    assert d["rows"]["measurements"], "前提：样例包本来就有数据"
    _assert_rejected(pack, {"measurements": []}, d["revisions"], msg_kw="拒写")
    # 显式确认后才允许
    out = pe.save_pack(str(pack), {"measurements": []}, d["revisions"], allow_clear=True)
    assert out["saved"] == ["measurements.csv"]
    assert pe.load_pack(str(pack))["rows"]["measurements"] == []


def test_只读样例包不可写(pack):
    src = str(pe._repo_samples() / "DEMO-T1")
    _assert_rejected(Path(src), {"measurements": []}, None, msg_kw="只读来源")


def test_越界路径被拒(home):
    for bad in ("/etc", str(Path.home() / "Documents")):
        with pytest.raises(pe.PackError) as ei:
            pe.save_pack(bad, {"measurements": []}, None)
        assert "不在允许的数据包目录内" in str(ei.value)


def test_zip包只读浏览_要复制才能填(tmp_path, home):
    import zipfile
    src = pe._repo_samples() / "DEMO-T1"
    z = tmp_path / "anywhere.zip"
    with zipfile.ZipFile(z, "w") as zf:
        for f in src.iterdir():
            zf.write(f, f.name)
    # zip 在允许根之外 ⇒ 先复制进工作区再谈
    with pytest.raises(pe.PackError):
        pe.load_pack(str(z))
    # 复制进工作区后：得到目录包 ⇒ 可写
    import shutil
    home.mkdir(parents=True, exist_ok=True)
    z2 = home / "DEMO-T1.zip"
    shutil.copy(z, z2)
    d = pe.load_pack(str(z2))
    assert d["kind"] == "zip" and d["writable"] is False
    assert any("复制到工作区" in w for w in d["warnings"])
    got = pe.copy_to_home(str(z2))
    assert Path(got["path"]).is_dir()
    assert pe.load_pack(got["path"])["writable"] is True
    blob, name = pe.pack_as_zip(got["path"])
    assert name.endswith(".zip") and len(blob) > 100


# ---------------------------------------------------------------- API 层（状态码要分清）

@pytest.fixture()
def client(home, monkeypatch):
    from fastapi.testclient import TestClient
    import main
    return TestClient(main.app)


def test_api_列包载入保存与错误码(client, pack):
    assert client.get("/api/pack/list").status_code == 200
    r = client.post("/api/pack/load", json={"path": str(pack)})
    assert r.status_code == 200
    d = r.json()
    r2 = client.post("/api/pack/save", json={"path": str(pack), "measurements": d["rows"]["measurements"],
                                             "revisions": d["revisions"]})
    assert r2.status_code == 200
    # 坏 run_id ⇒ 400（用户要改输入）
    r3 = client.post("/api/pack/save", json={"path": str(pack),
                                             "measurements": [{"run_id": "NOPE", "quantity": "q", "value": "1"}]})
    assert r3.status_code == 400 and "runs.csv" in r3.json()["detail"]
    # revision 冲突 ⇒ 409（界面提示重新载入）
    r4 = client.post("/api/pack/save", json={"path": str(pack), "measurements": [],
                                             "revisions": {"measurements": "0" * 64}})
    assert r4.status_code == 409
    # 越界路径 ⇒ 400
    assert client.post("/api/pack/load", json={"path": "/etc"}).status_code == 400
    # 下载
    r5 = client.get("/api/pack/download", params={"path": str(pack)})
    assert r5.status_code == 200 and r5.content[:2] == b"PK"


def test_漏提交的列保留盘上值(pack):
    """界面上只渲染了一部分列 ⇒ **没提交的列必须保留**（空串才是"清空"）。

    这条是自查出来的：若按"提交什么就是什么"写，编辑器没渲染的列会被静默清空。
    """
    d = pe.load_pack(str(pack))
    rows = d["rows"]["measurements"]
    target = rows[0]
    assert target.get("source_artifact_id") == "" or True       # 样例该列为空，先造一个值
    target["source_artifact_id"] = "ART-9"                      # 先在盘上落一个"别的列"的值
    pe.save_pack(str(pack), {"measurements": rows}, d["revisions"])
    d2 = pe.load_pack(str(pack))
    assert d2["rows"]["measurements"][0]["source_artifact_id"] == "ART-9"

    # 现在只提交"改过的那一列"，别的列整列不带 ⇒ 应保留
    partial = [{"meas_id": r["meas_id"], "value": r["value"]} for r in d2["rows"]["measurements"]]
    partial[0]["value"] = "211"
    pe.save_pack(str(pack), {"measurements": partial}, d2["revisions"])
    d3 = pe.load_pack(str(pack))
    got = d3["rows"]["measurements"][0]
    assert got["value"] == "211"
    assert got["source_artifact_id"] == "ART-9", "没提交的列被清空了 —— 这正是要防的静默清列"
    assert got["quantity"] == d2["rows"]["measurements"][0]["quantity"]

    # 显式空串 ＝ 清空（两种语义要能区分）
    partial[0]["source_artifact_id"] = ""
    pe.save_pack(str(pack), {"measurements": partial}, d3["revisions"])
    assert pe.load_pack(str(pack))["rows"]["measurements"][0]["source_artifact_id"] == ""
