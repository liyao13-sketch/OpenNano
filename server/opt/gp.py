"""GPR 代理模型:KB 知识条目 → 特征矩阵 → 高斯过程回归(带 CV R²)。

- 特征:parameters.steps 展平。每步以 step_name(小写)作前缀,如
  {step_name:'ME', power_w:160} → me_power_w。可指定 features 白名单,
  缺省自动取全数据方差非零的特征。
- 目标:results 中的数值字段(如 er_nm_min / selectivity / sidewall_angle_deg)。
- 模型:GaussianProcessRegressor(RBF + White, ARD),normalize_y。
  小样本(n<8)时退化为线性回归并注明。
"""
from __future__ import annotations

import warnings

import numpy as np

warnings.filterwarnings("ignore", category=UserWarning)
try:  # sklearn 收敛警告在小样本下常见,不影响使用
    from sklearn.exceptions import ConvergenceWarning
    warnings.filterwarnings("ignore", category=ConvergenceWarning)
except ImportError:  # pragma: no cover
    pass
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import RBF, ConstantKernel, WhiteKernel
from sklearn.linear_model import LinearRegression
from sklearn.model_selection import LeaveOneOut, cross_val_predict


def _step_features(entry: dict) -> dict[str, float]:
    """parameters.steps → {前缀_参数: 值}。"""
    out: dict[str, float] = {}
    steps = ((entry.get("parameters") or {}).get("steps")) or []
    for st in steps:
        prefix = str(st.get("step_name", "step")).strip().lower() or "step"
        for k, v in st.items():
            if k == "step_name":
                continue
            try:
                out[f"{prefix}_{k}"] = float(v)
            except (TypeError, ValueError):
                continue
    return out


def build_dataset(entries: list[dict], target: str,
                  features: list[str] | None = None,
                  step: str | None = None) -> dict:
    """KB 条目 → {X, y, feature_names, skipped}。

    step: 只用指定步(如 'ME')的参数作特征(其余步丢弃);None=全部步。
    features: 特征白名单;None=自动(方差非零列)。
    """
    rows, ys = [], []
    for e in entries:
        y = (e.get("results") or {}).get(target)
        if y is None:
            continue
        feats = _step_features(e)
        if step:
            p = f"{step.strip().lower()}_"
            feats = {k: v for k, v in feats.items() if k.startswith(p)}
        if not feats:
            continue
        rows.append(feats)
        ys.append(float(y))
    if not rows:
        return {"X": None, "y": None, "feature_names": [], "skipped": len(entries)}

    if features:
        names = [f for f in features if any(f in r for r in rows)]
    else:
        names = sorted({k for r in rows for k in r})
        # 去零方差列(对建模无信息)
        arr = np.array([[r.get(n, np.nan) for n in names] for r in rows])
        keep = [i for i in range(len(names))
                if np.nanvar(arr[:, i]) > 1e-12]
        names = [names[i] for i in keep]

    X = np.array([[r.get(n, np.nan) for n in names] for r in rows])
    y = np.array(ys, dtype=float)
    # 均值填补缺失(该步无此气体的条目)
    col_mean = np.nanmean(X, axis=0)
    nan_pos = np.isnan(X)
    if nan_pos.any():
        X[nan_pos] = np.take(col_mean, np.where(nan_pos)[1])
    return {"X": X, "y": y, "feature_names": names, "skipped": len(entries) - len(y)}


def _make_gp(dim: int) -> GaussianProcessRegressor:
    kernel = ConstantKernel(1.0) * RBF(length_scale=np.ones(dim),
                                       length_scale_bounds=(1e-2, 1e3)) \
        + WhiteKernel(noise_level=0.05, noise_level_bounds=(1e-5, 1e1))
    return GaussianProcessRegressor(kernel=kernel, normalize_y=True,
                                    n_restarts_optimizer=4, random_state=0)


def fit(dataset: dict) -> dict:
    """拟合并返回模型句柄信息。n<8 退化为线性回归。"""
    X, y, names = dataset["X"], dataset["y"], dataset["feature_names"]
    n = len(y)
    if n < 4:
        return {"ok": False, "error": f"样本不足({n}<4)", "n": n}

    model, fallback = None, False
    if n < 8:
        model, fallback = LinearRegression(), True
    else:
        model = _make_gp(X.shape[1])

    # 交叉验证 R²(LOO,n 小;k=3 折,n 大)
    try:
        if n <= 25:
            pred = cross_val_predict(model, X, y, cv=LeaveOneOut())
        else:
            pred = cross_val_predict(model, X, y, cv=3)
        denom = ((y - y.mean()) ** 2).sum()
        cv_r2 = 1 - ((y - pred) ** 2).sum() / denom if denom > 0 else float("nan")
    except Exception:  # noqa: BLE001
        cv_r2 = float("nan")

    model.fit(X, y)
    train_r2 = model.score(X, y)
    return {"ok": True, "model": model, "n": n, "features": names,
            "cv_r2": float(cv_r2), "train_r2": float(train_r2),
            "fallback_linear": fallback}


def predict(model, X: np.ndarray):
    """返回 (mean, std);线性回归退化时 std=None。"""
    if hasattr(model, "kernel"):        # GaussianProcessRegressor
        m, std = model.predict(X, return_std=True)
        return m, std
    return model.predict(X), None
