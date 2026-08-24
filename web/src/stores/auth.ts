import { defineStore } from "pinia";
import { ref } from "vue";
import { api, post } from "../api";
import type { User } from "../types";

export const useAuth = defineStore("auth", () => {
  // checked 区分“尚未查询会话”和“已确认未登录”，防止路由守卫重复请求。
  const user = ref<User | null>(null);
  const checked = ref(false);
  async function load() {
    // 401 与网络错误都清空本地身份；具体错误由需要交互的登录页负责提示。
    try {
      user.value = await api<User>("/api/v1/auth/me");
    } catch {
      user.value = null;
    } finally {
      checked.value = true;
    }
  }
  async function login(username: string, password: string) {
    user.value = await post<User>("/api/v1/auth/login", { username, password });
  }
  async function logout() {
    await post("/api/v1/auth/logout");
    user.value = null;
  }
  return { user, checked, load, login, logout };
});
