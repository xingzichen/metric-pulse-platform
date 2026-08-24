<script setup lang="ts">
import { computed } from "vue";
import { distributionOrders, getStatusPresentation } from "../status-display";

const props = defineProps<{
  title: string;
  counts?: Record<string, number>;
  category: keyof typeof distributionOrders;
}>();

const entries = computed(() => {
  // 过滤零值并按业务阶段排序，使大量状态下的图例仍能快速扫描。
  const order = distributionOrders[props.category] as readonly string[];
  return Object.entries(props.counts || {})
    .map(([status, rawCount]) => ({
      status,
      count: Math.max(0, Number(rawCount) || 0),
      presentation: getStatusPresentation(status),
    }))
    .filter((entry) => entry.count > 0)
    .sort((a, b) => {
      const aIndex = order.indexOf(a.status);
      const bIndex = order.indexOf(b.status);
      return (
        (aIndex < 0 ? order.length : aIndex) -
        (bIndex < 0 ? order.length : bIndex)
      );
    });
});
const total = computed(() =>
  entries.value.reduce((sum, entry) => sum + entry.count, 0),
);
function percentage(count: number) {
  if (!total.value) return 0;
  return Math.round((count / total.value) * 1000) / 10;
}
function percentageText(count: number) {
  // 极小但非零的分组保留“<0.1%”，避免界面看起来像数据丢失。
  const value = percentage(count);
  return count > 0 && value === 0 ? "<0.1%" : `${value}%`;
}
</script>

<template>
  <!-- 同一数据同时提供比例条和精确数值，兼顾趋势判断与数量核对。 -->
  <section class="distribution-card">
    <header class="distribution-head">
      <b>{{ title }}</b
      ><span>共 {{ total.toLocaleString("zh-CN") }} 项</span>
    </header>
    <template v-if="entries.length">
      <div class="distribution-bar" role="img" :aria-label="`${title}图表`">
        <span
          v-for="entry in entries"
          :key="entry.status"
          :style="{
            width: `${percentage(entry.count)}%`,
            backgroundColor: entry.presentation.color,
          }"
          :title="`${entry.presentation.label}：${entry.count} 项（${percentageText(entry.count)}）`"
        />
      </div>
      <div class="distribution-list">
        <div
          v-for="entry in entries"
          :key="entry.status"
          class="distribution-item"
        >
          <i :style="{ backgroundColor: entry.presentation.color }" />
          <span class="distribution-label">{{ entry.presentation.label }}</span>
          <strong>{{ entry.count.toLocaleString("zh-CN") }}</strong>
          <small>{{ percentageText(entry.count) }}</small>
        </div>
      </div>
    </template>
    <div v-else class="distribution-empty">暂无数据</div>
  </section>
</template>

<style scoped>
.distribution-card {
  min-width: 0;
  padding: 18px;
  border: 1px solid #e8edf5;
  border-radius: 12px;
  background: #fbfcfe;
}
.distribution-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 15px;
}
.distribution-head b {
  color: #25324b;
  font-size: 15px;
}
.distribution-head span {
  color: #8490a6;
  font-size: 12px;
  white-space: nowrap;
}
.distribution-bar {
  display: flex;
  width: 100%;
  height: 10px;
  margin-bottom: 15px;
  overflow: hidden;
  border-radius: 999px;
  background: #e9eef6;
}
.distribution-bar span {
  min-width: 2px;
  transition: width 0.25s ease;
}
.distribution-list {
  display: grid;
  gap: 10px;
}
.distribution-item {
  display: grid;
  grid-template-columns: 8px minmax(0, 1fr) auto 44px;
  align-items: center;
  gap: 8px;
  min-height: 20px;
}
.distribution-item i {
  width: 8px;
  height: 8px;
  border-radius: 50%;
}
.distribution-label {
  overflow: hidden;
  color: #4b5870;
  font-size: 13px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.distribution-item strong {
  color: #1d2940;
  font-size: 13px;
  font-variant-numeric: tabular-nums;
}
.distribution-item small {
  color: #8a95a9;
  font-size: 12px;
  text-align: right;
  font-variant-numeric: tabular-nums;
}
.distribution-empty {
  display: grid;
  min-height: 74px;
  place-items: center;
  color: #9aa4b5;
  font-size: 13px;
}
</style>
