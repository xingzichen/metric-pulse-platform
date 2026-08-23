<script setup lang="ts">
import { reactive, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ElMessage } from "element-plus";
import { useAuth } from "../stores/auth";
const form = reactive({ username: "admin", password: "" });
const busy = ref(false);
const auth = useAuth();
const router = useRouter();
const route = useRoute();
async function submit() {
  busy.value = true;
  try {
    await auth.login(form.username, form.password);
    await router.push(String(route.query.next || "/"));
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : "登录失败");
  } finally {
    busy.value = false;
  }
}
</script>
<template>
  <main
    style="
      min-height: 100vh;
      display: grid;
      place-items: center;
      background: linear-gradient(135deg, #101b37, #244b95);
    "
  >
    <el-card style="width: 390px; padding: 18px" class="card"
      ><h1>Metric Pulse</h1>
      <p class="muted">智能 Excel 数据采集与核对</p>
      <el-form label-position="top" @submit.prevent="submit"
        ><el-form-item label="用户名"
          ><el-input v-model="form.username" autofocus /></el-form-item
        ><el-form-item label="密码"
          ><el-input
            v-model="form.password"
            type="password"
            show-password
            @keyup.enter="submit" /></el-form-item
        ><el-button
          type="primary"
          class="w-full"
          size="large"
          :loading="busy"
          @click="submit"
          >登录</el-button
        ></el-form
      ></el-card
    >
  </main>
</template>
