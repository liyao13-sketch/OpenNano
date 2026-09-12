# kb/adapters —— 机台导出适配（提案制）

## 三层分工（别越界）

| 层 | 谁做 | 产出 |
|---|---|---|
| ① 通用切片 | **代码** —— 复用数据线 `ingest/datasets_menu.py`（`.grp/.rcp` 解析的**唯一真相**） | 结构 + 未映射列清单 |
| ② 语义映射 | **LLM 提案**（`kb/adapter_proposal.py`） | `proposed/<机台>.json`（**status=proposed，不生效**） |
| ③ 判据与裁决 | **人**（工艺／数据线） | 采纳 → 改契约 → 回归通过才升格 |

## 硬规则（零号铁律）

1. **绝不整份喂 LLM**：一份 `.grp` 约 15 万 token；只传**列名 + 少量样例值**。
2. **LLM 不得输出数值**、不得改契约、不得写 `core/`；输出经**白名单强校验**
   （不在规范键表里的建议作废，越界值留在 `raw_suggest` 供人看）。
3. **气路/物理归属**（MFC4 接什么气、哪路阀）**属设备研究**，一律 `needs_human=true`；
   LLM 的候选只作线索。
4. **唯一落盘位置 = `proposed/`**（本机，已 gitignore）。
   采纳后走既有流程：改 `datasets_menu.MFC_PARAM/MENU_PARAM` + `schema_v0.1.md` §13.2，
   再跑 `menu_regression.py` 回归，由数据线复核。
5. 菜单参数的权威仍在 core；工具只**预览/展示**，入库走「导出包 → 数据线复核 → `build_core.py`」。

## 相关

- 体检报告（批量扫 dump）：`kb/menu_checker.py` · `POST /api/menu/check`
- 菜单读取适配器：`kb/menu_reader.py` · `POST /api/menu/{scan,group}`
- 契约：`19_工艺资料/契约/…`、`18_工艺数据资产/03_实验数据/schema_v0.1.md` §十三
