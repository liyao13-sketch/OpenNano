"""凭据卫生守卫（2026-09-13 审计后新建）。

起因：审计发现 `server/.env` 里有形如**真实** DeepSeek key 的明文（`sk-` + 35 字符）。
当时核实的结论（可复跑）：**它没有进过 Git** —— `server/.gitignore` 已忽略 `.env`、
`git ls-files server/.env` 为空、`git log --all -- server/.env` 为空、
已跟踪文件里也扫不到 `sk-…` 形态。所以**不需要吊销或改写历史**，风险只剩"以后手滑提交"。

这三条用例就是把"以后手滑"变成**测试会红**：
  1. 已跟踪文件里不许出现真实形态的 key（`.env.example` 那种占位符不算）；
  2. `.gitignore` 里**必须**继续忽略 `.env`（防未来重构把这个保护删掉）；
  3. `.env.example` 只放占位符（它是模板，会进仓库）。

⚠️ 用例**绝不打印**疑似密钥原文，只报「文件:行 + 前 3 位 + 长度」。
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

# ⚠️ 层次别搞错：本文件在 `server/tests/` ⇒ parents[1] = `server/`，**仓库根在再上一层**。
#    第一版写成 parents[1] 当仓库根 ⇒ `.env.example` 路径不存在、被 `skip` 掉 —— **静默假绿**
#    （"跳过≠通过"的同族；已改成"缺文件就红"）。
SERVER = Path(__file__).resolve().parents[1]
ROOT = SERVER.parent

#: 真实形态的 key：`sk-` 后跟 ≥20 位字母数字（`sk-xxxx`、`sk-...`、`${...}` 这类占位符不匹配）
REAL_KEY = re.compile(r"\bsk-[A-Za-z0-9]{20,}")
PLACEHOLDER_OK = re.compile(r"^(sk-x+|sk-\.+|sk-\$\{[^}]*\}|sk-<[^>]*>|)$", re.I)


def _git(*args: str) -> str:
    """跑 git；不可用/非仓库时 skip（CI 上可能是导出目录）。"""
    try:
        r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        pytest.skip("环境里没有可用的 git")
    if r.returncode != 0:
        pytest.skip(f"git {args[0]} 不可用（非仓库？）：{r.stderr.strip()[:80]}")
    return r.stdout


def _mask(tok: str) -> str:
    return f"{tok[:3]}…（长度 {len(tok)}）"


def test_no_real_looking_api_key_in_tracked_files():
    """已跟踪文件里若出现真实形态 key ⇒ 立刻红（此时应**吊销轮换**并清历史，而不是删文件了事）。"""
    files = [f for f in _git("ls-files").splitlines() if f.strip()]
    assert files, "git ls-files 为空，判据失效"
    hits = []
    for rel in files:
        p = ROOT / rel
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except (OSError, UnicodeDecodeError):
            continue
        for i, line in enumerate(text.splitlines(), 1):
            for tok in REAL_KEY.findall(line):
                if PLACEHOLDER_OK.match(tok):
                    continue
                hits.append(f"{rel}:{i}  {_mask(tok)}")
    assert not hits, ("已跟踪文件里出现疑似真实 API key（**别只删文件**：先吊销轮换，再清历史）：\n  "
                      + "\n  ".join(hits[:10]))


def test_dotenv_stays_gitignored():
    """`.env` 必须继续被忽略（这条守住的是"将来有人改 .gitignore"）。"""
    r = subprocess.run(["git", "check-ignore", "-v", "server/.env"],
                       cwd=ROOT, capture_output=True, text=True)
    if r.returncode not in (0, 1):
        pytest.skip("环境里没有可用的 git")
    assert r.returncode == 0, (
        "server/.env 不再被 git 忽略！真实密钥随时可能被提交入历史 —— 请恢复 .gitignore 里的 `.env`")
    assert "server/.gitignore" in r.stdout, f"忽略规则来源可疑：{r.stdout.strip()}"
    # 明确要求那条**显式**规则还在：根 `.gitignore` 是"默认忽略一切"的白名单式写法，
    # 万一将来换成常规写法，就只剩这条显式规则能护住 .env。
    gi = (SERVER / ".gitignore").read_text(encoding="utf-8")
    assert any(l.strip() in (".env", "*.env") for l in gi.splitlines()), \
        "server/.gitignore 里的显式 `.env` 规则被删了"


def test_env_example_holds_only_placeholders():
    """`.env.example` 是**会进仓库**的模板 ⇒ 只允许占位符。"""
    ex = SERVER / ".env.example"
    if not ex.exists():
        # 它**是**被跟踪的文件 ⇒ 不该缺失；缺了就是判据失效，必须红（不许 skip 成假绿）
        pytest.fail(".env.example 不存在或路径算错 ⇒ 这条判据没测到任何东西")
    leaks = []
    for i, line in enumerate(ex.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        val = val.strip().strip("'\"")
        if not val:
            continue
        if REAL_KEY.match(val) and not PLACEHOLDER_OK.match(val):
            leaks.append(f"{ex.name}:{i}  {key.strip()}={_mask(val)}")
    assert not leaks, "`.env.example` 里出现真实形态的值（它是模板，会进仓库）：\n  " + "\n  ".join(leaks)
