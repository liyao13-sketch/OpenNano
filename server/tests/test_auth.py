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


# ================================================================
# A3 审计（5.6sol，2026-09-15）查出的缺陷 —— 每条都先复现红、再修、再钉判据
#
# 复现证据（修前，原脚本）：
#   P0-1 并发 add      → 100/100 轮丢号
#   P0-2 并发 setup    → 30/30 轮双 200（文件里只留一个管理员）
#   P1-1 重置口令      → 旧 cookie 仍 200（期望 401）
#   P1-2 未知用户名    → 中位 1.0ms vs 存在 15.0ms（**15.8×**，可远程枚举）
#   可疑1 密钥首次生成 → 两进程返回**不同**密钥（跨 worker 会话随机失效）
#   可疑2 坏密钥文件   → 口令正确的登录 **500**
#   可疑3 patch 两次落盘 → 返回 400 但新口令已生效
# 修后同一批脚本全绿。下面这些用例是它们的常驻版本。
# ================================================================

def test_concurrent_adds_do_not_lose_accounts(tmp_path):
    """**P0-1**：并发 `add` 不许丢号（修前：内存快照整份覆盖 ⇒ 100% 丢一个）。"""
    import threading
    from engine.auth import AccountStore
    from engine.atomic import file_lock                     # noqa: F401（顺带确认模块在）
    for n in range(20):
        p = tmp_path / f"users_{n}.json"
        AccountStore(p).add("admin", "", "admin_pass", "admin")
        barrier = threading.Barrier(2)
        errs: list[str] = []

        def _add(name):
            try:
                st = AccountStore(p)
                barrier.wait()
                st.add(name, "", "member_pass", "member")
            except Exception as e:                          # noqa: BLE001
                errs.append(repr(e))

        ts = [threading.Thread(target=_add, args=(x,)) for x in ("alice", "bob")]
        [t.start() for t in ts]
        [t.join() for t in ts]
        names = {u["username"] for u in AccountStore(p).users()}
        assert names == {"admin", "alice", "bob"}, f"第 {n} 轮丢号：{names} {errs}"


def test_concurrent_setup_yields_exactly_one_admin(tmp_path, monkeypatch):
    """**P0-2**：并发 `setup` 必须**恰好一个 200、一个 409**（修前：两个都 200）。"""
    import concurrent.futures
    monkeypatch.setenv("OPENNANO_AUTH", "on")
    monkeypatch.setenv("OPENNANO_ACCOUNTS", str(tmp_path / "users.json"))
    monkeypatch.setenv("OPENNANO_AUDIT", str(tmp_path / "audit.log"))
    monkeypatch.setenv("OPENNANO_SERVER_SECRET", str(tmp_path / ".sec"))
    import main
    from fastapi.testclient import TestClient

    def _setup(name):
        with TestClient(main.app) as c:
            return c.post("/api/auth/setup", json={"username": name, "name": name,
                                                  "password": "password_1"}).status_code

    for n in range(8):
        (tmp_path / "users.json").unlink(missing_ok=True)   # 每轮重置成"未初始化"
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
            codes = sorted(ex.map(_setup, ["admin_a", "admin_b"]))
        assert codes == [200, 409], f"第 {n} 轮契约被突破：{codes}"
        admins = [u for u in json.loads((tmp_path / "users.json").read_text(encoding="utf-8"))["users"]
                  if u["role"] == "admin"]
        assert len(admins) == 1, f"第 {n} 轮建出了 {len(admins)} 个管理员"


def test_password_reset_revokes_the_old_session(team):
    """**P1-1**：管理员重置口令 ⇒ 旧 cookie **立即**失效（修前：还能用满 7 天）。"""
    c = team["client"]
    _setup(c)
    _add_member(team, c)
    member = TestClient(team["main"].app, raise_server_exceptions=False)
    member.post("/api/auth/login", json={"username": "bob", "password": "member_pass_1"})
    assert member.get("/api/library").status_code == 200
    uid = next(u["id"] for u in c.get("/api/auth/users").json()["users"] if u["username"] == "bob")
    assert c.post(f"/api/auth/users/{uid}", json={"password": "brand_new_1"}).status_code == 200
    assert member.get("/api/library").status_code == 401, "旧 cookie 在改口令后仍然可用"


def test_role_change_revokes_the_old_session(team):
    """降权/提权也递增会话版本（避免"降权了但手里的 cookie 还是管理员"）。"""
    c = team["client"]
    _setup(c)
    _add_member(team, c)
    member = TestClient(team["main"].app, raise_server_exceptions=False)
    member.post("/api/auth/login", json={"username": "bob", "password": "member_pass_1"})
    uid = next(u["id"] for u in c.get("/api/auth/users").json()["users"] if u["username"] == "bob")
    assert c.post(f"/api/auth/users/{uid}", json={"role": "admin"}).status_code == 200
    assert member.get("/api/library").status_code == 401


