"""响应面可视化:等高线(任意两特征) + 主效应(逐特征曲线) → PNG bytes。

matplotlib 后端 Agg;中文字体 PingFang(与 ta_rate_model 同配置)。
"""
from __future__ import annotations

import io

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from . import gp

plt.rcParams["font.sans-serif"] = ["PingFang SC", "Hiragino Sans GB",
                                   "Arial Unicode MS", "sans-serif"]
plt.rcParams["axes.unicode_minus"] = False


def _grid(model, base: np.ndarray, names: list[str], xi: int, yi: int,
          res: int = 40):
    lo, hi = base[xi] * 0.6, base[xi] * 1.4
    lo2, hi2 = base[yi] * 0.6, base[yi] * 1.4
    if hi - lo < 1e-9:
        lo, hi = base[xi] - 1, base[xi] + 1
    if hi2 - lo2 < 1e-9:
        lo2, hi2 = base[yi] - 1, base[yi] + 1
    gx = np.linspace(min(lo, 0) if base[xi] == 0 else lo, hi, res)
    gy = np.linspace(min(lo2, 0) if base[yi] == 0 else lo2, hi2, res)
    XX, YY = np.meshgrid(gx, gy)
    P = np.tile(base, (XX.size, 1))
    P[:, xi] = XX.ravel()
    P[:, yi] = YY.ravel()
    Z, S = gp.predict(model, P)
    return gx, gy, Z.reshape(XX.shape), (S.reshape(XX.shape) if S is not None else None)


def contour(model, feature_names: list[str], X: np.ndarray, target: str,
            x_feat: str | None = None, y_feat: str | None = None) -> bytes:
    x_feat = x_feat or feature_names[0]
    y_feat = y_feat or (feature_names[1] if len(feature_names) > 1 else feature_names[0])
    xi, yi = feature_names.index(x_feat), feature_names.index(y_feat)
    base = X.mean(axis=0)
    gx, gy, Z, S = _grid(model, base, feature_names, xi, yi)
    if S is not None:
        S = S / (S.max() + 1e-12)

    fig, ax = plt.subplots(figsize=(6.4, 4.8), dpi=110)
    cf = ax.contourf(gx, gy, Z, levels=18, cmap="viridis")
    plt.colorbar(cf, ax=ax, label=target)
    cs = ax.contour(gx, gy, Z, levels=10, colors="w", linewidths=0.6, alpha=0.7)
    ax.clabel(cs, fontsize=7, fmt="%.3g")
    if S is not None:
        ax.contour(gx, gy, S, levels=[0.3, 0.6], colors="r", linestyles="--",
                   linewidths=1.0)
        ax.plot([], [], "r--", label="不确定度等值线")
        ax.legend(fontsize=8, loc="upper right")
    ax.scatter(X[:, xi], X[:, yi], c="w", s=26, edgecolors="k", zorder=5,
               label="已有实验")
    ax.set_xlabel(x_feat)
    ax.set_ylabel(y_feat)
    ax.set_title(f"响应面: {target} ({x_feat} × {y_feat},其余取均值)")
    ax.legend(fontsize=8, loc="lower right")
    buf = io.BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return buf.getvalue()


def main_effects(model, feature_names: list[str], X: np.ndarray,
                 target: str) -> bytes:
    base = X.mean(axis=0)
    n = len(feature_names)
    ncol = min(n, 3)
    nrow = (n + ncol - 1) // ncol
    fig, axes = plt.subplots(nrow, ncol, figsize=(4.2 * ncol, 3.0 * nrow),
                             dpi=110, squeeze=False)
    for i, name in enumerate(feature_names):
        ax = axes[i // ncol][i % ncol]
        span = (X[:, i].max() - X[:, i].min()) or 1.0
        xs = np.linspace(X[:, i].min() - 0.1 * span, X[:, i].max() + 0.1 * span, 60)
        P = np.tile(base, (xs.size, 1))
        P[:, i] = xs
        m, s = gp.predict(model, P)
        ax.plot(xs, m, lw=2, color="#38bdf8")
        if s is not None:
            ax.fill_between(xs, m - 1.96 * s, m + 1.96 * s, alpha=0.22,
                            color="#38bdf8")
        ax.set_title(name, fontsize=10)
        ax.tick_params(labelsize=8)
    for j in range(n, nrow * ncol):
        axes[j // ncol][j % ncol].set_visible(False)
    fig.suptitle(f"主效应图: {target}(阴影=95% 置信带,其余特征取均值)", fontsize=11)
    buf = io.BytesIO()
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    fig.savefig(buf, format="png")
    plt.close(fig)
    return buf.getvalue()
