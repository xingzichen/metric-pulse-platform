<script setup lang="ts">
import { useQuery } from "@tanstack/vue-query";
import { api } from "../api";
import type { FileItem, Task } from "../types";
import StatusTag from "../components/StatusTag.vue";
import { completedReviewCount, pendingReviewCount } from "../task-stats";

// 工作台聚合任务与文件两个接口；任务短轮询用于及时反映后台串行采集进度。
const tasks = useQuery({
  queryKey: ["tasks"],
  queryFn: () => api<{ items: Task[] }>("/api/v1/tasks"),
  refetchInterval: 3000,
});
const files = useQuery({
  queryKey: ["files"],
  queryFn: () => api<{ items: FileItem[] }>("/api/v1/files"),
});
</script>
<template>
  <!-- 顶部指标帮助操作者优先发现正在执行和等待人工核对的工作。 -->
  <div class="page-head">
    <div>
      <h1>工作台</h1>
      <div class="muted">今天需要关注的采集进度与审核积压</div>
    </div>
    <el-button type="primary" @click="$router.push('/tasks/new')"
      >创建任务</el-button
    >
  </div>
  <div class="stat-grid">
    <div class="stat">
      <span class="muted">任务总数</span
      ><b>{{ tasks.data.value?.items.length ?? 0 }}</b>
    </div>
    <div class="stat">
      <span class="muted">执行中</span
      ><b>{{
        tasks.data.value?.items.filter((x) =>
          ["RUNNING", "QUEUED"].includes(x.status),
        ).length ?? 0
      }}</b>
    </div>
    <div class="stat">
      <span class="muted">待核对</span
      ><b>{{
        tasks.data.value?.items.reduce(
          (n, x) => n + pendingReviewCount(x.stats),
          0,
        ) ?? 0
      }}</b>
    </div>
    <div class="stat">
      <span class="muted">已上传文件</span
      ><b>{{ files.data.value?.items.length ?? 0 }}</b>
    </div>
  </div>
  <el-card class="card"
    ><template #header><b>最近任务</b></template
    ><el-table
      :data="tasks.data.value?.items"
      @row-click="(row: Task) => $router.push(`/tasks/${row.id}`)"
      ><el-table-column
        prop="name"
        label="任务"
        min-width="220" /><el-table-column label="状态"
        ><template #default="s"
          ><StatusTag :value="s.row.status" /></template></el-table-column
      ><el-table-column label="进度" min-width="200"
        ><template #default="s"
          ><el-progress
            :percentage="
              s.row.stats.total
                ? Math.round(
                    (((s.row.stats.succeeded || 0) +
                      (s.row.stats.failed || 0)) /
                      s.row.stats.total) *
                      100,
                  )
                : 0
            " /></template></el-table-column
      ><el-table-column label="已核对"
        ><template #default="s">{{
          completedReviewCount(s.row.stats)
        }}</template></el-table-column
      ><el-table-column
        prop="updatedAt"
        label="更新时间"
        min-width="180" /></el-table
  ></el-card>
</template>
