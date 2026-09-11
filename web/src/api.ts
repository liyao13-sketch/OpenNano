import type { Module, Library } from './types'

async function j<T>(url: string, opts?: RequestInit): Promise<T> {
  const r = await fetch(url, opts)
  if (!r.ok) throw new Error(`${r.status} ${url}`)
  return r.json()
}

/** POST 取文件并触发下载(文件名取自响应头)。返回字节数。 */
export async function download(url: string, body: any): Promise<number> {
  const r = await fetch(url, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!r.ok) throw new Error(`${r.status} ${url}`)
  const cd = r.headers.get('Content-Disposition') || ''
  const star = /filename\*=UTF-8''([^;]+)/.exec(cd)
  const m = /filename="?([^";]+)"?/.exec(cd)
  let fname = ''
  if (star) { try { fname = decodeURIComponent(star[1]) } catch { fname = star[1] } }
  const blob = await r.blob()
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = fname || (m ? m[1] : 'opennano.xlsx')
  a.click()
  URL.revokeObjectURL(a.href)
  return blob.size
}

export const api = {
  health: () => j<any>('/api/health'),
  catalog: () => j<{categories:any[];processes:any[];metrology:any[];families:{key:string;label:string}[];module_catalog:any[]}>('/api/catalog'),
  library: () => j<Library>('/api/library'),
  newModule: (subtype: string, x: number, y: number) =>
    j<Module>('/api/modules/new', { method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ subtype, x, y }) }),
  compute: (params: any, handed: any, key_values: any, formulas: any, context?: Record<string, any>) =>
    j<{key_values: Record<string,number>}>('/api/compute', { method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ params, handed, key_values, formulas, context: context || {} }) }),
  saveProject: (name: string, modules: Module[], edges: any[]) =>
    j<any>('/api/project/save', { method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ name, modules, edges }) }),
  loadProject: (name?: string) => j<any>('/api/project' + (name ? `?name=${encodeURIComponent(name)}` : '')),
  projectList: () => j<{projects:{name:string;modules:number;edges:number;saved_at:string}[]}>('/api/project/list'),
  projectDelete: (name: string) => j<any>('/api/project/' + encodeURIComponent(name), { method:'DELETE' }),
  configExport: () => j<any>('/api/config/export'),
  configImport: (bundle: {library?:any; kb_entries?:any[]}) =>
    j<any>('/api/config/import', { method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify(bundle) }),
  kb: (q?: string) => j<any[]>('/api/kb' + (q ? `?q=${encodeURIComponent(q)}` : '')),
  kbStats: () => j<any>('/api/kb/stats'),
  kbIngestUpload: (payload: {filename:string; content_b64:string; process_type?:string; material?:string; dry_run?:boolean}) =>
    j<any>('/api/kb/ingest_upload', { method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify(payload) }),
  kbFields: (process_type?: string, material?: string) => {
    const qs = new URLSearchParams()
    if (process_type) qs.set('process_type', process_type)
    if (material) qs.set('material', material)
    const q = qs.toString()
    return j<{total:number; fields:{field:string;label:string;unit:string;count:number;param?:string|null}[]}>('/api/kb/fields' + (q ? `?${q}` : ''))
  },
  agentChat: (message: string, history: any[]) =>
    j<{answer:string;sources:any[];llm_available:boolean;tool_calls?:any[];canvas_ops?:any[]}>('/api/agent/chat', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ message, history }) }),
  applyEquipment: (subtype: string, equipment_id: string) =>
    j<any>('/api/modules/apply_equipment', { method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ subtype, equipment_id }) }),
  eqAdd: (category: string, name: string) =>
    j<any>('/api/library/equipment/add', { method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ category, name }) }),
  eqRemove: (equipment_id: string) =>
    j<any>('/api/library/equipment/remove', { method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ equipment_id }) }),
  eqUpdate: (equipment_id: string, patch: { name?: string; params?: Record<string, any>; inputs?: string[]; outputs?: string[]; formulas?: Record<string,string> }) =>
    j<any>('/api/library/equipment/update', { method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ equipment_id, ...patch }) }),
  setDefault: (category: string, equipment_id: string | null) =>
    j<any>('/api/library/defaults', { method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ category, equipment_id }) }),
  paramAdd: (name: string, unit: string, category: string, scope?: string) =>
    j<any>('/api/library/params', { method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ name, unit, category, scope }) }),
  categoryAdd: (name: string) =>
    j<any>('/api/library/param_categories', { method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ name }) }),
  categoryRemove: (name: string) =>
    j<any>('/api/library/param_categories/' + encodeURIComponent(name), { method:'DELETE' }),
  machineAdd: (m: any) => j<any>('/api/machines', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(m) }),
  machineUpdate: (id: string, m: any) => j<any>('/api/machines/' + id, { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(m) }),
  machineRemove: (id: string) => j<any>('/api/machines/' + id, { method:'DELETE' }),
  paramRemove: (name: string) =>
    j<any>('/api/library/params/' + encodeURIComponent(name), { method:'DELETE' }),
  setFilmProps: (props: Record<string, number>) =>
    j<any>('/api/library/film_props', { method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ props }) }),
  gds: (cfg: any) => j<any>('/api/gds', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(cfg) }),
  gdsLive: (cfg: any) => j<any>('/api/gds/live', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(cfg) }),
  doe: (req: any) => j<any>('/api/doe', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(req) }),
  flowRun: (payload: {modules:any[]; edges:any[]; until?:string|null; context?:Record<string,any>}) =>
    j<{ok:boolean;order:string[];results:Record<string,Record<string,number>>;log:any[];
       errors:any[];cyclic:string[];ran:number;skipped:number}>('/api/flow/run', {
      method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload) }),
  expackImport: (path: string) => j<any>('/api/expack/import', {
    method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ path }) }),
  coreQuantities: () => j<{quantities:{quantity:string;unit:string;n:number}[]; tools:{tool:string;tool_id:string;n:number}[]; stages:string[]}>('/api/core/quantities'),
  coreStats: () => j<{available:boolean; counts:Record<string,number>}>('/api/core/stats'),
  optFit: (req: {process_type?:string; material?:string|null; target:string; step?:string;
                 source?:string; quantity?:string|null; stage?:string|null; tool_id?:string|null}) =>
    j<any>('/api/opt/fit', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(req) }),
  optSuggest: (req: {model_id:string; mode:string; target_value?:number|null; n?:number}) =>
    j<any>('/api/opt/suggest', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(req) }),
  optPlotUrl: (modelId: string, kind: string) => `/api/opt/plot?model_id=${encodeURIComponent(modelId)}&kind=${kind}`,
  setParamLinks: (links: {from:string;to:string}[]) =>
    j<any>('/api/library/param_links', { method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ links }) }),
  rules: () => j<{rules:any[];bias_table:Record<string,number>}>('/api/rules'),
  rulesSave: (rules: any[]) =>
    j<any>('/api/rules', { method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ rules }) }),
  rulesResolve: (context: Record<string, any>, to?: string) =>
    j<{resolved:any[]}>('/api/rules/resolve', { method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ context, to }) }),
}
