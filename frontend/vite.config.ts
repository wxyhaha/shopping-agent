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
              name: "react-runtime",
              test: /node_modules[\\/](react|react-dom|scheduler)[\\/]/,
              priority: 20,
              minSize: 20 * 1024,
            },
            {
              name: "data-routing",
              test: /node_modules[\\/](?:@tanstack|react-router|react-router-dom)[\\/]/,
              priority: 10,
              minSize: 15 * 1024,
            },
            {
              name: "design-system",
              test: /node_modules[\\/](?:antd|@ant-design|@rc-component|rc-[^\\/]+|dayjs)[\\/]/,
              priority: 5,
              minSize: 20 * 1024,
            },
          ],
        },
      },
    },
  },
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8000",
      "/health": "http://127.0.0.1:8000",
    },
  },
});
