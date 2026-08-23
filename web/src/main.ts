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

const router = createRouter({ history: createWebHistory(), routes });
const pinia = createPinia();
const app = createApp(App);
app.use(pinia).use(router).use(ElementPlus).use(VueQueryPlugin);

router.beforeEach(async (to) => {
  const auth = useAuth(pinia);
  if (!auth.checked) await auth.load();
  if (to.path !== "/login" && !auth.user)
    return { path: "/login", query: { next: to.fullPath } };
  if (to.path === "/login" && auth.user) return "/";
});
app.mount("#app");
