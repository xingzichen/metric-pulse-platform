import "element-plus/dist/index.css";
import "./styles.css";
import ElementPlus from "element-plus";
import { VueQueryPlugin } from "@tanstack/vue-query";
import { createPinia } from "pinia";
import { createApp } from "vue";
import { createRouter, createWebHistory } from "vue-router";
import { routes } from "vue-router/auto-routes";
import App from "./App.vue";
import { useAuth } from "./stores/auth";

// 应用只创建一个路由器、状态仓库和查询缓存，页面切换时复用已获取的数据与登录态。
const router = createRouter({ history: createWebHistory(), routes });
const pinia = createPinia();
const app = createApp(App);
app.use(pinia).use(router).use(ElementPlus).use(VueQueryPlugin);

router.beforeEach(async (to) => {
  // 首次导航先恢复服务端会话，避免页面短暂显示后再跳回登录页。
  const auth = useAuth(pinia);
  if (!auth.checked) await auth.load();
  if (to.path !== "/login" && !auth.user)
    return { path: "/login", query: { next: to.fullPath } };
  if (to.path === "/login" && auth.user) return "/";
});
app.mount("#app");
