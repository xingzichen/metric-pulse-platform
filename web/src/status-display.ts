/**
 * 后端状态码的集中展示字典。
 *
 * API 和数据库继续使用稳定的英文枚举；只有展示层在这里转换为中文、颜色和排序。
 * 未识别的新状态采用中性色，确保后端扩展枚举时前端仍可安全渲染。
 */
export type StatusTone =
  "neutral" | "info" | "success" | "warning" | "danger" | "purple";

export interface StatusPresentation {
  label: string;
  tone: StatusTone;
  color: string;
  background: string;
  border: string;
}

const tones: Record<StatusTone, Omit<StatusPresentation, "label" | "tone">> = {
  neutral: { color: "#64748b", background: "#f8fafc", border: "#cbd5e1" },
  info: { color: "#2563eb", background: "#eff6ff", border: "#bfdbfe" },
  success: { color: "#15803d", background: "#f0fdf4", border: "#bbf7d0" },
  warning: { color: "#b45309", background: "#fffbeb", border: "#fde68a" },
  danger: { color: "#b91c1c", background: "#fef2f2", border: "#fecaca" },
  purple: { color: "#7e22ce", background: "#faf5ff", border: "#e9d5ff" },
};

const definitions: Record<string, { label: string; tone: StatusTone }> = {
  UPLOADED: { label: "已上传", tone: "neutral" },
  ANALYZING: { label: "分析中", tone: "info" },
  READY: { label: "已就绪", tone: "success" },
  NEEDS_CONFIRMATION: { label: "待确认", tone: "warning" },
  DRAFT: { label: "草稿", tone: "neutral" },
  QUEUED: { label: "排队中", tone: "info" },
  RUNNING: { label: "运行中", tone: "info" },
  PAUSING: { label: "暂停中", tone: "warning" },
  PAUSED: { label: "已暂停", tone: "warning" },
  STOPPING: { label: "停止中", tone: "warning" },
  STOPPED: { label: "已停止", tone: "neutral" },
  SUCCEEDED: { label: "已完成", tone: "success" },
  SUCCEEDED_WITH_ERRORS: { label: "完成但有异常", tone: "warning" },
  FAILED: { label: "失败", tone: "danger" },
  DELETED: { label: "已删除", tone: "neutral" },
  PENDING: { label: "待处理", tone: "neutral" },
  LEASED: { label: "已领取", tone: "info" },
  FAILED_RETRYABLE: { label: "等待重试", tone: "warning" },
  FAILED_FINAL: { label: "最终失败", tone: "danger" },
  DISCARDED: { label: "已终止", tone: "neutral" },
  NOT_EVALUATED: { label: "未评估", tone: "neutral" },
  RESOLVED: { label: "已解决", tone: "success" },
  PARTIAL: { label: "部分解决", tone: "warning" },
  UNRESOLVED: { label: "未解决", tone: "warning" },
  CONFLICT: { label: "证据冲突", tone: "danger" },
  INVALID: { label: "结果无效", tone: "danger" },
  UNREVIEWED: { label: "待审核", tone: "neutral" },
  AUTO_APPROVED: { label: "自动通过", tone: "success" },
  APPROVED: { label: "人工通过", tone: "success" },
  CORRECTED: { label: "已修正", tone: "purple" },
  CONFIRMED_UNRESOLVED: { label: "已确认无法解决", tone: "purple" },
  REJECTED: { label: "已驳回", tone: "danger" },
  SKIPPED: { label: "已跳过", tone: "neutral" },
  BUILDING: { label: "生成中", tone: "info" },
  STALE: { label: "已失效", tone: "warning" },
  PUBLISHED: { label: "已发布", tone: "success" },
  LOW: { label: "低风险", tone: "success" },
  MEDIUM: { label: "中风险", tone: "warning" },
  HIGH: { label: "高风险", tone: "danger" },
  DIRECT_LINK: { label: "采集链接直取", tone: "success" },
  SEARCH_FALLBACK: { label: "搜索降级", tone: "warning" },
  UNIQUE_MATCH: { label: "唯一匹配", tone: "success" },
  OFFICIAL_ANNUAL_POSITION_MATCH: { label: "官方年度名单匹配", tone: "success" },
  AMBIGUOUS_MATCH: { label: "多条匹配", tone: "danger" },
  TARGET_NOT_FOUND: { label: "未匹配到数据", tone: "warning" },
  UNSTRUCTURED_RELEVANT: { label: "正文相关", tone: "info" },
  UNSTRUCTURED: { label: "非结构化来源", tone: "info" },
  NO_DIRECT_SOURCE: { label: "无采集链接", tone: "neutral" },
  FETCH_FAILED: { label: "获取失败", tone: "danger" },
  NO_MATCH_KEYS: { label: "缺少匹配字段", tone: "warning" },
  PARSE_FAILED: { label: "解析失败", tone: "danger" },
  MATCHED: { label: "匹配通过", tone: "success" },
  UNMATCHED: { label: "未匹配", tone: "danger" },
  DETERMINISTIC: { label: "程序换算", tone: "success" },
  MODEL_FALLBACK: { label: "模型降级换算", tone: "warning" },
  NONE: { label: "未执行换算", tone: "neutral" },
  CONVERTED: { label: "已完成换算", tone: "success" },
  SAME_UNIT: { label: "无需变换单位", tone: "success" },
  UNSUPPORTED: { label: "程序规则未覆盖", tone: "warning" },
  MISSING_SOURCE_UNIT: { label: "缺少来源单位", tone: "warning" },
  MISSING_TARGET_UNIT: { label: "缺少标准单位", tone: "warning" },
  NON_NUMERIC: { label: "原始值不是数值", tone: "danger" },
  DIMENSION_MISMATCH: { label: "单位维度不一致", tone: "danger" },
  INVALID_RESULT: { label: "换算结果无效", tone: "danger" },
};

