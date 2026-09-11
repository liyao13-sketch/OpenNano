"""OpenNano 优化引擎(O1 单点优化):GPR 拟合 + BO 建议 + 可视化。

数据流:KB 知识条目(parameters.steps 多步配方 + results) → 扁平特征 → GPR。
特征抽取:每步前缀化(step_name 小写 + 参数名),如 me_power_w / bt_bcl3;
样本 = steps 含该步且 results 有目标字段的条目。
"""
MODEL_REGISTRY: dict[str, dict] = {}   # model_id → {model,X,y,features,target,meta}
