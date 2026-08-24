<script setup lang="ts">
import { computed, reactive, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useQuery } from "@tanstack/vue-query";
import { ElMessage } from "element-plus";
import { api, post } from "../../api";
import type { AnalysisSheet, Dataset, FileItem, Task } from "../../types";

// 创建页把文件识别建议转换成可编辑的任务数据集配置，提交后由后端冻结行契约。
const route = useRoute();
const router = useRouter();
const form = reactive<{
  file_id: string;
  name: string;
  datasets: Dataset[];
  start_immediately: boolean;
}>({
  file_id: String(route.query.fileId || ""),
  name: "",
  datasets: [],
  start_immediately: false,
});
const files = useQuery({
  queryKey: ["files"],
  queryFn: () => api<{ items: FileItem[] }>("/api/v1/files"),
});
const detail = useQuery({
  queryKey: ["new-task-file", computed(() => form.file_id)],
  enabled: computed(() => !!form.file_id),
  queryFn: () => api<FileItem>(`/api/v1/files/${form.file_id}`),
});
const excludedSheets = computed(() =>
  (detail.data.value?.analysis?.sheets || []).filter(
    (sheet: AnalysisSheet) => sheet.excluded,
  ),
);
const exclusionSummary = computed(() =>
  excludedSheets.value
    .map(
      (sheet: AnalysisSheet) =>
        `${sheet.name}（${sheet.exclusion_reason?.label || "不由本平台处理"}）`,
    )
    .join("；"),
);
watch(
  () => detail.data.value?.analysis,
  (analysis) => {
    if (!analysis) return;
    // 只为存在目标字段的工作表创建采集计划，仍允许用户在提交前调整字段角色。
    form.datasets = analysis.sheets
      .filter((x: AnalysisSheet) => x.target_fields.length)
      .map((x: AnalysisSheet) => ({
        sheet_name: x.name,
        descriptor_fields: [...x.descriptor_fields],
        target_fields: [...x.target_fields],
        business_key_fields: [...x.business_key_fields],
        mode: x.mode,
      }));
  },
  { immediate: true },
);
async function submit() {
  try {
    const task = await post<Task>("/api/v1/tasks", form);
    ElMessage.success("任务创建成功");
    await router.push(`/tasks/${task.id}`);
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : "创建失败");
  }
}
</script>
<template>
  <!-- 配置按工作表折叠，避免多表文件一次铺开造成操作负担。 -->
  <div class="page-head">
    <div>
      <h1>创建采集任务</h1>
      <div class="muted">模型建议可以调整，提交后行约束将冻结以保证可追溯</div>
    </div>
  </div>
  <el-alert
    v-if="excludedSheets.length"
    title="已按业务边界排除以下工作表"
    :description="exclusionSummary"
    type="info"
    :closable="false"
    show-icon
    style="margin-bottom: 18px"
  />
  <el-form label-position="top"
    ><el-card class="card"
      ><el-form-item label="任务名称"
        ><el-input
          v-model="form.name"
          placeholder="例如：2026 年人工智能数据更新" /></el-form-item
      ><el-form-item label="源文件"
        ><el-select v-model="form.file_id" class="w-full" filterable
          ><el-option
            v-for="f in files.data.value?.items"
            :key="f.id"
            :label="f.originalName"
            :value="f.id" /></el-select></el-form-item></el-card
    ><el-card class="card" style="margin-top: 18px"
      ><template #header><b>工作表采集方案</b></template
      ><el-collapse
        ><el-collapse-item
          v-for="set in form.datasets"
          :key="set.sheet_name"
          :title="set.sheet_name"
          ><el-form-item label="采集模式"
            ><el-radio-group v-model="set.mode"
              ><el-radio-button value="row_contract_collect"
                >已有行补全</el-radio-button
              ><el-radio-button value="snapshot_build"
                >完整快照构建</el-radio-button
              ></el-radio-group
            ></el-form-item
          ><el-form-item label="描述字段"
            ><el-select v-model="set.descriptor_fields" multiple class="w-full"
              ><el-option
                v-for="x in detail.data.value?.analysis?.sheets.find(
                  (s) => s.name === set.sheet_name,
                )?.headers"
                :key="x"
                :value="x" /></el-select></el-form-item
          ><el-form-item label="目标字段"
            ><el-select v-model="set.target_fields" multiple class="w-full"
              ><el-option
                v-for="x in detail.data.value?.analysis?.sheets.find(
                  (s) => s.name === set.sheet_name,
                )?.headers"
                :key="x"
                :value="x" /></el-select></el-form-item
          ><el-form-item label="业务键"
            ><el-select
              v-model="set.business_key_fields"
              multiple
              class="w-full"
              ><el-option
                v-for="x in detail.data.value?.analysis?.sheets.find(
                  (s) => s.name === set.sheet_name,
                )?.headers"
                :key="x"
                :value="
                  x
                " /></el-select></el-form-item></el-collapse-item></el-collapse
    ></el-card>
    <div style="margin-top: 20px; text-align: right">
      <el-checkbox v-model="form.start_immediately">创建后立即开始</el-checkbox
      ><el-button
        type="primary"
        style="margin-left: 16px"
        :disabled="!form.file_id || !form.name"
        @click="submit"
        >创建任务</el-button
      >
    </div></el-form
  >
</template>
