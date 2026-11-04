import { fileURLToPath } from "node:url";
import { randomUUID } from "node:crypto";
import { rename, unlink, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { build } from "vite";

const outDir = fileURLToPath(new URL("../../backend/app/browser_assets", import.meta.url));
const result = await build({
  configFile: false,
  build: {
    target: "es2022",
    outDir,
    write: false,
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

// Windows can reject truncating a file currently memory-mapped by a reader. Generate
// completely before replacement so running collectors retain the prior complete bundle.
for (const output of Array.isArray(result) ? result : [result]) {
  for (const asset of output.output) {
    const target = join(outDir, asset.fileName);
    const temporary = `${target}.${randomUUID()}.tmp`;
    try {
      await writeFile(temporary, asset.type === "chunk" ? asset.code : asset.source, { flag: "wx" });
      await rename(temporary, target);
    } finally {
      await unlink(temporary).catch(error => { if (error.code !== "ENOENT") throw error; });
    }
  }
}
