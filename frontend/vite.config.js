import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Khi chạy dev (npm run dev), mọi lời gọi /v1 được chuyển tới API ở cổng 8000.
export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  server: { port: 5173, proxy: { "/v1": "http://localhost:8000" } },
  build: {
    chunkSizeWarningLimit: 700,
    rollupOptions: { output: { manualChunks: { charts: ["recharts"], motion: ["framer-motion"] } } },
  },
});
