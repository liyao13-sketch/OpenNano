"""团队账号 / 会话 / 留痕 / 版本守卫（P0 · 2026-09-15）—— 回归网。

钉住的是**团队化第一天就必须成立的四件事**（都是"不做就等于没有"的）：

1. **不设默认口令**：首次初始化只能建**第一个管理员**，建完就不许再 `setup`；
2. **口令只存哈希**：账号文件里搜不到明文；
3. **未登录不能读也不能写**（`OPENNANO_AUTH=on` 时），且**停用立刻生效**；
4. **别人先改过就拒写**（工程 `_rev` / 库 `LibraryConflict`）——
   这是多人共用一台服务器**最容易丢改动**的地方：每个人 POST 的都是整份工程/整份库。

外加持之以恒的一条：**留痕**（谁在什么时候改了什么）真的落盘、真的能读回来。
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def team(tmp_path, monkeypatch):
    """把服务端拉进"团队模式"：强制登录 + 账号/留痕/密钥全在临时目录。

    ⚠️ `OPENNANO_PROJECTS_DIR` 必须在 `import main` **之前**设好不够 —— `main` 是用
    `from opennano_config import PROJECTS_DIR` 拿进来的**模块级常量**，所以要 `setattr` 覆盖。
    """
    monkeypatch.setenv("OPENNANO_AUTH", "on")
    monkeypatch.setenv("OPENNANO_ACCOUNTS", str(tmp_path / "users.json"))
    monkeypatch.setenv("OPENNANO_AUDIT", str(tmp_path / "audit.log"))
    monkeypatch.setenv("OPENNANO_SERVER_SECRET", str(tmp_path / ".server_secret"))
    monkeypatch.setenv("OPENNANO_PBKDF2_ITERS", "1000")
    import main
    monkeypatch.setattr(main, "PROJECTS_DIR", tmp_path / "projects")
    (tmp_path / "projects").mkdir(exist_ok=True)
    return {"tmp": tmp_path, "main": main,
            "client": TestClient(main.app, raise_server_exceptions=False)}


def _setup(c, username="alice", password="pw_test_1"):
    return c.post("/api/auth/setup", json={"username": username, "name": "owner",
                                           "password": password})


def _add_member(team, c, username="bob", password="member_pass_1"):
    r = c.post("/api/auth/users", json={"username": username, "name": "小计",
                                        "password": password, "role": "member"})
    return r


# ---------------------------------------------------------------- ① 不设默认口令

def test_first_run_needs_setup_and_setup_works_only_once(team):
    c = team["client"]
    st = c.get("/api/auth/state").json()
    assert st["needs_setup"] is True and st["user"] is None and st["auth_required"] is True

    r = _setup(c)
    assert r.status_code == 200 and r.json()["user"]["role"] == "admin"
    # 建完就是"已初始化"：**不允许再 setup**（否则任何人都能再造一个管理员）
    assert c.get("/api/auth/state").json()["needs_setup"] is False
    r2 = _setup(c, username="someone_else", password="other_pass_1")
    assert r2.status_code == 409 and "已经初始化" in r2.json()["detail"]


def test_no_default_account_exists_before_setup(team):
    """不许有"出厂账号/出厂口令"：初始化之前账号表必须是空的。"""
    from engine import auth as A
    assert A.AccountStore().users() == []


# ---------------------------------------------------------------- ② 口令只存哈希

def test_password_is_never_stored_in_clear(team):
    c = team["client"]
    _setup(c, password="pw_test_1")
    raw = (team["tmp"] / "users.json").read_text(encoding="utf-8")
    assert "pw_test_1" not in raw, "口令明文进了账号文件"
    u = json.loads(raw)["users"][0]
    assert u["algo"] == "pbkdf2_sha256" and u["salt"] and u["hash"] and int(u["iters"]) > 0
    # 对外表示里**不许**带 salt/hash
    pub = c.get("/api/auth/state").json()["user"]
    assert set(pub) & {"salt", "hash", "iters"} == set()


# ---------------------------------------------------------------- ③ 未登录挡在门外

def test_unauthenticated_requests_are_rejected(team):
    c = team["client"]
    _setup(c)
    anon = TestClient(team["main"].app, raise_server_exceptions=False)   # 干净 cookie
    assert anon.get("/api/library").status_code == 401
    assert anon.post("/api/project/save", json={"name": "X", "modules": [], "edges": []}).status_code == 401
    assert anon.get("/api/audit").status_code == 401
    # 公开路径：健康检查 / 状态 / 登录 / 初始化
    assert anon.get("/api/auth/state").status_code == 200
    assert anon.get("/api/health").status_code in (200, 404)   # 存在与否都不该 401


def test_login_grants_access_and_wrong_password_does_not(team):
    c = team["client"]
    _setup(c, username="alice", password="pw_test_1")
    anon = TestClient(team["main"].app, raise_server_exceptions=False)
    assert anon.post("/api/auth/login", json={"username": "alice",
                                             "password": "wrong_pass"}).status_code == 401
    assert anon.get("/api/library").status_code == 401
    ok = anon.post("/api/auth/login", json={"username": "alice", "password": "pw_test_1"})
    assert ok.status_code == 200
    assert anon.get("/api/library").status_code == 200
    assert anon.post("/api/auth/logout").status_code == 200
    assert anon.get("/api/library").status_code == 401


def test_only_admin_manages_accounts_and_members_can_login(team):
    c = team["client"]
    _setup(c)
    assert _add_member(team, c).status_code == 200

    member = TestClient(team["main"].app, raise_server_exceptions=False)
    assert member.post("/api/auth/login", json={"username": "bob",
                                                "password": "member_pass_1"}).status_code == 200
    assert member.get("/api/library").status_code == 200                  # 能干业务
    r = _add_member(team, member, username="third", password="third_pass_1")
    assert r.status_code == 403 and "管理员" in r.json()["detail"]          # 不能管账号
    assert member.get("/api/auth/users").status_code == 403


def test_deactivated_account_is_locked_out_immediately(team):
    """停用要**立刻**生效（包括已经登录的会话）—— 否则"离组的人"还能一直改数据。"""
    c = team["client"]
    _setup(c)
    _add_member(team, c)
    member = TestClient(team["main"].app, raise_server_exceptions=False)
    member.post("/api/auth/login", json={"username": "bob", "password": "member_pass_1"})
    assert member.get("/api/library").status_code == 200

    uid = next(u["id"] for u in c.get("/api/auth/users").json()["users"]
               if u["username"] == "bob")
    assert c.post(f"/api/auth/users/{uid}", json={"active": False}).status_code == 200
    assert member.get("/api/library").status_code == 401                  # 会话当场失效
    assert member.post("/api/auth/login", json={"username": "bob",
                                                "password": "member_pass_1"}).status_code == 401


def test_corrupt_accounts_file_is_not_silently_replaced(team):
    """账号文件坏掉时：**不静默重建**（那会把全组身份清空且没人知道），而是报错并拒绝初始化。"""
    p = team["tmp"] / "users.json"
    p.write_text("{ 这不是 json", encoding="utf-8")
    c = team["client"]
    st = c.get("/api/auth/state").json()
    assert st["error"] and st["needs_setup"] is False
    assert p.read_text(encoding="utf-8") == "{ 这不是 json"              # 原文件没被动
    assert _setup(c).status_code == 409


# ---------------------------------------------------------------- ④ 谁在我之前改过

def test_project_save_rejects_a_stale_rev(team):
    """**最有价值的一条**：两个人先后保存同一个工程 ⇒ 后保存的必须被拦，不许静默覆盖。"""
    c = team["client"]
    _setup(c)
    base = {"name": "AR50-T1", "modules": [{"id": "m1"}], "edges": []}
    first = c.post("/api/project/save", json=base)
    assert first.status_code == 200
    rev1 = first.json()["_rev"]
    assert rev1

    # 甲载入并保存了一版
    r = c.post("/api/project/save", json={**base, "modules": [{"id": "m1"}, {"id": "m2"}],
                                         "rev": rev1})
    assert r.status_code == 200 and r.json()["_rev"] != rev1

    # 乙手上还是 rev1（他是在甲保存之前载入的）⇒ 拒写
    stale = c.post("/api/project/save", json={**base, "modules": [{"id": "m1"}, {"id": "m3"}],
                                             "rev": rev1})
    assert stale.status_code == 409 and "被改过" in stale.json()["detail"]
    on_disk = json.loads((team["tmp"] / "projects" / "AR50-T1.json").read_text(encoding="utf-8"))
    assert [m["id"] for m in on_disk["modules"]] == ["m1", "m2"], "甲的工作被乙覆盖了"
    # 人明确确认后才允许覆盖，并且**留痕**
    forced = c.post("/api/project/save", json={**base, "modules": [{"id": "m3"}],
                                              "rev": rev1, "force": True})
    assert forced.status_code == 200
    rows = (team["tmp"] / "audit.log").read_text(encoding="utf-8")
    assert "project.overwrite" in rows


def test_project_save_without_rev_cannot_overwrite_an_existing_name(team):
    """没带版本就去写一个**已存在**的工程名（比如"另存为"撞了同事的工程）⇒ 拦下。"""
    c = team["client"]
    _setup(c)
    c.post("/api/project/save", json={"name": "SomeoneElse", "modules": [], "edges": []})
    r = c.post("/api/project/save", json={"name": "SomeoneElse", "modules": [{"id": "x"}],
                                         "edges": []})
    assert r.status_code == 409 and "没有保存" in r.json()["detail"]


def test_library_loaded_rev_mismatch_is_refused_not_overwritten(tmp_path, monkeypatch):
    """库是**整份覆盖**写的：盘上变了就拒写（真实踩过：手改 4 台机台被运行中的进程抹掉）。"""
    from engine.library import LibraryConflict, LibraryStore
    p = tmp_path / "library.json"
    store = LibraryStore(p)
    store.data["machines"] = [{"id": "mc1", "name": "A", "tool_id": "RIE10NR"}]
    store._save()                                     # 正常：盘上 == 内存

    external = json.loads(p.read_text(encoding="utf-8"))
    external["machines"] = [{"id": "mc1", "name": "B", "tool_id": "PECVD-SAMCO"},
                            {"id": "mc2", "name": "C", "tool_id": "SI500"}]
    p.write_text(json.dumps(external, ensure_ascii=False), encoding="utf-8")   # "别人"改了

    store.data["machines"].append({"id": "mc9", "name": "D", "tool_id": "DWL66"})
    with pytest.raises(LibraryConflict):
        store._save()
    assert len(json.loads(p.read_text(encoding="utf-8"))["machines"]) == 2, "别人的改动被覆盖了"

    store.reload()                                    # 显式接受对方版本
    assert [m["name"] for m in store.data["machines"]] == ["B", "C"]


# ---------------------------------------------------------------- 留痕

def test_audit_records_who_did_what(team):
    c = team["client"]
    _setup(c, username="alice", password="alice_pass_1")
    c.post("/api/project/save", json={"name": "P1", "modules": [], "edges": []})
    rows = c.get("/api/audit").json()["rows"]
    assert any(r["actor"] == "alice" and r["action"] == "project.save"
               and r["target"] == "P1" for r in rows), rows
    assert any(r["action"] == "auth.setup" for r in rows)


def test_audit_still_records_when_auth_is_off(team, monkeypatch):
    """`AUTH=off`（单人本地）也要留痕 —— 只是 actor 记成 `anonymous`。"""
    monkeypatch.setenv("OPENNANO_AUTH", "off")
    c = team["client"]
    assert c.post("/api/project/save", json={"name": "Solo", "modules": [],
                                            "edges": []}).status_code == 200
    log = (team["tmp"] / "audit.log").read_text(encoding="utf-8")
    assert '"actor": "anonymous"' in log and "project.save" in log


def test_audit_log_is_append_only_and_readable(team):
    from engine import audit
    audit.record("a", "x.one", target="t1")
    audit.record("b", "x.two", target="t2")
    rows = audit.tail(limit=10)
    assert [r["action"] for r in rows[:2]] == ["x.two", "x.one"]        # 最新在前
    assert audit.tail(limit=10, actor="a")[0]["action"] == "x.one"


# ---------------------------------------------------------------- 配置必须"用时现读"

def test_config_paths_are_read_at_call_time_not_at_import(tmp_path, monkeypatch):
    """**路径不许在 import 时冻结** —— 这条是 2026-09-15 的真实事故换来的：

    第一版用 `cfg.ACCOUNTS_PATH`（`opennano_config` 导入时求值的常量），于是用例里改 env 毫无作用，
    **跑一趟回归网就把真 `~/.opennano/users.json` / `audit.log` / `.server_secret` 写了出来**
    （和当年 `PROJECTS_DIR` 被冒烟脚本顶掉同一个坑）。判据本身很土，但守的是"测试绝不碰真资产"。
    """
    from engine import auth, audit
    monkeypatch.setenv("OPENNANO_ACCOUNTS", str(tmp_path / "u.json"))
    monkeypatch.setenv("OPENNANO_AUDIT", str(tmp_path / "a.log"))
    monkeypatch.setenv("OPENNANO_SERVER_SECRET", str(tmp_path / "s"))
    assert auth._accounts_path() == tmp_path / "u.json"
    assert auth._secret_path() == tmp_path / "s"
    assert audit._path() == tmp_path / "a.log"
    # 同一进程里再换一次也必须跟着变（这正是"导入时冻结"做不到的事）
    other = tmp_path / "another"
    monkeypatch.setenv("OPENNANO_ACCOUNTS", str(other / "u.json"))
    assert auth._accounts_path() == other / "u.json"
