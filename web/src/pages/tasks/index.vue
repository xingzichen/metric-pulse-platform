<script setup lang="ts">
import { ref } from "vue";
import { useQuery } from "@tanstack/vue-query";
import { api } from "../../api";
import type { Task } from "../../types";
import StatusTag from "../../components/StatusTag.vue";
const status = ref("");
const query = useQuery({
  queryKey: ["tasks", status],
  queryFn: () =>
    api<{ items: Task[] }>(
      `/api/v1/tasks${status.value ? `?status=${status.value}` : ""}`,
    ),
  refetchInterval: 3000,
});
</script>
<template>
  <div class="page-head">
    <div>
      <h1>采集任务</h1>
      <div class="muted">控制执行、观察进度、进入核对和导出</div>
    </div>
    <el-button type="primary" @click="$router.push('/tasks/new')"
      >创建任务</el-button
    >
  </div>
  <el-card class="card"
    ><div style="margin-bottom: 16px">
      <el-select
        v-model="status"
        clearable
        placeholder="全部状态"
        style="width: 200px"
        ><el-option
          v-for="x in [
            'RUNNING',
            'PAUSED',
            'SUCCEEDED',
            'SUCCEEDED_WITH_ERRORS',
            'FAILED',
            'STOPPED',
          ]"
          :key="x"
          :value="x"
      /></el-select>
    </div>
    <el-table
      :data="query.data.value?.items"
      @row-click="(r: Task) => $router.push(`/tasks/${r.id}`)"
      ><el-table-column
        prop="name"
        label="名称"
        min-width="220" /><el-table-column label="状态"
        ><template #default="s"
          ><StatusTag :value="s.row.status" /></template></el-table-column
      ><el-table-column label="完成"
        ><template #default="s"
          >{{ s.row.stats.succeeded || 0 }} /
          {{ s.row.stats.total || 0 }}</template
        ></el-table-column
      ><el-table-column label="待核对"
        ><template #default="s">{{
          (s.row.stats.succeeded || 0) - (s.row.stats.reviewed || 0)
        }}</template></el-table-column
      ><el-table-column
        prop="updatedAt"
        label="更新时间"
        min-width="190" /></el-table
  ></el-card>
</template>
