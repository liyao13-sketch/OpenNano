"""安全公式引擎:用 ast 白名单求值参数传递公式(不做任意脚本)。

支持:四则/幂/取模、一元正负、min/max/abs/round/sqrt/exp/log/log10/sin/cos/tan、pi/e;
比较(== != < > <= >= in/not in)、布尔(and/or/not)、字符串/布尔/列表字面量
(供影响规则 when 条件与定性表达式使用)。
变量来源:设备参数(module.params)+ 承接参数(上游 handed)+ 已设输出 + 规则上下文。
"""
from __future__ import annotations

import ast
import math
import operator

_BINOPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
           ast.Div: operator.truediv, ast.Pow: operator.pow, ast.Mod: operator.mod}
_UNOPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_CMPOPS = {ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt,
           ast.Gt: operator.gt, ast.LtE: operator.le, ast.GtE: operator.ge}
_FUNCS = {"abs": abs, "min": min, "max": max, "round": round,
          "sqrt": math.sqrt, "exp": math.exp, "log": math.log, "log10": math.log10,
          "sin": math.sin, "cos": math.cos, "tan": math.tan}
_CONSTS = {"pi": math.pi, "e": math.e, "true": True, "false": False}


def _eval(node, env):
    if isinstance(node, ast.Expression):
        return _eval(node.body, env)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float, str, bool)):
        return node.value
    if isinstance(node, ast.List):
        return [_eval(e, env) for e in node.elts]
    if isinstance(node, ast.Name):
        if node.id in env:
            return env[node.id]
        if node.id in _CONSTS:
            return _CONSTS[node.id]
        if node.id == "True":
            return True
        if node.id == "False":
            return False
        raise NameError(node.id)
    if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
        return _BINOPS[type(node.op)](_eval(node.left, env), _eval(node.right, env))
    if isinstance(node, ast.UnaryOp):
        if type(node.op) in _UNOPS:
            return _UNOPS[type(node.op)](_eval(node.operand, env))
        if isinstance(node.op, ast.Not):
            return not _eval(node.operand, env)
    if isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            return all(_eval(v, env) for v in node.values)
        if isinstance(node.op, ast.Or):
            return any(_eval(v, env) for v in node.values)
    if isinstance(node, ast.Compare):
        left = _eval(node.left, env)
        for op, comp in zip(node.ops, node.comparators):
            right = _eval(comp, env)
            if type(op) in _CMPOPS:
                ok = _CMPOPS[type(op)](left, right)
            elif isinstance(op, ast.In):
                ok = left in right
            elif isinstance(op, ast.NotIn):
                ok = left not in right
            else:
                raise ValueError(f"unsupported comparison: {ast.dump(op)}")
            if not ok:
                return False
            left = right
        return True
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
            and node.func.id in _FUNCS:
        args = [_eval(a, env) for a in node.args]
        return _FUNCS[node.func.id](*args)
    raise ValueError(f"unsupported expression: {ast.dump(node)}")


def eval_expr(expr: str, variables: dict):
    """求值单个安全表达式;空/非法返回 None。"""
    if not expr or not str(expr).strip():
        return None
    tree = ast.parse(str(expr), mode="eval")
    return _eval(tree, dict(variables))


def eval_bool(expr: str, variables: dict) -> bool:
    """求值布尔条件(影响规则 when);空=恒真,非法=False(规则不生效)。"""
    if not expr or not str(expr).strip():
        return True
    try:
        return bool(eval_expr(expr, variables))
    except Exception:  # noqa: BLE001
        return False


def compute_outputs(module, handed: dict) -> dict:
    """按 module.formulas 计算输出参数,写回 module.key_values。"""
    env: dict = {}
    env.update(module.params or {})       # 设备参数
    env.update(handed or {})              # 承接参数(来自上游)
    env.update(module.key_values or {})   # 已设输出
    for out, expr in (module.formulas or {}).items():
        try:
            v = eval_expr(expr, env)
            if v is not None:
                module.key_values[out] = float(v)
                env[out] = float(v)
        except Exception:  # noqa: BLE001  # 公式求值失败忽略,保留原值
            pass
    return module.key_values
