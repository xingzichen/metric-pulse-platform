<script setup lang="ts">
import { computed, ref } from "vue";
import { useRoute } from "vue-router";
import { useQuery } from "@tanstack/vue-query";
import { api, post } from "../../api";
import type { FileItem, Sheet } from "../../types";
import StatusTag from "../../components/StatusTag.vue";
import { ElMessage } from "element-plus";

// 文件详情展示识别后的工作表契约；active 只控制预览，不修改服务端分析结果。
const route = useRoute<"/files/[fileId]">();
const id = String(route.params.fileId);
const active = ref("");
const query = useQuery({
  queryKey: ["file", id],
  queryFn: () => api<FileItem>(`/api/v1/files/${id}`),
});
const selected = computed(
  () =>
    query.data.value?.sheets?.find((x: Sheet) => x.name === active.value) ||
    query.data.value?.sheets?.[0],
);
async function recognize() {
  // 重新识别是显式人工操作，避免页面访问时重复占用唯一的本地模型通道。
  await post(`/api/v1/files/${id}/recognize`);
  ElMessage.success("已提交多模态识别");
}
function selectSheet(value: string | number) {
  active.value = String(value);
}
</script>
<template>
  <div v-if="query.data.value">
    <!-- 左侧切换工作表，右侧同时展示渲染预览和模型识别出的字段角色。 -->
    <div class="page-head">
      <div>
        <h1>{{ query.data.value.originalName }}</h1>
        <StatusTag :value="query.data.value.status" />
      </div>
      <div class="actions">
        <el-button @click="recognize">重新语义识别</el-button
        ><el-button
          type="primary"
          @click="$router.push({ path: '/tasks/new', query: { fileId: id } })"
          >用此文件创建任务</el-button
        >
      </div>
    </div>
    <div class="split">
      <el-card class="card"
        ><template #header>工作表</template
        ><el-menu :default-active="selected?.name" @select="selectSheet"
          ><el-menu-item
            v-for="sheet in query.data.value.sheets"
            :key="sheet.id"
            :index="sheet.name"
            ><span>{{ sheet.name }}</span
            ><el-tag size="small" style="margin-left: auto"
              >{{ Math.round(sheet.confidence * 100) }}%</el-tag
            ></el-menu-item
          ></el-menu
        ></el-card
      ><el-card v-if="selected" class="card"
        ><template #header
          ><b>{{ selected.name }}</b></template
        ><img
          :src="`/api/v1/files/${id}/preview/${encodeURIComponent(selected.name)}`"
          style="
            width: 100%;
            max-height: 330px;
            object-fit: contain;
            border: 1px solid #eee;
          "
        /><el-descriptions :column="1" border style="margin-top: 16px"
          ><el-descriptions-item label="描述字段">{{
            selected.descriptorFields.join("、")
          }}</el-descriptions-item
          ><el-descriptions-item label="待采集字段">{{
            selected.targetFields.join("、")
          }}</el-descriptions-item
          ><el-descriptions-item label="业务键">{{
            selected.businessKeyFields.join("、")
          }}</el-descriptions-item
          ><el-descriptions-item label="模式">{{
            String(selected.profile.mode || "")
          }}</el-descriptions-item></el-descriptions
        ></el-card
      >
    </div>
  </div>
</template>
