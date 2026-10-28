import { fileURLToPath } from "node:url";
import { build } from "vite";

await build({
  configFile: false,
  build: {
    target: "es2022",
    outDir: fileURLToPath(new URL("../../backend/app/browser_assets", import.meta.url)),
    emptyOutDir: false,
    minify: false,
    sourcemap: false,
    lib: {
      entry: fileURLToPath(new URL("../src/extension/backend-collector.ts", import.meta.url)),
      name: "ShoppingAgentCollector",
      formats: ["iife"],
      fileName: () => "collector.js",
    },
  },
});
