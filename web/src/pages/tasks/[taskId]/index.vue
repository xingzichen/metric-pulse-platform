<script setup lang="ts">
import { computed } from "vue";
import { useRoute } from "vue-router";
import { useQuery, useQueryClient } from "@tanstack/vue-query";
import { ElMessage, ElMessageBox } from "element-plus";
import { api, post } from "../../../api";
import type { Task } from "../../../types";
import StatusTag from "../../../components/StatusTag.vue";
const route = useRoute<"/tasks/[taskId]/">();
const id = String(route.params.taskId);
const qc = useQueryClient();
const query = useQuery({
  queryKey: ["task", id],
  queryFn: () => api<Task>(`/api/v1/tasks/${id}`),
  refetchInterval: 2000,
});
const progress = computed(() => {
  const s = query.data.value?.stats;
  return s?.total
    ? Math.round(
        (((s.succeeded || 0) + (s.failed || 0) + (s.discarded || 0)) /
          s.total) *
          100,
      )
    : 0;
});
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
        <span class="muted">成功</span
        ><b>{{ query.data.value.stats.succeeded || 0 }}</b>
      </div>
      <div class="stat">
        <span class="muted">失败</span
        ><b>{{ query.data.value.stats.failed || 0 }}</b>
      </div>
      <div class="stat">
        <span class="muted">已核对</span
        ><b>{{ query.data.value.stats.reviewed || 0 }}</b>
      </div>
    </div>
    <el-card class="card"
      ><template #header><b>任务进度</b></template
      ><el-progress :percentage="progress" :stroke-width="18" /><el-descriptions
        :column="2"
        border
        style="margin-top: 22px"
        ><el-descriptions-item label="运行版本">{{
          query.data.value.runVersion
        }}</el-descriptions-item
        ><el-descriptions-item label="控制版本">{{
          query.data.value.version
        }}</el-descriptions-item
        ><el-descriptions-item label="创建时间">{{
          query.data.value.createdAt
        }}</el-descriptions-item
        ><el-descriptions-item label="更新时间">{{
          query.data.value.updatedAt
        }}</el-descriptions-item></el-descriptions
      >
      <div class="actions" style="margin-top: 20px">
        <el-button type="primary" @click="$router.push(`/tasks/${id}/review`)"
          >进入逐行核对</el-button
        ><el-button @click="$router.push(`/tasks/${id}/exports`)"
          >导出中心</el-button
        >
      </div></el-card
    >
  </div>
</template>
