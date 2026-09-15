"""公开层卫生（2026-09-15 · 脱敏单之后新增）—— 公开仓库里**不许新增身份指纹**。

## 为什么有它

脱敏（工单 `20260915-助手线-to-工具线-01`）把**既有**的真名/夹具/内部语境清掉了，但有两件事没有机制守着：

1. **回流**：以后谁顺手在注释里写回真名/助手名/内部文件名，没人会发现（公开仓库是自动公开的）；
2. **增长**：机台清单/厂商/型号这批（`TOOL_DISPLAY` 13 键值 + 库内 seed）是**已知的、待外置**的债
   （另单 `20260915-助手线-to-兼-01`）—— 债可以留，但**不许变多**。

所以本判据是**棘轮**：命中数 ≤ 上限，**上限只许往下压**。

## 两层设计（为什么表不在仓库里）

- **L1 · 结构性**（不需要外部输入，本机与 CI 都能跑）：不许出现 `/Users/…` 绝对路径、
  不许把 `.pyc`/替换表/bundle 之类产物提交进来。
- **L2 · 指纹表**：真机台名/厂商/人名那张表**本身也是指纹** ⇒ 放在工作区
  （`小镌记忆库/工具/公开层禁词.txt`），**读不到就跳过**（评测/CI 环境），跳过原因写明这一点。
  ⚠️ 本文件里**不得写任何禁词字面量**，否则判据自己就犯了它要判的事。

## 判据自己也要验一遍（"体检器会说谎"的教训）

`test_scanner_actually_flags_a_violation` 拿**合成违例**跑一遍扫描器 —— 若扫描器是空转的
（比如读不到文件就当通过），这条会红。**只在本机绿、在别处不响的判据，等于没有。**
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from conftest import REPO, WS_ROOT

#: 指纹表位置：环境变量优先，其次工作区默认位（**表不进仓库**）
DENYLIST_ENV = "OPENNANO_PUBLIC_DENYLIST"
DENYLIST_DEFAULT = WS_ROOT / "个人空间/99_助手文件/小镌记忆库/工具/公开层禁词.txt"


def _denylist_path() -> Path:
    return Path(os.environ.get(DENYLIST_ENV) or DENYLIST_DEFAULT)


def _tracked_files() -> list[str]:
    out = subprocess.run(["git", "-C", str(REPO), "ls-files"],
                         capture_output=True, text=True, check=True).stdout
    return [p for p in out.split("\n") if p.strip()]


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None                       # 二进制/读不动 ⇒ 跳过（不是"通过"）


def load_denylist(path: Path | None = None) -> list[tuple[str, int, str]]:
    """→ [(模式, 允许上限, 说明)]。`#` 注释与空行忽略；缺 TAB 上限的行当 0 处理。"""
    p = Path(path or _denylist_path())
    rows: list[tuple[str, int, str]] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        word = parts[0].strip()
        if not word:
            continue
        try:
            limit = int(parts[1]) if len(parts) > 1 and parts[1].strip() else 0
        except ValueError:
            limit = 0
        rows.append((word, limit, parts[2].strip() if len(parts) > 2 else ""))
    return rows


def scan(files: list[str], rows: list[tuple[str, int, str]], root: Path = REPO) -> list[str]:
    """→ 违规描述清单（空 ＝ 全过）。**纯函数**，好让自测拿合成输入验它。"""
    texts: dict[str, str] = {}
    for rel in files:
        t = _read(Path(root) / rel)
        if t is not None:
            texts[rel] = t
    bad: list[str] = []
    for word, limit, note in rows:
        hits = {rel: t.count(word) for rel, t in texts.items() if word in t}
        total = sum(hits.values())
        if total > limit:
            worst = sorted(hits.items(), key=lambda kv: -kv[1])[:3]
            where = " · ".join(f"{f}×{n}" for f, n in worst)
            bad.append(f"`{word}` 命中 {total} > 上限 {limit}（{note}）｜主要落点：{where}")
    return bad


# ---------------------------------------------------------------- L1 结构性

def test_no_absolute_home_paths_are_tracked():
    """代码里不许出现 `/Users/<某人>/…` 绝对路径（会把用户名写进公网）。"""
    hits = [f for f in _tracked_files()
            if (t := _read(REPO / f)) and "/Users/" in t]
    assert hits == [], f"公开仓库里有绝对家目录路径：{hits[:5]}"


def test_no_artifacts_are_committed():
    """构建/临时产物不许入库（`.pyc`、替换表、bundle、ds_store）。"""
    bad = [f for f in _tracked_files()
           if f.endswith((".pyc", ".pyo"))
           or "__pycache__" in f
           or Path(f).name in {"replacements.txt", ".DS_Store"}
           or f.endswith(".bundle")]
    assert bad == [], f"产物被提交进来了：{bad[:5]}"


# ---------------------------------------------------------------- L2 指纹棘轮

def test_scanner_actually_flags_a_violation(tmp_path):
    """**判据自验**：扫描器必须真的会响 —— 拿合成违例跑，期望报出违规。

    为什么要这条：本判据在「读不到指纹表」时会跳过；如果扫描器写错了（例如把
    `total > limit` 写成 `total >= limit` 之外的笔误、或读不到文件就当通过），
    跳过的路径会掩盖它。这条用合成输入把扫描逻辑钉死。
    """
    d = tmp_path / "synthetic"; d.mkdir()
    (d / "code.py").write_text("x = 'FORBIDDEN_SAMPLE_TOKEN'\n", encoding="utf-8")
    rows = [("FORBIDDEN_SAMPLE_TOKEN", 0, "自测用")]
    assert scan(["code.py"], rows, root=d), "扫描器没报违规 —— 判据是空转的"
    ok = scan(["code.py"], [("FORBIDDEN_SAMPLE_TOKEN", 1, "")], root=d)
    assert ok == [], "上限内不该报违规（否则是假红）"


def test_public_layer_fingerprints_do_not_grow():
    """**棘轮**：指纹命中数不得超过上限（上限只许往下压；表不进仓库，读不到就跳过）。"""
    p = _denylist_path()
    if not p.exists():
        pytest.skip(f"本机没有公开层禁词表（{p}）—— 该表本身是指纹，**故意不进仓库**；"
                    "评测/CI 环境跳过是预期行为（L1 结构性判据仍然会跑）")
    rows = load_denylist(p)
    assert rows, f"禁词表是空的：{p}"
    bad = scan(_tracked_files(), rows)
    assert bad == [], ("公开层指纹超上限（要么是回流，要么是新增；**别调高上限**，"
                       "该走外置/删除）：\n  - " + "\n  - ".join(bad))