export function getStatusPresentation(
  value?: string | null,
): StatusPresentation {
  const definition = (value && definitions[value]) || {
    label: "未知状态",
    tone: "neutral" as const,
  };
  return { ...definition, ...tones[definition.tone] };
}

const reasonLabels: Record<string, string> = {
  EXECUTION_NOT_SUCCEEDED: "执行尚未成功",
  ROW_CONTRACT_INVALID: "行数据不符合采集约束",
  EVIDENCE_CONFLICT: "来源证据相互冲突",
  NO_SUPPORTED_VALUE: "未找到有证据支持的值",
  REQUIRED_FIELDS_MISSING: "必填字段仍有缺失",
  VALIDATION_FAILED: "结果校验未通过",
  VALIDATED_COMPLETE: "结果完整且已通过校验",
  HUMAN_CORRECTION: "人工修正后通过",
  RECOLLECTION_REQUESTED: "已要求重新采集",
  NO_DIRECT_SOURCE: "未提供采集链接",
  DIRECT_FETCH_FAILED: "采集链接获取失败",
  DIRECT_PARSE_FAILED: "采集链接解析失败",
  TARGET_NOT_FOUND: "采集链接中未找到对应数据",
  AMBIGUOUS_MATCH: "采集链接中存在多条匹配数据",
  DIRECT_SOURCE_INCOMPLETE: "采集链接中的目标字段不完整",
  UNIT_CONVERSION_INVALID: "单位换算无效",
  RAW_OBSERVATION_INCOMPLETE: "来源原始值或单位不完整",
};

const blockerLabels: Record<string, string> = {
  EXECUTION_INCOMPLETE: "执行尚未完成",
  RESOLVED_NOT_APPROVED: "已解决但尚未审核通过",
  UNRESOLVED_NOT_CONFIRMED: "未解决项尚未人工确认",
  INVALID: "存在无效结果",
  NOT_EVALUATED: "存在未评估结果",
  ANNUAL_COHORT_NOT_FULLY_APPROVED: "年度 Top 50 尚未全部形成正式结果",
  ANNUAL_COHORT_SIZE_INVALID: "年度 Top 50 批次数量不完整",
};

export function getReasonLabel(value?: string | null) {
  return value ? reasonLabels[value] || "其他原因" : "—";
}

export function getBlockerLabel(value: string) {
  return blockerLabels[value] || "其他未完成项";
}

export const distributionOrders = {
  // 顺序体现处理阶段和关注优先级，而不是依赖对象键或服务端返回顺序。
  execution: [
    "RUNNING",
    "LEASED",
    "PENDING",
    "FAILED_RETRYABLE",
    "SUCCEEDED",
    "FAILED_FINAL",
    "DISCARDED",
  ],
  resolution: [
    "RESOLVED",
    "PARTIAL",
    "UNRESOLVED",
    "CONFLICT",
    "INVALID",
    "NOT_EVALUATED",
  ],
  review: [
    "AUTO_APPROVED",
    "APPROVED",
    "CORRECTED",
    "CONFIRMED_UNRESOLVED",
    "REJECTED",
    "SKIPPED",
    "UNREVIEWED",
  ],
  acquisition: ["DIRECT_LINK", "SEARCH_FALLBACK"],
} as const;
