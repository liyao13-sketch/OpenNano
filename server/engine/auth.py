"""团队账号与会话（P0 · 2026-09-15 · owner定"每人一个账号"）。

## 为什么有它

方向变了：从"**一个工程师**的记忆"扩到"**一个团队**共享一套会进化的经验记忆"，
用机形态＝**各自笔记本连一台内网服务器**（owner 2026-09-15 选定）。
多人连同一台后端，先要能回答两个问题：**这是谁写的？** · **谁在我之前改过？**
本模块管第一个；第二个见 `main.api_project_save` 的 `_rev` 版本守卫与 `LibraryStore` 的文件守卫。

## 四条口径（照项目零号铁律同款）

1. **不设默认账号、不设默认口令** —— 首次启动由**人**在界面上建第一个管理员。
   有默认口令的部署等于没有身份（而且一定会被扫）。
2. 口令**只存 PBKDF2-HMAC-SHA256 哈希 + 每人独立 salt**，永不回显、永不写日志、永不进 git。
3. 会话＝**HMAC 签名令牌**，HttpOnly cookie；服务端密钥单独一个 `0600` 文件。
   **标准库实现**（不引 passlib/jwt —— 少一个依赖就少一条供应链）。
4. `OPENNANO_AUTH=off` 可关（单人本地/自动化用）；`auto`（默认）＝**存在账号才强制**。
   ⚠️ 关掉只影响"强制登录"，**留痕照记**（actor 记为 `anonymous`）。

## 诚实的边界（必须一起说清）

内网 HTTP 下**口令是明文过网的**：这套东西防的是"**记错人 / 互相覆盖 / 事后说不清**"，
不是防"能抓包的攻击者"。要真防，得配 TLS 或只跑在可信内网 —— 已写进 `docs/deploy_intranet.md`。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sys
import time
from pathlib import Path

import opennano_config as cfg
from .atomic import (create_exclusive, file_lock, lock_backend,
                     write_json_atomic)

#: 口令哈希的默认迭代数（**每次调用现读 env**，这样测试/运维不用重启就能调；
#: 每用户记录自己的迭代数 ⇒ 以后调大不影响老账号）
DEFAULT_ITERS = 200_000


def _default_iters() -> int:
    try:
        return max(1000, int(os.environ.get("OPENNANO_PBKDF2_ITERS") or DEFAULT_ITERS))
    except ValueError:
        return DEFAULT_ITERS


#: 会话有效期（秒）—— 7 天；内网工具，别让工程师天天登录
SESSION_TTL = int(os.environ.get("OPENNANO_SESSION_TTL") or 7 * 24 * 3600)
COOKIE_NAME = "opennano_session"


class AuthError(Exception):
    """可读的账号/会话错误（API 层转 4xx，别漏成 500）。"""


class AlreadyInitialized(AuthError):
    """账号库已有账号 —— 首次初始化只能成功一次（并发下后者必须 409，不是各建一个）。"""


# ---------------------------------------------------------------- 文件与密钥

def _accounts_path() -> Path:
    """⚠️ **每次现读 env**，不用 `opennano_config` 的模块级常量 —— 那是**导入时冻结**的。

    教训（2026-09-15，本模块第一版就踩了）：`cfg.ACCOUNTS_PATH` 在 `opennano_config` 首次 import
    时求值，于是"测试在 fixture 里改 env"根本不起作用 ⇒ **跑一趟用例把真 `~/.opennano/users.json`
    写出来了**（与当年 `PROJECTS_DIR` 把正在用的工程顶掉是同一个坑，见那份 docstring）。
    配置项必须在**用时**读，才谈得上"可覆盖"。
    """
    return Path(os.environ.get("OPENNANO_ACCOUNTS") or cfg.ACCOUNTS_PATH)


def _secret_path() -> Path:
    return Path(os.environ.get("OPENNANO_SERVER_SECRET") or cfg.SERVER_SECRET)


def _server_secret() -> bytes:
    """服务端签名密钥：首次用时**独占创建**（0600）。**不进 git、不进记忆库**。

    ⚠️ A3 审计（2026-09-15）查出两处，这里一并修：
      · **首次生成有竞态**：两个 worker 各生成一份、各写一次 ⇒ 后写的赢，另一个进程用它自己的密钥
        签发的会话在对面全部失效（实测：两进程返回**不同**密钥）。改法＝`O_CREAT|O_EXCL` 独占创建，
        失败方**读对方写的那份**（这是唯一能让两进程一致的顺序）。
      · **坏密钥文件**（不是合法 hex）会让 `bytes.fromhex` 抛 ValueError，进而让**签发会话的请求 500**
        （实测：`/api/auth/setup` 与"口令正确的登录"都是 500）。改法＝**隔离坏文件 + 轮换新密钥 + 出声**
        （与库文件损坏同一套处置：不静默覆盖，但也绝不把整个服务打成 500）。
        代价：所有既有会话失效（密钥换了）⇒ 必须让人知道，故记 audit + 写 stderr + 在 `/api/auth/state` 上报。
    """
    p = _secret_path()
    if p.exists():
        try:
            raw = p.read_text(encoding="utf-8").strip()
            if raw:
                return bytes.fromhex(raw)
        except ValueError:
            _quarantine_secret(p)
        except OSError as e:
            print(f"[auth] 密钥文件读不动（{type(e).__name__}: {e}）：{p}", file=sys.stderr)

    key = secrets.token_bytes(32)
    if create_exclusive(p, key.hex(), 0o600):
        return key
    # 别人先创建成功 ⇒ 必须用**对方的**密钥（否则跨 worker 会话互不认）
    try:
        raw = p.read_text(encoding="utf-8").strip()
        if raw:
            return bytes.fromhex(raw)
    except (OSError, ValueError):
        pass
    raise AuthError(f"密钥文件无法创建也读不出：{p}")     # 出声，不静默用一份临时密钥


#: 上一次因坏文件而轮换密钥的时间（空 = 没发生；给 `/api/auth/state` 上报用）
SECRET_ROTATED_AT = ""


def _quarantine_secret(p: Path) -> None:
    """坏密钥文件**改名留档**（不删）+ 记 audit。新密钥由调用方创建。"""
    global SECRET_ROTATED_AT
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = p.with_name(f"{p.name}.corrupt-{stamp}")
    try:
        p.replace(dest)
    except OSError:
        try:
            p.unlink()                      # 改名失败也绝不带着坏文件往下走
        except OSError:
            pass
    SECRET_ROTATED_AT = time.strftime("%Y-%m-%dT%H:%M:%S")
    print(f"[auth] ⚠️ 密钥文件不是合法 hex ⇒ 已隔离为 {dest.name} 并**轮换新密钥**；"
          f"所有既有登录会话已失效（用户需重新登录）", file=sys.stderr)
    try:
        from . import audit as _audit
        _audit.record("system", "auth.secret.rotated", target=str(p),
                      detail="坏密钥文件被隔离，会话全部失效", ok=False)
    except Exception:                       # noqa: BLE001 —— 留痕失败不许挡住认证
        pass


# ---------------------------------------------------------------- 口令

def hash_password(password: str, *, iters: int | None = None,
                  salt: bytes | None = None) -> dict:
    """→ `{algo, iters, salt, hash}`。**只存这个 dict，绝不存明文。**"""
    if not password or len(password) < 6:
        raise AuthError("口令至少 6 位")
    it = int(iters or _default_iters())
    sd = salt or secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), sd, it)
    return {"algo": "pbkdf2_sha256", "iters": it, "salt": sd.hex(), "hash": dk.hex()}


def verify_password(password: str, rec: dict) -> bool:
    if not rec or rec.get("algo") != "pbkdf2_sha256":
        return False
    try:
        dk = hashlib.pbkdf2_hmac("sha256", (password or "").encode("utf-8"),
                                 bytes.fromhex(rec["salt"]), int(rec["iters"]))
    except (KeyError, ValueError):
        return False
    return hmac.compare_digest(dk.hex(), str(rec.get("hash") or ""))


#: 未知用户名也要跑一次 PBKDF2（见 `AccountStore.verify`）。懒构造：真用时才算。
_DUMMY_RECORD: dict | None = None


def _dummy_record() -> dict:
    global _DUMMY_RECORD
    if _DUMMY_RECORD is None:
        _DUMMY_RECORD = hash_password("dummy-password-for-timing", iters=_default_iters())
    return _DUMMY_RECORD


# ---------------------------------------------------------------- 账号库

class AccountStore:
    """账号库（一个 JSON 文件）。读不动**绝不静默重建** —— 与 `LibraryStore` 同一条教训：
    损坏的账号文件被默认值顶掉，等于把全组的身份清空（而且没人知道）。"""

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else _accounts_path()
        self.data: dict = {"version": 1, "users": []}
        self.load_error = ""
        self.save_blocked = False
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            self.data.update(json.loads(self.path.read_text(encoding="utf-8")))
        except Exception as e:  # noqa: BLE001
            self.load_error = f"{type(e).__name__}: {e}"
            self.save_blocked = True          # 读不动就不许写：不许用空账号库顶掉它

    def _save(self) -> None:
        if self.save_blocked:
            raise AuthError(f"账号文件读不动（{self.load_error}）⇒ 拒绝写入，请先处置该文件")
        write_json_atomic(self.path, self.data, 0o600)      # 原子替换，不留半截 JSON

    # ---- 写入的唯一入口：**锁内重读 → 校验 → 修改 → 原子落盘** ----
    def _mutate(self, fn):
        """A3 审计（2026-09-15）的修法：三处缺陷（丢号 / 双管理员 / 部分提交）根因相同 ——
        读—改—写不是原子的。所以**所有写入都从这里走**，不许任何方法自己 `_save()`。

        为什么必须在锁内**重读**：内存里的 `self.data` 可能是几百毫秒前的快照，
        直接拿它覆盖 = 把别人刚写的那条抹掉（实测 100/100 轮丢号）。锁内重读后，
        并发写变成串行"读最新 → 改 → 写"，两条都能留下。
        """
        with file_lock(self.path):
            self.load_error = ""
            self.save_blocked = False
            self.data = {"version": 1, "users": []}
            self._load()                                     # 锁内重读
            if self.save_blocked:
                raise AuthError(f"账号文件读不动（{self.load_error}）⇒ 拒绝写入，请先处置该文件")
            out = fn()                                        # 校验 + 修改（抛错 ⇒ 一个字节都不写）
            self._save()
            return out

    # ---- 读 ----
    def users(self) -> list[dict]:
        return list(self.data.get("users") or [])

    def active(self) -> list[dict]:
        return [u for u in self.users() if u.get("active", True)]

    def by_username(self, username: str) -> dict | None:
        u0 = (username or "").strip().lower()
        return next((u for u in self.users() if (u.get("username") or "").lower() == u0), None)

    def by_id(self, uid: str) -> dict | None:
        return next((u for u in self.users() if u.get("id") == uid), None)

    @staticmethod
    def public(u: dict) -> dict:
        """对外表示：**绝不含 salt/hash**。"""
        return {"id": u.get("id"), "username": u.get("username"), "name": u.get("name") or "",
                "role": u.get("role") or "member", "active": bool(u.get("active", True)),
                "created_at": u.get("created_at") or ""}

    # ---- 写（全部经 `_mutate`）----
    def add(self, username: str, name: str, password: str, role: str = "member") -> dict:
        un = (username or "").strip()
        if not un:
            raise AuthError("用户名不能为空")
        if role not in ("admin", "member"):
            raise AuthError("角色只能是 admin / member")
        rec = {**hash_password(password)}                # 口令哈希在锁外算（慢，且与账号表无关）

        def _do():
            if self.by_username(un):                     # **锁内**重名校验：并发 add 同名只会有一个成功
                raise AuthError(f"用户名已存在：{un}")
            u = {"id": f"u_{secrets.token_hex(4)}", "username": un,
                 "name": (name or "").strip() or un, "role": role, "active": True,
                 "session_version": 1,                   # 会话版本：递增即让旧 cookie 立即失效
                 "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"), **rec}
            self.data.setdefault("users", []).append(u)
            return u
        return self._mutate(_do)

    def bootstrap_admin(self, username: str, name: str, password: str) -> dict:
        """**首次初始化**（只能成功一次）。判空与建号在**同一把锁内**完成 ——
        A3 审计 P0-2：原来"先判空、再 add"分两步，两个并发请求会各建一个管理员（实测 30/30 双 200）。
        """
        un = (username or "").strip()
        if not un:
            raise AuthError("用户名不能为空")
        rec = {**hash_password(password)}

        def _do():
            if self.users():                             # **锁内**再判一次：谁先到谁赢，后者 409
                raise AlreadyInitialized("已经初始化过了（已有账号）；请让管理员加号")
            u = {"id": f"u_{secrets.token_hex(4)}", "username": un,
                 "name": (name or "").strip() or un, "role": "admin", "active": True,
                 "session_version": 1,
                 "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"), **rec}
            self.data.setdefault("users", []).append(u)
            return u
        return self._mutate(_do)

    def set_password(self, uid: str, password: str) -> dict:
        rec = {**hash_password(password)}

        def _do():
            u = self.by_id(uid)
            if not u:
                raise AuthError("账号不存在")
            u.update(rec)
            u["session_version"] = int(u.get("session_version") or 1) + 1   # 旧 cookie 立即失效
            return u
        return self._mutate(_do)

    def patch(self, uid: str, password: str | None = None, **kw) -> dict:
        """改名 / 改角色 / 停用 / 重置口令 —— **一次校验、一次落盘**。

        A3 审计可疑点 3：原来是 `set_password()` + `patch()` **两次落盘** ⇒ 提交
        `{"password": 新口令, "role": 非法}` 会**返回 400 但口令已经改了**（实测：新口令能登录）。
        现在全部先校验、再改、最后写一次 ⇒ 校验失败则一个字节都不落。
        """
        rec = {**hash_password(password)} if password else None

        def _do():
            u = self.by_id(uid)
            if not u:
                raise AuthError("账号不存在")
            for k in ("name", "role", "active"):                 # 先校验（任何非法 ⇒ 抛，不写）
                if k in kw and kw[k] is not None and k == "role" and kw[k] not in ("admin", "member"):
                    raise AuthError("角色只能是 admin / member")
            bump = False
            if rec:
                u.update(rec)
                bump = True
            for k in ("name", "role", "active"):
                if k in kw and kw[k] is not None and u.get(k) != kw[k]:
                    u[k] = kw[k]
                    if k in ("role", "active"):                  # 降权/停用 ⇒ 旧会话立即失效
                        bump = True
            if bump:
                u["session_version"] = int(u.get("session_version") or 1) + 1
            return u
        return self._mutate(_do)

    def verify(self, username: str, password: str) -> dict:
        """校验口令。

        ⚠️ A3 审计 P1-2：用户名不存在时**不跑** PBKDF2、存在时跑满 ⇒ 实测中位数差 **14 ms**
        （15.8×），可远程枚举用户名。修法＝用户名不存在/记录不合法时也跑一次**固定参数的 dummy**
        PBKDF2，让两条路径的计算量对齐。响应文案本来就相同，这里对齐的是**耗时**。
        """
        u = self.by_username(username)
        if not u or u.get("algo") != "pbkdf2_sha256":
            verify_password(password, _dummy_record())           # 与真校验同量级，忽略结果
            raise AuthError("用户名或口令不对")
        if not verify_password(password, u):
            raise AuthError("用户名或口令不对")
        if not u.get("active", True):
            raise AuthError("账号已停用")
        return u


# ---------------------------------------------------------------- 令牌

def issue_token(uid: str, session_version: int = 1) -> str:
    """令牌 = `uid.sv.exp.sig`。

    ⚠️ **`sv`（会话版本）是 A3 审计 P1-1 的修法**：原来令牌只绑 `uid + exp`，于是
    **管理员重置口令后，旧 cookie 还能用满 7 天**（实测：重置后旧会话仍 200）。
    把用户的 `session_version` 签进令牌，改口令 / 改角色 / 停用时递增 ⇒ **旧 cookie 立即失效**。
    """
    exp = int(time.time()) + SESSION_TTL
    body = f"{uid}.{int(session_version)}.{exp}"
    sig = hmac.new(_server_secret(), body.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{body}.{sig}"


def read_token(token: str) -> tuple[str, int]:
    """→ `(uid, session_version)`；无效/过期 → `("", 0)`（**不抛**，中间件里要安静地放行到 401）。"""
    parts = (token or "").split(".")
    if len(parts) != 4:
        return "", 0
    uid, sv, exp, sig = parts
    body = f"{uid}.{sv}.{exp}"
    try:
        want = hmac.new(_server_secret(), body.encode(), hashlib.sha256).hexdigest()[:32]
    except AuthError:
        return "", 0                     # 密钥文件坏到无法读写 ⇒ 当未登录（不 500）
    if not hmac.compare_digest(sig, want):
        return "", 0
    try:
        if int(exp) < time.time():
            return "", 0
        return uid, int(sv)
    except ValueError:
        return "", 0


def user_from_request(request) -> dict | None:
    """从 cookie 解出当前用户。

    每次请求都查库（**故意的**）：这样"停用账号"与"改口令/降权"能**立刻**生效。
    ⚠️ 别为了省 IO 在这里加缓存 —— 审计稿专门点了这条（缓存会让撤销失效）。
    """
    tok = request.cookies.get(COOKIE_NAME) or ""
    uid, sv = read_token(tok)
    if not uid:
        return None
    u = AccountStore().by_id(uid)
    if not u or not u.get("active", True):
        return None
    if int(u.get("session_version") or 1) != sv:
        return None                     # 口令/角色/启用状态变过 ⇒ 旧会话作废
    return u


def actor_of(request) -> str:
    """留痕用的人名：登录者用 `username`，没有则 `anonymous`（`AUTH=off` 时也照记）。"""
    st = getattr(request, "state", None)
    if st is not None and getattr(st, "actor", None):
        return st.actor
    u = user_from_request(request)
    return (u or {}).get("username") or "anonymous"


# ---------------------------------------------------------------- 强制策略

def auth_mode() -> str:
    return (os.environ.get("OPENNANO_AUTH") or "auto").strip().lower()


def enforcement_needed() -> bool:
    """`on` 一律强制；`off` 一律不强制；`auto`＝**有账号才强制**（首次部署不锁门）。

    ⚠️ `auto` 的边界要记住：**没建账号时服务是全开的** —— 所以首次部署第一件事就是建管理员
    （界面会直接进"创建管理员"，`/api/auth/state` 的 `needs_setup` 告诉前端）。
    """
    m = auth_mode()
    if m == "on":
        return True
    if m == "off":
        return False
    try:
        return len(AccountStore().active()) > 0
    except Exception:  # noqa: BLE001 —— 账号文件坏掉时**宁可要求登录**（失败要可见）
        return True


#: 免登录路径（登录/初始化/健康检查；前端静态资源不在 /api 下，由中间件另判）
PUBLIC_PATHS = ("/api/auth/state", "/api/auth/setup", "/api/auth/login", "/api/health")


def is_public(path: str) -> bool:
    if not path.startswith("/api"):
        return True
    return any(path == p or path.startswith(p + "/") for p in PUBLIC_PATHS)


def state_for(request) -> dict:
    """给前端的一站式状态：要不要登录、要不要初始化、当前是谁。"""
    store = AccountStore()
    # ⚠️ E2E 暴露的缺口（2026-09-15 自查）：不带 cookie 的请求**根本不碰密钥**
    #    （`read_token("")` 提前返回）⇒ 页面首次加载时看不到"密钥已轮换、会话全失效"，
    #    而那正是最该看到它的时候。这里**主动探一次密钥**：坏文件在此被隔离轮换，
    #    而不是等到用户点登录时才 500（或轮换后无人知晓）。
    try:
        _server_secret()
    except AuthError:
        pass
    u = user_from_request(request)
    return {
        "auth_mode": auth_mode(),
        "auth_required": enforcement_needed(),
        "needs_setup": (not store.users()) and not store.load_error,
        "user_count": len(store.active()),
        "user": AccountStore.public(u) if u else None,
        "error": store.load_error,
        # 密钥文件坏过 ⇒ 已轮换（既有会话全部失效）—— 必须让界面说出来
        "secret_rotated_at": SECRET_ROTATED_AT,
        # A3 审计可疑点 4 的建议：认证关闭 + 非回环监听时，把"这扇门是开的"显式告诉人
        "open_to_network": _open_to_network(),
    }


def listen_host() -> str:
    """当前监听地址（只从启动参数/环境推断；推断不出返回空串 = 未知）。

    用途只有一个：判断"认证关闭时这扇门是不是开在整个局域网上"。
    """
    env = os.environ.get("OPENNANO_HOST")
    if env:
        return env
    argv = sys.argv
    for i, a in enumerate(argv):
        if a == "--host" and i + 1 < len(argv):
            return argv[i + 1]
        if a.startswith("--host="):
            return a.split("=", 1)[1]
    return ""


def open_to_network() -> bool:
    """**认证不强制** 且 监听地址不是回环 ⇒ 局域网内任何人可改数据（含未建账号的默认态）。"""
    if enforcement_needed():
        return False
    host = listen_host()
    if not host:
        return False                     # 推断不出就别吓人（默认 uvicorn 是 127.0.0.1）
    return host not in ("127.0.0.1", "localhost", "::1")


def _open_to_network() -> bool:
    try:
        return open_to_network()
    except Exception:                    # noqa: BLE001 —— 只是告警，不许把 /api/auth/state 打崩
        return False
