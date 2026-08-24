<script setup lang="ts">
import { ref } from "vue";
import { useQuery, useQueryClient } from "@tanstack/vue-query";
import { ElMessage } from "element-plus";
import { api, post } from "../../api";
import type { FileItem } from "../../types";
import StatusTag from "../../components/StatusTag.vue";

// 模板引用已识别文件的稳定结构；创建新版本与发布为两个显式步骤。
const dialog = ref(false),
  name = ref(""),
  fileId = ref("");
const qc = useQueryClient();
const templates = useQuery({
  queryKey: ["templates"],
  queryFn: () => api<{ items: any[] }>("/api/v1/templates"),
});
const files = useQuery({
  queryKey: ["files"],
  queryFn: () => api<{ items: FileItem[] }>("/api/v1/files"),
});
async function create() {
  await post("/api/v1/templates", { name: name.value, file_id: fileId.value });
  dialog.value = false;
  await qc.invalidateQueries({ queryKey: ["templates"] });
  ElMessage.success("模板版本已创建");
}
async function publish(id: string) {
  // 发布后刷新列表，使同名模板的版本状态以服务端结果为准。
  await post(`/api/v1/templates/${id}/publish`);
  await qc.invalidateQueries({ queryKey: ["templates"] });
}
</script>
<template>
  <!-- 模板仅减少重复配置，不携带任何测试对照数据或所谓金标语义。 -->
  <div class="page-head">
    <div>
      <h1>模板中心</h1>
      <div class="muted">沉淀稳定的表结构和采集约束，减少后续人工配置</div>
    </div>
    <el-button type="primary" @click="dialog = true">从文件创建模板</el-button>
  </div>
  <el-card class="card"
    ><el-table :data="templates.data.value?.items"
      ><el-table-column prop="name" label="模板" /><el-table-column
        prop="version"
        label="版本"
      /><el-table-column label="状态"
        ><template #default="s"
          ><StatusTag :value="s.row.status" /></template></el-table-column
      ><el-table-column prop="structureHash" label="结构哈希" /><el-table-column
        label="操作"
        ><template #default="s"
          ><el-button
            v-if="s.row.status !== 'PUBLISHED'"
            link
            type="primary"
            @click="publish(s.row.id)"
            >发布</el-button
          ></template
        ></el-table-column
      ></el-table
    ></el-card
  ><el-dialog v-model="dialog" title="创建模板" width="480"
    ><el-form label-position="top"
      ><el-form-item label="模板名称"><el-input v-model="name" /></el-form-item
      ><el-form-item label="来源文件"
        ><el-select v-model="fileId" class="w-full"
          ><el-option
            v-for="f in files.data.value?.items"
            :key="f.id"
            :label="f.originalName"
            :value="f.id" /></el-select></el-form-item></el-form
    ><template #footer
      ><el-button @click="dialog = false">取消</el-button
      ><el-button type="primary" @click="create">创建</el-button></template
    ></el-dialog
  >
</template>
