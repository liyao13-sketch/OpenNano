"""原子写与跨进程文件锁（2026-09-15 · A3 审计后新建）。

## 为什么单独一个模块

审计（5.6sol）查出账号文件有三处同源缺陷：**整份覆盖写**（丢号）· **判空与写之间无锁**（双管理员）
· **多 worker 首次生成密钥各写各的**。它们的根因是**同一个**：*读—改—写不是原子的*。
所以修法也是一处：**锁内重读 → 校验 → 修改 → 临时文件 + fsync → os.replace 原子替换**。
分别打补丁必然漏（这正是审计稿第 4 条建议："不要分别打补丁"）。

## 两条纪律

1. **锁是跨进程的**（`fcntl.flock`）——只加 `threading.Lock` 挡不住多 worker / 多进程（审计稿原话）。
2. **写是原子的**（同目录临时文件 + `os.replace`）——`write_text` 整份覆盖在并发下会丢改动，
   崩溃时还可能留下半截 JSON。

⚠️ **平台**：`fcntl` 在 macOS/Linux 有；没有它时退化为"仅进程内锁 + 原子替换"，
并把这件事**说出来**（`lock_backend()`），不假装有跨进程保护。
"""
from __future__ import annotations

import json
import os
import secrets
import threading
from contextlib import contextmanager
from pathlib import Path

try:                                    # POSIX（macOS/Linux）
    import fcntl
    _HAVE_FCNTL = True
except ImportError:                     # Windows 等
    fcntl = None                        # type: ignore[assignment]
    _HAVE_FCNTL = False

_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def lock_backend() -> str:
    """当前跨进程锁后端：`flock` 或 `thread-only`（后者**不是**跨进程保护）。"""
    return "flock" if _HAVE_FCNTL else "thread-only"


def _thread_lock(key: str) -> threading.Lock:
    with _LOCKS_GUARD:
        lk = _LOCKS.get(key)
        if lk is None:
            lk = _LOCKS[key] = threading.Lock()
        return lk


@contextmanager
def file_lock(path: Path, timeout: float = 10.0):
    """对 `path` 加**跨进程**排他锁（锁文件 = `<path>.lock`）。

    进程内先过一把 `threading.Lock`（同一进程的多个线程不会互相 fake 成功），
    再抢 `flock`（跨进程）。`timeout` 到点抛 `TimeoutError` —— **宁可失败出声，也不要静默继续写**。
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    lk = _thread_lock(str(p))
    if not lk.acquire(timeout=timeout):
        raise TimeoutError(f"取进程内锁超时：{p}")
    fd = None
    try:
        if _HAVE_FCNTL:
            lock_path = p.with_name(p.name + ".lock")
            fd = os.open(lock_path, os.O_WRONLY | os.O_CREAT, 0o600)
            deadline = timeout
            while True:                      # flock 没有超时参数 ⇒ 自己轮询
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    deadline -= 0.05
                    if deadline <= 0:
                        raise TimeoutError(f"取跨进程锁超时：{lock_path}") from None
                    import time as _t
                    _t.sleep(0.05)
        yield
    finally:
        if fd is not None:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)
        lk.release()


def write_json_atomic(path: Path, data, mode: int = 0o600) -> None:
    """**原子**写 JSON：同目录临时文件 → `fsync` → `os.replace`。

    `os.replace` 在同一文件系统上是原子的 ⇒ 读者只会看到"旧的完整版"或"新的完整版"，
    **不会看到半截 JSON**（审计稿 P0-1 担心的第二种后果）。
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, p)
        try:                                  # 目录项也落盘（崩溃后不丢"改名"这一步）
            dfd = os.open(p.parent, os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        except OSError:
            pass
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def write_text_atomic(path: Path, text: str, mode: int = 0o600) -> None:
    """同上，写纯文本（密钥文件用）。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, p)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def create_exclusive(path: Path, text: str, mode: int = 0o600) -> bool:
    """**独占创建**（`O_CREAT|O_EXCL`）：成功返回 True；已存在返回 False（不覆盖）。

    密钥首次生成用这个 —— 两个 worker 同时起跑时，只有一个能创建成功，
    另一个必须**读对方写的那份**（否则各自签发的会话在对方那里全部失效）。
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    except FileExistsError:
        return False
    try:
        os.write(fd, text.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    return True
