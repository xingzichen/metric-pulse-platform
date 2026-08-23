import vue from "@vitejs/plugin-vue";
import { defineConfig } from "vite";
import VueRouter from "vue-router/vite";

export default defineConfig({
  // The plugin scans pages at startup. Disabling its extra watcher avoids
  // exhausting Synology/macOS file descriptors in large shared workspaces.
  plugins: [VueRouter({ watch: false }), vue()],
  server: {
    proxy: {
      "/api": { target: "http://localhost:8000", changeOrigin: true },
      "/health": { target: "http://localhost:8000", changeOrigin: true },
    },
  },
});
