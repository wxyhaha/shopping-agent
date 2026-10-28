# 当前项目状态

T010、T020 已完成，文档基线提交为 `bdf85c1`；T020 实施成果正在提交。用户确认 T020 本轮验收通过。T030 商品真实采集与证据记录已启动，当前仍未实现采集能力。任务状态以 [PLAN.md](PLAN.md) 为准，技术与环境探针证据见 [T020_PROBES.md](docs/T020_PROBES.md)。

## 已实现

- React + TypeScript + Vite 本地网页：建立比较任务、添加商品链接、查看最近任务和任务详情。应用固定在视口内，主内容区独立滚动，宽屏详情内容区已加宽。
- WXT + Manifest V3 扩展：淘宝、天猫、京东页面右下方固定显示 Shadow DOM 隔离的悬浮助手；记住最近目标任务，页面标题仅在面板预览，用户单击后由扩展后台校验并写入本机共享 API。目标选择器可切换任务或新建任务。
- FastAPI + Pydantic API、SQLite + SQLAlchemy + Alembic 数据迁移。任务和商品链接存入服务所在系统的数据目录，重启后保留。
- URL/请求校验、创建与加项幂等处理、OpenAPI 生成的共享 TypeScript 契约；任务名称及商品页标题可编辑，任务可二次确认删除且级联清除所属链接，商品链接也可单独移除。
- 任务详情使用短链接文字跳转商品页；疑似购物车、订单或账号页会提示核对。扩展对淘宝、天猫、京东声明静态内容脚本匹配，并申请 loopback 后台 host permission、`activeTab` 和 `storage`；仅支持的商品详情地址开放添加。
- Chat Completions 兼容的 AI 任务标题生成已接入；任务名归纳品类/用途，不以单个商品品牌或完整标题命名。只在创建任务时生成一次，之后商品变更不会自动改名，任务名仍可手动编辑。只发送已读取的商品页标题，不发送原始 URL。当前不采集页面、不运行商品分析、不生成商品结论。

## 已验证

- WSL Python 3.10.12、Node.js 24.22.1、uv 0.13.6；依赖版本锁在 `backend/uv.lock` 与 `frontend/package-lock.json`。
- API 与 SQLite/Alembic 自动化检查：8 项通过，包括非法 URL、幂等、404 和服务重启后恢复。
- Ruff 检查与格式检查、前端 TypeScript 类型检查通过；OpenAPI JSON 与 TypeScript 类型已更新。
- Vite 网页生产构建成功；WXT 分别构建 Chrome MV3 与 Edge MV3 成功。此前 npm audit 报告 0 个已知漏洞。
- 本轮字号和滚动布局调整后，前端类型检查、网页生产构建通过；本地页、JS 与 CSS 入口均返回 HTTP 200。用户确认本轮 T020 人工验收通过。
- WSL 服务迁移、健康检查和生产静态网页可用；Windows PowerShell 访问 WSL `127.0.0.1:8000/health/ready` 返回 `ready`。
- 本轮新增删除 API 已进入 OpenAPI；重启后的 WSL 服务 readiness 为 `ready`。前端 `npm run typecheck`、`npm run build:web`、`npm run build:chrome`、`npm run build:edge` 均通过；任务删除与所属链接级联纳入用户确认通过的本轮验收。
- LangGraph SQLite 最小探针成功暂停、关闭并重开检查点数据库、继续执行。
- 用户截图报告本地工作台空白及 Ant Design 键盘常量异常。移除 Rolldown 设计系统分组的 `maxSize` 拆分后，最新网页构建输出单一 479.09 kB 设计系统 chunk，无 chunk size 警告。后台 readiness、网页入口和 JS 入口分别返回 `ready`、HTTP 200、HTTP 200；用户之后确认本轮 T020 人工验收通过。

## T030 尚未交付

- 用户确认 T020 本轮验收通过；没有单独留存逐项录像或浏览器日志。面板可见的截图反馈以及当前验收结论见 [T020 探针](docs/T020_PROBES.md)。
- 淘宝/天猫匿名请求未返回标题或可见正文；京东匿名响应只有静态标题等有限信号。所选 SKU、价格条件、详情图和登录态尚未验证。这些是 T030 的采集问题，不属于 T020 完成能力。
- 用户确认网关协议为 OpenAI Chat Completions 兼，标题模型 ID 为 `gemini-3.8-flash-high`；私有配置已写入忽略的本机 `.env`。当前中性命名提示词用含品牌的单商品合成标题调用后返回“家用网线比较”；未创建或修改任务。
- LangGraph 探针没有接入任务执行器，调度、崩溃重放和任务恢复未验证。
- Windows 本机独立恢复、Docker/DeepTutor 挂载、人工视觉与键盘体验、学习者掌握情况。

本轮 T020 代码和记录按用户授权提交本地 Git；未获本轮推送指令。主分支此前的文档提交 `bdf85c1` 未推送。项目运行不访问或修改 `.deeptutor/`。

恢复工作先读 [AGENTS.md](AGENTS.md) 与 [HANDOFF.md](HANDOFF.md)，再核对当前工作区和 [PLAN.md](PLAN.md)。
