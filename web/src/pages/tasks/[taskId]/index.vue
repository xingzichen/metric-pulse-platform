<script setup lang="ts">
import { computed } from "vue";
import { useRoute } from "vue-router";
import { useQuery, useQueryClient } from "@tanstack/vue-query";
import { ElMessage, ElMessageBox } from "element-plus";
import { api, post } from "../../../api";
import type { Task } from "../../../types";
import StatusTag from "../../../components/StatusTag.vue";
import StatusDistribution from "../../../components/StatusDistribution.vue";
import { getStatusPresentation } from "../../../status-display";
import { completedReviewCount } from "../../../task-stats";

// 任务详情是运行控制台：轮询任务快照，同时把三轴状态和来源路径拆开呈现。
const route = useRoute<"/tasks/[taskId]/">();
const id = String(route.params.taskId);
const qc = useQueryClient();
const query = useQuery({
  queryKey: ["task", id],
  queryFn: () => api<Task>(`/api/v1/tasks/${id}`),
  refetchInterval: 2000,
});
const repairPreview = useQuery({
  // 该接口只审计历史来源差异，绝不在打开页面时自动改写已采集结果。
  queryKey: ["source-repair-preview", id],
  queryFn: () =>
    api<{
      total: number;
      readOnly: boolean;
      applyRequiresConfirmation: boolean;
      items: Array<{
        unitId: string;
        sheetName: string;
        sourceRow: number;
        inputUrl: string;
        selectedUrl?: string | null;
        suggestion?: Record<string, unknown> | null;
        reason: string;
      }>;
    }>(`/api/v1/tasks/${id}/source-repair-preview?limit=20`),
});
const progress = computed(() => {
  // 执行进度以所有终态单元计数；业务是否解决、是否审核通过由独立指标表达。
  const s = query.data.value?.stats;
  return s?.total
    ? Math.round(
        (((s.succeeded || 0) + (s.failed || 0) + (s.discarded || 0)) /
          s.total) *
          100,
      )
    : 0;
});
const progressColor = computed(
  () => getStatusPresentation(query.data.value?.status).color,
);
const reviewed = computed(() =>
  query.data.value ? completedReviewCount(query.data.value.stats) : 0,
);
function formatDateTime(value?: string) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date(value));
}
async function control(action: string) {
  try {
    const t = query.data.value!;
    if (["stop", "delete"].includes(action))
      await ElMessageBox.confirm(
        `确认${action === "stop" ? "停止" : "删除"}任务？`,
      );
    if (action === "delete")
      await api(`/api/v1/tasks/${id}`, { method: "DELETE" });
    else
      await post(`/api/v1/tasks/${id}/${action}`, {
        // 乐观锁防止轮询期间另一位操作者的控制指令被旧页面覆盖。
        expected_version: t.version,
      });
    await qc.invalidateQueries({ queryKey: ["task", id] });
    ElMessage.success("操作成功");
  } catch (e) {
    if (e !== "cancel")
      ElMessage.error(e instanceof Error ? e.message : "操作失败");
  }
}
</script>
<template>
  <div v-if="query.data.value">
    <!-- 后端只返回当前状态允许的操作，前端不自行推测状态机迁移。 -->
    <div class="page-head">
      <div>
        <h1>{{ query.data.value.name }}</h1>
        <StatusTag :value="query.data.value.status" />
      </div>
      <div class="actions">
        <el-button
          v-for="action in query.data.value.allowedActions"
          :key="action"
          :type="
            action === 'start' || action === 'resume'
              ? 'primary'
              : action === 'stop'
                ? 'warning'
                : 'default'
          "
          @click="control(action)"
          >{{
            (
              {
                start: "开始",
                resume: "恢复",
                pause: "暂停",
                stop: "停止",
                delete: "删除",
              } as any
            )[action]
          }}</el-button
        >
      </div>
    </div>
    <div class="stat-grid">
      <div class="stat">
        <span class="muted">总采集单元</span
        ><b>{{ query.data.value.stats.total || 0 }}</b>
      </div>
      <div class="stat">
        <span class="muted">执行成功</span
        ><b>{{ query.data.value.stats.succeeded || 0 }}</b>
      </div>
      <div class="stat">
        <span class="muted">业务已解决</span
        ><b>{{ query.data.value.stats.resolved || 0 }}</b>
      </div>
      <div class="stat">
        <span class="muted">失败</span
        ><b>{{ query.data.value.stats.failed || 0 }}</b>
      </div>
      <div class="stat">
        <span class="muted">已核对</span><b>{{ reviewed }}</b>
      </div>
    </div>
    <el-card class="card"
      ><template #header><b>任务进度</b></template
      ><el-progress
        :percentage="progress"
        :stroke-width="18"
        :color="progressColor"
      /><el-descriptions :column="2" border style="margin-top: 22px"
        ><el-descriptions-item label="运行版本">{{
          query.data.value.runVersion
        }}</el-descriptions-item
        ><el-descriptions-item label="控制版本">{{
          query.data.value.version
        }}</el-descriptions-item
        ><el-descriptions-item label="创建时间">{{
          formatDateTime(query.data.value.createdAt)
        }}</el-descriptions-item
        ><el-descriptions-item label="更新时间">{{
          formatDateTime(query.data.value.updatedAt)
        }}</el-descriptions-item></el-descriptions
      >
      <div class="distribution-grid">
        <!-- 四张分布图明确区分“执行完成”“数据解决”“人工核对”和“来源路径”。 -->
        <StatusDistribution
          title="执行状态分布"
          category="execution"
          :counts="query.data.value.stats.executionCounts"
        />
        <StatusDistribution
          title="解决状态分布"
          category="resolution"
          :counts="query.data.value.stats.resolutionCounts"
        />
        <StatusDistribution
          title="审核状态分布"
          category="review"
          :counts="query.data.value.stats.reviewCounts"
        />
        <StatusDistribution
          title="来源获取方式"
          category="acquisition"
          :counts="query.data.value.stats.acquisitionCounts"
        />
      </div>
      <div class="actions" style="margin-top: 20px">
        <el-button type="primary" @click="$router.push(`/tasks/${id}/review`)"
          >进入逐行核对</el-button
        ><el-button @click="$router.push(`/tasks/${id}/exports`)"
          >导出中心</el-button
        >
      </div></el-card
    >
    <el-card
      v-if="repairPreview.data.value?.total"
      class="card source-repair-card"
      style="margin-top: 18px"
    >
      <!-- 历史修复保持预览模式，应用修复必须通过独立的二次确认流程。 -->
      <template #header>
        <div class="repair-head">
          <b>历史来源影响预览</b>
          <el-tag type="warning">
            {{ repairPreview.data.value.total }} 行待核查
          </el-tag>
        </div>
      </template>
      <el-alert
        title="这里只生成只读影响清单，不会覆盖任何历史结果；修复应用前必须再次确认。"
        type="warning"
        :closable="false"
        show-icon
      />
      <el-table :data="repairPreview.data.value.items" max-height="360">
        <el-table-column prop="sheetName" label="工作表" min-width="180" />
        <el-table-column prop="sourceRow" label="原始行" width="90" />
        <el-table-column label="输入采集链接" min-width="260">
          <template #default="scope">
            <a :href="scope.row.inputUrl" target="_blank">查看输入来源</a>
          </template>
        </el-table-column>
        <el-table-column label="实际采用来源" min-width="260">
          <template #default="scope">
            <a v-if="scope.row.selectedUrl" :href="scope.row.selectedUrl" target="_blank">
              查看采用来源
            </a>
            <span v-else class="muted">没有选中来源记录</span>
          </template>
        </el-table-column>
        <el-table-column label="审计原因" width="160">
          <template #default="scope">
            {{
              scope.row.reason === "LEGACY_ROUTE_NOT_AUDITED"
                ? "旧任务缺少路由审计"
                : "采用来源与输入不同"
            }}
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<style scoped>
.stat-grid {
  grid-template-columns: repeat(5, minmax(0, 1fr));
}
.distribution-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
  margin-top: 20px;
}
.repair-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
@media (max-width: 1100px) {
  .stat-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .distribution-grid {
    grid-template-columns: 1fr;
  }
}
</style>
