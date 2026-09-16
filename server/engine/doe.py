"""DOE 实验矩阵生成(V2):全因子 / 部分因子 / BBD / CCD + 随机化。

输入:变量列表 [{param, min, max, step}],设计类型,中心点,期望次数(部分因子用)。
编码约定:min/max = ±1 水平,中心 = 中点;BBD/CCD 忽略 step。
输出:matrix(实际单位), levels, runs, design_type。
"""
from __future__ import annotations

import itertools
import random


def _levels(minv: float, maxv: float, step: float) -> list[float]:
    """等间隔采样,含端点。step<=0 时退化为两端+中点。"""
    if step is None or step <= 0:
        return [minv, maxv]
    n = int(round((maxv - minv) / step))
    n = max(n, 1)
    return [minv + i * step for i in range(n + 1)]


def _coded_matrix(vars_: list[dict], design: str, alpha: float,
                  center_points: int) -> list[list[float]]:
    """生成编码(-1/0/+1,轴向 ±alpha)设计,再映射回实际单位。"""
    k = len(vars_)
    lo = [float(v["min"]) for v in vars_]
    hi = [float(v["max"]) for v in vars_]
    mid = [(a + b) / 2 for a, b in zip(lo, hi)]
    half = [(b - a) / 2 for a, b in zip(lo, hi)]

    def to_units(coded: list[float]) -> list[float]:
        return [m + c * h for m, h, c in zip(mid, half, coded)]

    if design == "bbd" and 3 <= k <= 7:
        rows: list[list[float]] = []
        for i, j in itertools.combinations(range(k), 2):
            for xi in (-1, 1):
                for xj in (-1, 1):
                    coded = [0.0] * k
                    coded[i], coded[j] = float(xi), float(xj)
                    rows.append(to_units(coded))
        for _ in range(max(center_points, 3)):    # BBD 默认 ≥3 中心点
            rows.append(to_units([0.0] * k))
        return rows

    if design == "ccd":
        rows = []
        for corner in itertools.product([-1, 1], repeat=k):
            rows.append(to_units([float(c) for c in corner]))
        for i in range(k):                         # 轴向点 ±alpha
            for s in (-alpha, alpha):
                coded = [0.0] * k
                coded[i] = float(s)
                rows.append(to_units(coded))
        for _ in range(max(center_points, 3)):
            rows.append(to_units([0.0] * k))
        return rows

    # full / partial(BBD 定义域外也落这里)
    levels = [_levels(float(v["min"]), float(v["max"]), float(v.get("step", 0)))
              for v in vars_]
    full = list(itertools.product(*levels))
    if design == "partial":
        want = (len(full) // 2 if len(full) > 1 else len(full))
        want = max(1, min(want, len(full)))
        step = max(1, len(full) // want)
        matrix = [list(r) for i, r in enumerate(full) if i % step == 0][:want]
    else:
        matrix = [list(r) for r in full]
    if center_points and vars_:
        mids = [(lv[0] + lv[-1]) / 2 for lv in levels]
        for _ in range(center_points):
            matrix.append(list(mids))
    return matrix


def generate_matrix(variables: list[dict], design_type: str = "full",
                    center_points: int = 0, n_runs: int | None = None,
                    randomize: bool = False, seed: int = 42,
                    alpha: float | None = None) -> dict:
    """生成实验矩阵。返回 {matrix, levels, runs, design_type}。

    design_type: full 全因子 / partial 部分因子 / bbd Box-Behnken / ccd 中心复合
    randomize: 打乱 run 顺序(降低时间漂移混淆)
    alpha: CCD 轴向距离;None=1(面心设计,不出 min/max 界)
    """
    if design_type not in ("full", "partial", "bbd", "ccd"):
        design_type = "full"
    alpha = 1.0 if alpha is None else float(alpha)
    matrix = _coded_matrix(variables, design_type, alpha, center_points)

    if randomize:
        random.Random(seed).shuffle(matrix)

    levels = [[float(v["min"]), (float(v["min"]) + float(v["max"])) / 2, float(v["max"])]
              for v in variables]
    return {"matrix": matrix, "levels": levels,
            "runs": len(matrix), "design_type": design_type}


#: 一次 DOE 请求的矩阵行数上限（2026-09-16 审计：原来无上限，`step` 写小一点
#: 就能让 `full = product(...)` 直接吃爆服务进程内存 —— 实测把 pytest 进程都杀了）。
MAX_RUNS = 100_000


def estimate_runs(variables: list[dict], design_type: str = "full",
                  center_points: int = 0, n_runs: int | None = None) -> int:
    """预估矩阵行数；**不可行/不可估**返回 -1。

    给入口做上限闸用（`main.api_doe`）。与 `generate_matrix` 的分支保持一致：
    full = ∏ 各变量水平数；partial = n_runs；bbd/ccd 按变量数算。
    """
    k = len(variables or [])
    if k == 0:
        return 0
    if design_type == "bbd":
        if not (3 <= k <= 7):
            return -1
        return 4 * (k * (k - 1) // 2) + max(0, center_points)
    if design_type == "ccd":
        if k > 20:
            return -1
        return 2 ** k + 2 * k + max(0, center_points)
    if design_type == "partial":
        return int(n_runs or 0) or 8
    total = 1
    for v in variables:
        try:
            lv = _levels(float(v["min"]), float(v["max"]), v.get("step"))
        except (KeyError, TypeError, ValueError):
            return -1
        total *= max(1, len(lv))
        if total > 10 ** 9:                     # 早早退出，别真去乘出天文数字
            return total
    return total
