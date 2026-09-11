"""影响规则(Influence Rule):参数/属性 → 参数 的定性+定量统一影响模型。

一条规则 = {
  id, from, to,          影响源(任意参数/属性名) → 被影响参数
  when,                  生效条件(布尔表达式,空=恒生效;如 surface_film == 'SiO₂')
  expr,                  定量面:表达式(空=纯定性)
  sign, mechanism,       定性面:方向(+/-/非单调) + 机理描述
  scope,                 global | equipment:<id> | node:<id>
  source, reliability_score, enabled
}

统一三套旧特例(见 2026-09-09 会话决策):
  film_props  → from=surface_film, to=gds_bias, when=膜名匹配, expr=bias值
  param_links → 纯定性规则(sign/mechanism)
  设备 formulas 保留原位(概念上视作 equipment 级规则,二期迁移)
"""
from __future__ import annotations

import re
import uuid

from . import engine as formula_engine

FILM_WHEN_RE = re.compile(r"surface_film\s*==\s*['\"]([^'\"]+)['\"]")


def new_rule_id() -> str:
    return f"ir-{uuid.uuid4().hex[:8]}"


def eval_when(rule: dict, env: dict) -> bool:
    """规则在给定上下文是否生效。"""
    return formula_engine.eval_bool(rule.get("when") or "", env)


def applicable(rules: list[dict], env: dict, scope: str | None = None) -> list[dict]:
    """过滤出满足条件(且 enabled)的规则;scope 非 None 时只取该 scope。"""
    out = []
    for r in rules or []:
        if not r.get("enabled", True):
            continue
        if scope is not None and r.get("scope", "global") != scope:
            continue
        if eval_when(r, env):
            out.append(r)
    return out


def eval_rule(rule: dict, env: dict) -> float | None:
    """定量求值;无 expr 或求值失败返回 None(纯定性)。"""
    expr = (rule.get("expr") or "").strip()
    if not expr:
        return None
    try:
        v = formula_engine.eval_expr(expr, env)
        return float(v) if v is not None else None
    except Exception:  # noqa: BLE001
        return None


def compute_outputs(rules: list[dict], env: dict, key_values: dict,
                    allowed: set | None = None) -> dict:
    """把适用的定量规则求值写入 key_values(在设备公式之后执行,可覆盖)。

    allowed: 该节点允许被写入的参数名集合(输出 ∪ 自身参数 ∪ 已有值)。
    给定后,只写属于本节点的目标参数——避免"膜层 bias 规则"被写到无关节点上。
    """
    for r in applicable(rules, env):
        to = r.get("to")
        if allowed is not None and to not in allowed:
            continue
        v = eval_rule(r, env)
        if v is not None:
            key_values[to] = v
            env[to] = v
    return key_values


def bias_table(rules: list[dict]) -> dict[str, float]:
    """从规则推导 膜→gds_bias 查询表(兼容 when 形如 surface_film == 'X' 的规则)。

    供连线悬停等同步场景快速取值;一般场景走 /api/rules/resolve。
    """
    table: dict[str, float] = {}
    for r in rules or []:
        if r.get("to") != "gds_bias" or not r.get("expr"):
            continue
        m = FILM_WHEN_RE.search(r.get("when") or "")
        if not m:
            continue
        try:
            table[m.group(1)] = float(str(r["expr"]).strip())
        except (TypeError, ValueError):
            continue
    return table


def migrate(film_props: dict | None, param_links: list | None) -> list[dict]:
    """一次性迁移:film_props + param_links → 影响规则列表。"""
    rules: list[dict] = []
    for film, bias in (film_props or {}).items():
        try:
            fv = float(bias)
        except (TypeError, ValueError):
            continue
        rules.append({
            "id": new_rule_id(),
            "from": "surface_film", "to": "gds_bias",
            "when": f"surface_film == '{film}'",
            "expr": str(fv),
            "sign": "-" if fv < 0 else "+",
            "mechanism": f"{film} 表面 LDW/GDS 写入需 CD bias 补偿(迁移自材料属性库)",
            "scope": "global", "source": "迁移自 film_props",
            "reliability_score": 4, "enabled": True,
        })
    for l in (param_links or []):
        frm, to = l.get("from"), l.get("to")
        if not frm or not to:
            continue
        rules.append({
            "id": new_rule_id(),
            "from": frm, "to": to, "when": "", "expr": "",
            "sign": "", "mechanism": "定性依赖(迁移自参数依赖边,待补机理与公式)",
            "scope": "global", "source": "迁移自 param_links",
            "reliability_score": 3, "enabled": True,
        })
    return rules
