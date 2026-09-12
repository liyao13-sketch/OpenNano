export interface ParamDef { label: string; unit: string; default: number; min: number; max: number }
export interface Module {
  id: string; kind: string; subtype: string; name: string;
  x: number; y: number;
  params: Record<string, number>; param_meta: Record<string, any>;
  param_defs: Record<string, ParamDef>;
  equipment_id: string;
  equipment_name?: string;
  machine_id?: string; machine_name?: string; family?: string; family_label?: string;
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
}
export interface CatalogItem { group: string; kind: string; subtype: string; name: string; desc: string }
export interface Equipment { id: string; name: string; params: Record<string, ParamDef>;
  inputs: string[]; outputs: string[]; formulas: Record<string, string> }
export interface Library {
  categories: string[]; equipment: Record<string, Equipment[]>; params: Record<string, any>;
  param_links: {from:string;to:string}[]; defaults: any; film_props: Record<string, number>;
  influence_rules?: any[]; bias_table?: Record<string, number>;
  machines?: any[]; param_categories?: string[];
}
