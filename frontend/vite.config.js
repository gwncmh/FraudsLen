import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Khi chạy dev (npm run dev), mọi lời gọi /v1 được chuyển tới API ở cổng 8000.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { "/v1": "http://localhost:8000" } },
});
