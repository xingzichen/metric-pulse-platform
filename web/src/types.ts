/**
 * 前端消费的 API 数据传输对象。
 *
 * 字段名与后端视图保持一致；状态暂用 string，是为了在部署期间后端新增状态时前端不会
 * 因联合类型落后而无法编译，最终中文展示统一交给 status-display.ts。
 */
export interface User {
  id: string;
  username: string;
  role: "VIEWER" | "OPERATOR" | "REVIEWER" | "ADMIN";
}
export interface FileItem {
  id: string;
  originalName: string;
  size: number;
  status: string;
  createdAt: string;
  analysis?: WorkbookAnalysis;
  sheets?: Sheet[];
}
export interface WorkbookAnalysis {
  sheet_count: number;
  needs_confirmation: boolean;
  sheets: AnalysisSheet[];
}
export interface AnalysisSheet {
  name: string;
  headers: string[];
  display_headers: string[];
  descriptor_fields: string[];
  target_fields: string[];
  business_key_fields: string[];
  mode: string;
  excluded?: boolean;
  exclusion_reason?: {
    sheet_id: string;
    code: string;
    label: string;
  } | null;
  confidence: number;
  max_row: number;
}
export interface Sheet {
  id: string;
  name: string;
  headers: string[];
  displayHeaders: string[];
  descriptorFields: string[];
  targetFields: string[];
  businessKeyFields: string[];
  confidence: number;
  profile: Record<string, unknown>;
}
export interface Task {
  id: string;
  name: string;
  fileId: string;
  status: string;
  version: number;
  runVersion: number;
  stats: {
    // 顶层计数用于概览，四组 Counts 用于任务详情的分布图和审核进度。
    total?: number;
    pending?: number;
    running?: number;
    succeeded?: number;
    failed?: number;
    discarded?: number;
    reviewed?: number;
    resolved?: number;
    executionCounts?: Record<string, number>;
    resolutionCounts?: Record<string, number>;
    reviewCounts?: Record<string, number>;
    acquisitionCounts?: Record<string, number>;
    sourceCacheHits?: number;
  };
  allowedActions: string[];
  config?: { datasets: Dataset[] };
  createdAt: string;
  updatedAt: string;
}
export interface Dataset {
  sheet_name: string;
  descriptor_fields: string[];
  target_fields: string[];
  business_key_fields: string[];
  mode: string;
}
export interface Unit {
  id: string;
  taskId: string;
  status: string;
  // 执行、解决、审核是三条独立状态轴，不能用一个“成功/失败”字段相互替代。
  executionStatus: string;
  resolutionStatus: string;
  resolutionReason: string | null;
  reviewStatus: string;
  reviewRequired: boolean;
  riskLevel: string;
  validationVersion: string;
  targetFields: string[];
  suggestion: Record<string, unknown> | null;
  finalValues: Record<string, unknown> | null;
  validation: Record<string, unknown> | null;
  error: string | null;
  version: number;
  record?: {
    sheetName: string;
    sourceRow: number;
    rawData: Record<string, unknown>;
    rowContract: Record<string, unknown>;
  };
  evidence?: Evidence[];
  acquisitionAttempts?: SourceAcquisitionAttempt[];
  history?: unknown[];
}
export interface SourceAcquisitionAttempt {
  id: string;
  route: string;
  status: string;
  reason?: string | null;
  inputUrl?: string | null;
  normalizedUrl?: string | null;
  finalUrl?: string | null;
  contentHash?: string | null;
  cacheHit: boolean;
  persistentCacheHit: boolean;
  matchStatus?: string | null;
  matchCount: number;
  details: Record<string, unknown>;
  startedAt: string;
  endedAt?: string | null;
}
export interface Evidence {
  id: string;
  sourceUrl?: string;
  title?: string;
  locator?: string;
  excerpt?: string;
  metadata: Record<string, unknown>;
}
