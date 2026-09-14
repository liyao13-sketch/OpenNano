export interface ParamDef { label: string; unit: string; default: number; min: number; max: number }
export interface Module {
  id: string; kind: string; subtype: string; name: string;
  x: number; y: number;
  params: Record<string, number>; param_meta: Record<string, any>;
  param_defs: Record<string, ParamDef>;
  equipment_id: string;
  equipment_name?: string;
  machine_id?: string; machine_name?: string; family?: string; family_label?: string;
  /** core 口径的机台号 / 显示名（`core_schema.TOOL_DISPLAY` 那套字面量）。
   *  画布的 `machine_name` 是**应用库显示名**（`DRIE-Bosch` / `PECVD`），与 core 机台号是两套；
   *  导出走 `core_tool_id` 优先，缺省才回落到库内机台档案（见 `kb/core_vocab.resolve_tool`）。 */
  core_tool_id?: string; core_tool?: string;
  param_inputs: string[]; param_outputs: string[]; formulas: Record<string, string>;
  material: Record<string, any>; key_values: Record<string, number>;
  doe: any; annotations: any[]; sim_result: any;
  // 流程运行(BEAMER 式):禁用/备注/运行状态
  disabled?: boolean; comment?: string; run_state?: 'idle' | 'ok' | 'stale' | 'running';
  // 数据桥：与 core 对接的字段（批次视图/续做/导出追加包都读这些，别再用 (m as any)）
  core_run_id?: string; core_parent_run_id?: string; core_batch_id?: string;
  core_stage?: string; core_stage_seq?: number; core_sample_id?: string;
  core_recipe_id?: string; core_date?: string;
  core_measurements?: any[]; core_observations?: any[]; core_menu_steps?: any[];
  /** core 的 runs.run_nature：chain / trial / batch_level / season（热机，画布默认收起） */
  run_nature?: string;
  /* 本工序第几次（不含 season）：后端按 core 全量算 ⇒ 显示 `run{N}` */
  stage_run_index?: number;
  /** 参数调试线归属（core v0.1.6）：同工序一次参数扫描 + 第几轮 ⇒ 画布显示 run1/run2… */
  tune_id?: string; tune_step?: number | string;
}
/* family/family_label：左栏方块按**工艺族**上色（须与画布节点同色）⇒ 由后端 module_catalog 给出 */
export interface CatalogItem { group: string; kind: string; subtype: string; name: string; desc: string;
  family?: string; family_label?: string }
export interface Equipment { id: string; name: string; params: Record<string, ParamDef>;
  inputs: string[]; outputs: string[]; formulas: Record<string, string> }
export interface Library {
  categories: string[]; equipment: Record<string, Equipment[]>; params: Record<string, any>;
  param_links: {from:string;to:string}[]; defaults: any; film_props: Record<string, number>;
  influence_rules?: any[]; bias_table?: Record<string, number>;
  machines?: any[]; param_categories?: string[];
  /** 库文件读不动时的原因（空 = 正常）；`corrupt_backup` 是留档路径、`save_blocked` 表示本次不落盘。
      后端不再静默退回默认值 —— 界面必须把这件事说出来，否则用户以为自己的机台/模板"没了"。 */
  load_error?: string; corrupt_backup?: string; save_blocked?: boolean;
}
