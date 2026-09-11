"""贝叶斯优化建议:Expected Improvement 采集函数 → 下一轮实验点。

策略:在变量边界内拉丁超立方采样 N 个候选 + 现有点去重,
按 EI 排序取 top-k;每点附预测值/不确定度/推荐理由(中文)。
mode: max 最大化 / min 最小化 / target 逼近目标值。
"""
from __future__ import annotations

import numpy as np
from scipy.stats import norm

from . import gp

N_CANDIDATES = 4000


def _latin_hypercube(n: int, dim: int, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    cuts = (rng.random((n, dim)) + np.arange(n)[:, None]) / n   # 每轴分层
    return rng.permuted(cuts, axis=0)


def expected_improvement(model, X_cand: np.ndarray, best: float,
                         mode: str, xi: float = 0.01) -> np.ndarray:
    mean, std = gp.predict(model, X_cand)
    if std is None:
        std = np.zeros_like(mean) + 1e-6
    std = np.maximum(std, 1e-9)
    if mode == "min":
        improve = best - mean - xi * abs(best)
    elif mode == "target":
        # 目标值模式:把 |mean-target| 当损失,越小越好
        improve = -(np.abs(mean - best) + xi * abs(best))
    else:
        improve = mean - best - xi * abs(best)
    z = improve / std
    return improve * norm.cdf(z) + std * norm.pdf(z)


def _normalize(X: np.ndarray, bounds_list: list[tuple]) -> np.ndarray:
    lo = np.array([b[0] for b in bounds_list])
    hi = np.array([b[1] for b in bounds_list])
    span = np.where(hi - lo > 1e-12, hi - lo, 1.0)
    return (X - lo) / span


def _maximin(cand_n: np.ndarray, train_n: np.ndarray, k: int) -> list[int]:
    """空间填充:每次挑"离已有点(含已选点)最远"的候选 → 最大化探索。"""
    chosen: list[int] = []
    d = np.linalg.norm(cand_n[:, None, :] - train_n[None, :, :], axis=2).min(axis=1)
    for _ in range(k):
        i = int(np.argmax(d))
        chosen.append(i)
        d = np.minimum(d, np.linalg.norm(cand_n - cand_n[i], axis=1))
        d[i] = -1.0
    return chosen


def suggest(model, feature_names: list[str], bounds: dict[str, tuple],
            mode: str = "max", target: float | None = None,
            k: int = 5, seed: int = 0, X_train: np.ndarray | None = None) -> dict:
    """下一轮实验建议。

    代理模型可靠(CV R²>0 且预测有区分度)→ EI 利用;
    模型退化(预测几乎无差异)→ 自动切**空间填充探索**(maximin,离已有实验最远),
    并在 strategy/reason 里说明,避免给出重复无意义的点。
    """
    bounds_list, names = [], []
    for f in feature_names:
        if f in bounds:
            bounds_list.append(bounds[f])
            names.append(f)
    if not bounds_list:
        return {"ok": False, "error": "没有可搜索的特征(需提供 bounds)"}

    lo = np.array([b[0] for b in bounds_list])
    hi = np.array([b[1] for b in bounds_list])
    cand = lo + _latin_hypercube(N_CANDIDATES, len(names), seed) * (hi - lo)
    mean_all, std_all = gp.predict(model, cand)
    spread = float(np.nanmax(mean_all) - np.nanmin(mean_all))
    scale = abs(float(np.nanmean(mean_all))) + 1e-9
    degenerate = (std_all is None) or (spread / scale < 1e-4)

    if degenerate and X_train is not None and len(X_train):
        cand_n = _normalize(cand, bounds_list)
        train_n = _normalize(X_train, bounds_list)
        order = _maximin(cand_n, train_n, k)
        strategy = "space_filling"
        hint = ("代理模型对参数不敏感(预测几乎无差异,样本不足或变量无关联):"
                "改为空间填充探索——这些点离已有实验最远,信息量最大")
    else:
        if mode == "target":
            best = float(target)
        else:
            best = float(mean_all.min() if mode == "min" else mean_all.max())
        ei = expected_improvement(model, cand, best, mode)
        order = [int(i) for i in np.argsort(-ei)]
        strategy = "ei"
        hint = "按 Expected Improvement 排序(利用+探索平衡)"

    out, used = [], []
    for i in order:
        point = cand[i]
        if any(np.allclose(point, p, rtol=0.02) for p in used):
            continue                       # 去近似重复
        mean, std = gp.predict(model, point.reshape(1, -1))
        reason = []
        if strategy == "space_filling":
            reason.append("远离已有实验点")
        elif mode == "target":
            reason.append(f"距目标 {abs(mean[0] - target):.3g}")
        else:
            reason.append(f"预测 {mean[0]:.4g}")
        if std is not None and std[0] is not None:
            reason.append(f"不确定度 ±{std[0]:.2g}")
        if strategy == "ei":
            reason.append(f"EI={float(ei[i]):.3g}")
        out.append({"params": {n: round(float(v), 4) for n, v in zip(names, point)},
                    "predicted": round(float(mean[0]), 4),
                    "std": round(float(std[0]), 4) if std is not None else None,
                    "ei": (float(ei[i]) if strategy == "ei" else None),
                    "reason": " · ".join(reason)})
        used.append(point)
        if len(out) >= k:
            break
    return {"ok": True, "suggestions": out, "mode": mode, "features": names,
            "strategy": strategy, "note": hint}
