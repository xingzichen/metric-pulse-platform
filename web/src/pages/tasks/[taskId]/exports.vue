<script setup lang="ts">
import { useRoute } from "vue-router";
import { useQuery, useQueryClient } from "@tanstack/vue-query";
import { ElMessage } from "element-plus";
import { api, post } from "../../../api";
import StatusTag from "../../../components/StatusTag.vue";
const id = String(useRoute<"/tasks/[taskId]/exports">().params.taskId);
const qc = useQueryClient();
const ready = useQuery({
  queryKey: ["readiness", id],
  queryFn: () =>
    api<{ ready: boolean; blockers: { code: string; count: number }[] }>(
      `/api/v1/tasks/${id}/export-readiness`,
    ),
});
const jobs = useQuery({
  queryKey: ["exports", id],
  queryFn: () =>
    api<{
      items: {
        id: string;
        status: string;
        createdAt: string;
        error?: string;
      }[];
    }>(`/api/v1/tasks/${id}/exports`),
});
async function create() {
  try {
    await post(`/api/v1/tasks/${id}/exports`);
    await qc.invalidateQueries({ queryKey: ["exports", id] });
    ElMessage.success("正式导出已生成");
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : "导出失败");
  }
}
</script>
<template>
  <div class="page-head">
    <div>
      <h1>导出中心</h1>
      <div class="muted">只有完成核对、无失败或驳回的数据才允许正式导出</div>
    </div>
    <el-button
      type="primary"
      :disabled="!ready.data.value?.ready"
      @click="create"
      >生成正式导出</el-button
    >
  </div>
  <el-alert
    v-if="!ready.data.value?.ready"
    title="尚未满足导出门禁"
    type="warning"
    show-icon
    :closable="false"
    ><template #default
      ><span
        v-for="b in ready.data.value?.blockers"
        :key="b.code"
        style="margin-right: 16px"
        >{{ b.code }}：{{ b.count }}</span
      ></template
    ></el-alert
  ><el-alert
    v-else
    title="所有采集结果均已完成核对，可以导出"
    type="success"
    show-icon
    :closable="false"
  /><el-card class="card" style="margin-top: 18px"
    ><el-table :data="jobs.data.value?.items"
      ><el-table-column
        prop="createdAt"
        label="生成时间"
        min-width="200"
      /><el-table-column label="状态"
        ><template #default="s"
          ><StatusTag :value="s.row.status" /></template></el-table-column
      ><el-table-column prop="error" label="错误" /><el-table-column
        label="操作"
        ><template #default="s"
          ><el-button
            v-if="s.row.status === 'READY'"
            type="primary"
            link
            tag="a"
            :href="`/api/v1/exports/${s.row.id}/download`"
            >下载</el-button
          ></template
        ></el-table-column
      ></el-table
    ></el-card
  >
</template>
