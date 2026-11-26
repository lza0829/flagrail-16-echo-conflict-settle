import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
export default defineConfig({
  plugins: [react()],
  // host:true → 绑 0.0.0.0，保证 http://127.0.0.1:6800 与容器外映射都可达
  // （vite 默认 localhost 在本机可能只解析到 IPv6 ::1）。
  server: { host: true, port: 6800, proxy: { "/api": "http://127.0.0.1:11800" } },
  preview: { host: true, port: 6800, proxy: { "/api": "http://127.0.0.1:11800" } },
});
