import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

export default defineConfig({
  plugins: [vue()],
  server: {
    proxy: {
      // 前端调用 /api → 后端 FastAPI
      "/api": {
        target: "http://backend:8000",
        changeOrigin: true,
        rewrite: p => p.replace(/^\/api/, "")
      }
    }
  }
});
