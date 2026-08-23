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
  stats: Record<string, number>;
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
  reviewStatus: string;
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
  history?: unknown[];
}
export interface Evidence {
  id: string;
  sourceUrl?: string;
  title?: string;
  locator?: string;
  excerpt?: string;
  metadata: Record<string, unknown>;
}
