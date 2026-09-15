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
import time
from pathlib import Path

import opennano_config as cfg

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
    """服务端签名密钥：首次用时生成（0600）。**不进 git、不进记忆库**。"""
    p = _secret_path()
    if p.exists():
        try:
            raw = p.read_text(encoding="utf-8").strip()
            if raw:
                return bytes.fromhex(raw)
        except OSError:
            pass
    p.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_bytes(32)
    p.write_text(key.hex(), encoding="utf-8")
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    return key


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
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2),
                             encoding="utf-8")
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

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

    # ---- 写 ----
    def add(self, username: str, name: str, password: str, role: str = "member") -> dict:
        un = (username or "").strip()
        if not un:
            raise AuthError("用户名不能为空")
        if self.by_username(un):
            raise AuthError(f"用户名已存在：{un}")
        if role not in ("admin", "member"):
            raise AuthError("角色只能是 admin / member")
        u = {"id": f"u_{secrets.token_hex(4)}", "username": un,
             "name": (name or "").strip() or un, "role": role, "active": True,
             "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"), **hash_password(password)}
        self.data.setdefault("users", []).append(u)
        self._save()
        return u

    def set_password(self, uid: str, password: str) -> None:
        u = self.by_id(uid)
        if not u:
            raise AuthError("账号不存在")
        u.update(hash_password(password))
        self._save()

    def patch(self, uid: str, **kw) -> dict:
        u = self.by_id(uid)
        if not u:
            raise AuthError("账号不存在")
        for k in ("name", "role", "active"):
            if k in kw and kw[k] is not None:
                if k == "role" and kw[k] not in ("admin", "member"):
                    raise AuthError("角色只能是 admin / member")
                u[k] = kw[k]
        self._save()
        return u

    def verify(self, username: str, password: str) -> dict:
        u = self.by_username(username)
        if not u or not verify_password(password, u):
            raise AuthError("用户名或口令不对")
        if not u.get("active", True):
            raise AuthError("账号已停用")
        return u


# ---------------------------------------------------------------- 令牌

def issue_token(uid: str) -> str:
    exp = int(time.time()) + SESSION_TTL
    body = f"{uid}.{exp}"
    sig = hmac.new(_server_secret(), body.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{body}.{sig}"


def read_token(token: str) -> str:
    """→ uid；无效/过期 → 空串（**不抛**，中间件里要能安静地放行到 401）。"""
    parts = (token or "").split(".")
    if len(parts) != 3:
        return ""
    uid, exp, sig = parts
    body = f"{uid}.{exp}"
    want = hmac.new(_server_secret(), body.encode(), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(sig, want):
        return ""
    try:
        if int(exp) < time.time():
            return ""
    except ValueError:
        return ""
    return uid


def user_from_request(request) -> dict | None:
    """从 cookie 解出当前用户（不查库？—— 查，好让"停用账号"立刻生效）。"""
    tok = request.cookies.get(COOKIE_NAME) or ""
    uid = read_token(tok)
    if not uid:
        return None
    u = AccountStore().by_id(uid)
    if not u or not u.get("active", True):
        return None
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
    u = user_from_request(request)
    return {
        "auth_mode": auth_mode(),
        "auth_required": enforcement_needed(),
        "needs_setup": (not store.users()) and not store.load_error,
        "user_count": len(store.active()),
        "user": AccountStore.public(u) if u else None,
        "error": store.load_error,
    }
