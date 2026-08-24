import type { Task } from "./types";

// REJECTED 表示“要求重采”，并非审核闭环，不能计入已核对数量。
const completedReviewStatuses = [
  "AUTO_APPROVED",
  "APPROVED",
  "CORRECTED",
  "CONFIRMED_UNRESOLVED",
  "SKIPPED",
] as const;

export function completedReviewCount(stats: Task["stats"]) {
  // 兼容尚未返回细分统计的旧接口快照；新数据始终以 reviewCounts 为准。
  if (!stats.reviewCounts) return stats.reviewed || 0;
  return completedReviewStatuses.reduce(
    (total, status) => total + (stats.reviewCounts?.[status] || 0),
    0,
  );
}

export function pendingReviewCount(stats: Task["stats"]) {
  return Math.max(0, (stats.succeeded || 0) - completedReviewCount(stats));
}