def test_unknown_username_still_runs_the_kdf(team, monkeypatch):
    """**P1-2**：用户名不存在时也必须跑一次 PBKDF2（修前不跑 ⇒ 实测 15.8× 耗时差，可枚举用户名）。

    判据用**确定性**的调用计数（不靠掐时间 —— 那种判据在 CI 上会假红）：
    未知用户名 ⇒ `verify_password` **照样被调用一次**。
    """
    from engine import auth as A
    calls: list[str] = []
    real = A.verify_password

    def counting(pw, rec):
        calls.append(rec.get("algo", "?"))
        return real(pw, rec)

    monkeypatch.setattr(A, "verify_password", counting)
    with pytest.raises(A.AuthError):
        A.AccountStore().verify("definitely_missing_user", "whatever_pass")
    assert calls == ["pbkdf2_sha256"], f"未知用户名没跑 dummy PBKDF2：{calls}"


def test_secret_file_is_created_exclusively_and_never_overwritten(tmp_path, monkeypatch):
    """**可疑点 1**：密钥文件已存在时**绝不许覆盖**（修前两个进程各写一份 ⇒ 会话互不认）。"""
    from engine import auth as A
    p = tmp_path / "secret"
    monkeypatch.setenv("OPENNANO_SERVER_SECRET", str(p))
    first = A._server_secret()
    p.write_text(first.hex(), encoding="utf-8")
    assert A._server_secret() == first                    # 第二次读到的还是同一把
    assert p.read_text(encoding="utf-8").strip() == first.hex()
    (tmp_path / "secret").unlink()
    again = A._server_secret()                            # 真没有时才创建
    assert len(again) == 32


def test_corrupt_secret_is_quarantined_and_does_not_500(tmp_path, monkeypatch):
    """**可疑点 2**：密钥文件不是合法 hex ⇒ 隔离 + 轮换，**不许 500**（修前 setup/登录都 500）。"""
    from engine import auth as A
    monkeypatch.setenv("OPENNANO_AUTH", "on")
    monkeypatch.setenv("OPENNANO_ACCOUNTS", str(tmp_path / "users.json"))
    monkeypatch.setenv("OPENNANO_AUDIT", str(tmp_path / "audit.log"))
    monkeypatch.setenv("OPENNANO_SERVER_SECRET", str(tmp_path / "secret"))
    import main
    c = TestClient(main.app, raise_server_exceptions=False)
    assert c.post("/api/auth/setup", json={"username": "admin", "name": "",
                                          "password": "admin_pass"}).status_code == 200
    (tmp_path / "secret").write_text("not-hex", encoding="utf-8")      # 事后损坏
    fresh = TestClient(main.app, raise_server_exceptions=False)
    r = fresh.post("/api/auth/login", json={"username": "admin", "password": "admin_pass"})
    assert r.status_code == 200, f"坏密钥文件把登录打成了 {r.status_code}"
    assert fresh.get("/api/library").status_code == 200
    assert list(tmp_path.glob("secret.corrupt-*")), "坏密钥文件没有被隔离留档"
    assert "auth.secret.rotated" in (tmp_path / "audit.log").read_text(encoding="utf-8")
    # 无 cookie 的 `/api/auth/state`（= 页面首次加载）也必须报出来，否则用户永远不知道被登出了
    st = TestClient(main.app, raise_server_exceptions=False).get("/api/auth/state").json()
    assert st["secret_rotated_at"], "页面首次加载看不到密钥轮换"


def test_patch_is_all_or_nothing(team):
    """**可疑点 3**：一次请求里的多项修改要么全成、要么全不成（修前：报 400 但口令已改）。"""
    c = team["client"]
    _setup(c)
    _add_member(team, c)
    uid = next(u["id"] for u in c.get("/api/auth/users").json()["users"] if u["username"] == "bob")
    r = c.post(f"/api/auth/users/{uid}", json={"password": "new_pass_1", "role": "invalid_role"})
    assert r.status_code == 400
    assert TestClient(team["main"].app).post(
        "/api/auth/login", json={"username": "bob", "password": "member_pass_1"}
    ).status_code == 200, "报了 400，但旧口令已经不能用了（部分提交）"
    assert TestClient(team["main"].app).post(
        "/api/auth/login", json={"username": "bob", "password": "new_pass_1"}
    ).status_code == 401, "报了 400，但新口令已经生效（部分提交）"


def test_accounts_file_write_is_atomic_and_private(tmp_path):
    """写入要**原子**（不留半截 JSON、不留临时文件）且权限 0600。"""
    from engine.auth import AccountStore
    p = tmp_path / "users.json"
    AccountStore(p).add("alice", "", "alice_pass", "admin")
    assert json.loads(p.read_text(encoding="utf-8"))["users"][0]["username"] == "alice"
    assert oct(p.stat().st_mode)[-3:] == "600"
    assert not list(tmp_path.glob("*.tmp")), "原子写留下了临时文件"


def test_open_to_network_flag_when_auth_is_off(team, monkeypatch):
    """**可疑点 4 的建议**：认证不强制 + 非回环监听 ⇒ 状态里要报出来（界面据此弹告警）。"""
    from engine import auth as A
    monkeypatch.setenv("OPENNANO_AUTH", "off")
    monkeypatch.setenv("OPENNANO_HOST", "0.0.0.0")
    assert A.open_to_network() is True
    assert team["client"].get("/api/auth/state").json()["open_to_network"] is True
    monkeypatch.setenv("OPENNANO_HOST", "127.0.0.1")
    assert A.open_to_network() is False
