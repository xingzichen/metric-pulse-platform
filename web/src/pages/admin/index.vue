<script setup lang="ts">
import { reactive, ref } from "vue";
import { useQuery, useQueryClient } from "@tanstack/vue-query";
import { ElMessage } from "element-plus";
import { api, post } from "../../api";
import type { User } from "../../types";
const tab = ref("users");
const qc = useQueryClient();
const users = useQuery({
  queryKey: ["users"],
  queryFn: () => api<{ items: User[] }>("/api/v1/admin/users"),
});
const audit = useQuery({
  queryKey: ["audit"],
  queryFn: () => api<{ items: any[] }>("/api/v1/admin/audit"),
});
const health = useQuery({
  queryKey: ["model-health"],
  queryFn: () => api<any>("/api/v1/system/model-health"),
});
const dialog = ref(false);
const form = reactive({ username: "", password: "", role: "VIEWER" });
async function create() {
  await post("/api/v1/admin/users", form);
  dialog.value = false;
  await qc.invalidateQueries({ queryKey: ["users"] });
  ElMessage.success("用户已创建");
}
</script>
<template>
  <div class="page-head">
    <div>
      <h1>系统管理</h1>
      <div class="muted">用户权限、模型连接和审计记录</div>
    </div>
  </div>
  <el-tabs v-model="tab"
    ><el-tab-pane label="用户" name="users"
      ><el-button
        type="primary"
        style="margin-bottom: 14px"
        @click="dialog = true"
        >创建用户</el-button
      ><el-table :data="users.data.value?.items"
        ><el-table-column prop="username" label="用户名" /><el-table-column
          prop="role"
          label="角色" /><el-table-column
          prop="id"
          label="ID" /></el-table></el-tab-pane
    ><el-tab-pane label="本地模型" name="model"
      ><el-card class="card"
        ><el-result
          :icon="health.data.value?.ok ? 'success' : 'warning'"
          :title="health.data.value?.ok ? 'OMLX 服务可用' : 'OMLX 服务不可用'"
          :sub-title="
            health.data.value?.model || health.data.value?.error
          " /></el-card></el-tab-pane
    ><el-tab-pane label="审计日志" name="audit"
      ><el-table :data="audit.data.value?.items"
        ><el-table-column
          prop="createdAt"
          label="时间"
          min-width="190" /><el-table-column
          prop="action"
          label="操作" /><el-table-column
          prop="resourceType"
          label="资源" /><el-table-column
          prop="resourceId"
          label="资源 ID"
          min-width="220" /></el-table></el-tab-pane></el-tabs
  ><el-dialog v-model="dialog" title="创建用户" width="450"
    ><el-form label-position="top"
      ><el-form-item label="用户名"
        ><el-input v-model="form.username" /></el-form-item
      ><el-form-item label="初始密码"
        ><el-input v-model="form.password" type="password" /></el-form-item
      ><el-form-item label="角色"
        ><el-select v-model="form.role" class="w-full"
          ><el-option
            v-for="r in ['VIEWER', 'OPERATOR', 'REVIEWER', 'ADMIN']"
            :key="r"
            :value="r" /></el-select></el-form-item></el-form
    ><template #footer
      ><el-button @click="dialog = false">取消</el-button
      ><el-button type="primary" @click="create">创建</el-button></template
    ></el-dialog
  >
</template>
