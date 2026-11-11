/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: {
    rolldownOptions: {
      output: {
        codeSplitting: {
          groups: [
            {
              name: "react-vendor",
              test: /\/node_modules\/(react|react-dom|react-router|react-router-dom|scheduler)\//,
            },
            {
              name: "markdown-highlighting",
              test: /\/node_modules\/(refractor|rehype-prism-plus)\//,
            },
            { name: "ui-icons", test: /\/node_modules\/@ant-design\/(icons|icons-svg)\// },
          ],
        },
      },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts",
  },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
        timeout: 0,
        proxyTimeout: 0,
      },
    },
  },
});
