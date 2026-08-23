<script setup lang="ts">
import {
  DataAnalysis,
  Document,
  Files,
  Setting,
  UploadFilled,
} from "@element-plus/icons-vue";
import { useRouter } from "vue-router";
import { useAuth } from "./stores/auth";
const auth = useAuth();
const router = useRouter();
async function logout() {
  await auth.logout();
  await router.push("/login");
}
</script>

<template>
  <router-view v-if="$route.path === '/login'" />
  <el-container v-else class="shell">
    <el-aside width="226px" class="sidebar">
      <div class="brand">
        <div class="logo">MP</div>
        <div><strong>Metric Pulse</strong><small>智能采集平台</small></div>
      </div>
      <el-menu router :default-active="$route.path">
        <el-menu-item index="/"
          ><el-icon><DataAnalysis /></el-icon>工作台</el-menu-item
        >
        <el-menu-item index="/files"
          ><el-icon><UploadFilled /></el-icon>文件与识别</el-menu-item
        >
        <el-menu-item index="/tasks"
          ><el-icon><Files /></el-icon>采集任务</el-menu-item
        >
        <el-menu-item index="/templates"
          ><el-icon><Document /></el-icon>模板中心</el-menu-item
        >
        <el-menu-item v-if="auth.user?.role === 'ADMIN'" index="/admin"
          ><el-icon><Setting /></el-icon>系统管理</el-menu-item
        >
      </el-menu>
      <div class="account">
        <span>{{ auth.user?.username }}</span
        ><el-button text @click="logout">退出</el-button>
      </div>
    </el-aside>
    <el-container
      ><el-header
        ><span class="muted">让人工只处理真正需要判断的数据</span></el-header
      ><el-main><router-view /></el-main
    ></el-container>
  </el-container>
</template>
