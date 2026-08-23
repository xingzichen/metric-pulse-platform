import { defineStore } from "pinia";
import { ref } from "vue";
import { api, post } from "../api";
import type { User } from "../types";

export const useAuth = defineStore("auth", () => {
  const user = ref<User | null>(null);
  const checked = ref(false);
  async function load() {
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
