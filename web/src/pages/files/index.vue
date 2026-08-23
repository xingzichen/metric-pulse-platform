<script setup lang="ts">
import { ref } from "vue";
import { useQueryClient, useQuery } from "@tanstack/vue-query";
import { ElMessage } from "element-plus";
import { api } from "../../api";
import type { FileItem } from "../../types";
import StatusTag from "../../components/StatusTag.vue";
const queryClient = useQueryClient();
const uploading = ref(false);
const query = useQuery({
  queryKey: ["files"],
  queryFn: () => api<{ items: FileItem[] }>("/api/v1/files"),
});
async function upload(options: { file: File }) {
  uploading.value = true;
  const body = new FormData();
  body.append("upload", options.file);
  try {
    await api("/api/v1/files", { method: "POST", body });
    ElMessage.success("上传并识别完成");
    await queryClient.invalidateQueries({ queryKey: ["files"] });
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : "上传失败");
  } finally {
    uploading.value = false;
  }
}
</script>
<template>
  <div class="page-head">
    <div>
      <h1>文件与识别</h1>
      <div class="muted">上传 Excel，自动识别表结构、业务键和待采集字段</div>
    </div>
  </div>
  <el-upload
    drag
    :show-file-list="false"
    accept=".xlsx"
    :http-request="upload as any"
    :disabled="uploading"
    ><div style="padding: 20px">
      <h3>{{ uploading ? "正在解析工作簿…" : "拖入 .xlsx 文件或点击上传" }}</h3>
      <p class="muted">
        上传后先做确定性结构分析，再由本地多模态模型补充语义判断
      </p>
    </div></el-upload
  ><el-card class="card" style="margin-top: 20px"
    ><el-table
      :data="query.data.value?.items"
      @row-click="(r: FileItem) => $router.push(`/files/${r.id}`)"
      ><el-table-column
        prop="originalName"
        label="文件名"
        min-width="300" /><el-table-column label="状态"
        ><template #default="s"
          ><StatusTag :value="s.row.status" /></template></el-table-column
      ><el-table-column label="大小"
        ><template #default="s"
          >{{ (s.row.size / 1024 / 1024).toFixed(2) }} MB</template
        ></el-table-column
      ><el-table-column
        prop="createdAt"
        label="上传时间"
        min-width="180" /></el-table
  ></el-card>
</template>
