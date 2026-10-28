import { defineConfig } from "wxt";

export default defineConfig({
  modules: ["@wxt-dev/module-react"],
  manifest: {
    name: "Shopping Agent 商品助手",
    description: "在淘宝、天猫和京东页面快速加入本地比较任务。",
    permissions: ["activeTab", "storage"],
    host_permissions: [
      "http://127.0.0.1/*",
      "https://127.0.0.1/*",
      "http://localhost/*",
      "https://localhost/*",
    ],
    action: {
      default_title: "加入商品比较",
    },
  },
});
