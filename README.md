# Shopping Agent

个人购物辅助 Agent 项目，同时用于学习 Python 与 Agent 应用开发，并积累可用于求职的工程作品。项目名称暂用 Shopping Agent。

产品质量优先，技术选型按可靠性、体验、可维护性和成熟方案的适配程度评估；学习围绕真实产品实现展开。

## 首版目标

支持淘宝与京东，商品品类不设产品限制。本地网页和浏览器扩展同时纳入首版，共用后台、任务与商品数据。

核心流程：提供商品链接或通过扩展加入商品 → 确认具体规格与使用需求 → 阅读文字、参数和详情图片 → 比较已有商品 → 搜索类似规格的其他品牌商品 → 核实候选 → 展示有来源依据的对比与建议。

详细需求与未决事项见 [需求说明](docs/REQUIREMENTS.md)。分阶段范围、学习任务、验收草案和任务状态见 [开发计划](PLAN.md)。

T020 的网页、扩展与持久化任务流程已由用户验收通过。当前开发主线调整为 T035 Agent 闭环：模型根据需求选择工具，读取商品证据、动态询问关键问题、恢复并提交带引用的报告。真实网关合成探针通过，真实商品体验待验收；T030 采集按研究需要继续完善。架构、边界及结果见 [Agent 研究记录](docs/AGENT_RESEARCH.md)、[计划](PLAN.md) 和 [当前状态](STATUS.md)。

## 当前已交付能力

- 任务详情中的“购物研究助手”接受用途、硬条件和问题；模型选择工具读取最新快照、向用户确认并继续，最终显示带来源引用和未知项的报告。运行与 LangGraph 检查点持久化，支持停止、失败后继续及历史结果。研究会向配置的网关发送需求和读取的文字证据；图片内容阅读尚未接入。
- 替代搜索已接入：提取硬条件与偏好，在淘宝/京东搜索候选，加入任务后读取详情，逐项记录满足、不满足或待核实。仅有搜索标题不能生成合格推荐；真实平台目前仍有风控与结果未识别限制。实现、16 项检查及真实模型合成联调见 [替代搜索](docs/ALTERNATIVE_SEARCH.md)。
- 价格由后端按证据确定性核算，支持同维度单位价；28 项价格边界用例已通过。私有业务数据库与检查点可成对备份、校验并恢复到新目录，见 [数据恢复](docs/DATA_RECOVERY.md)。

- Agent 可按需调用后台独立 Chromium 打开任务商品链接并滚动采集，无需打开商品页或更新扩展。访问受阻时记录登录/验证原因并请求接管；使用独立的私有会话。安装、人工登录入口和实际验证边界见 [后台采集说明](docs/BACKGROUND_COLLECTION.md)。
- 自动规格核验已接入：模型读取可选目录、按 ID 请求受控选择，程序核对选中状态及稳定后的同页价格，再返回有证据的新快照。工作台可查看可选目录和核验状态；实际天猫样本已读出 2 组 26 个选项并核验目标长度。完整边界与学习记录见 [自动规格核验](docs/AUTOMATIC_VARIANTS.md)。
- 受阻研究中可点击“打开登录窗口”，在后台独立可见浏览器中自行登录，再回工作台“保存并继续研究”。窗口最多等待 10 分钟，会话私有保存后供后台复用；不复制日常 Chrome/Edge 配置。需要后台所在电脑有图形桌面，当前 WSLg 已验证窗口能够启动。

- 网页可创建、列出、打开比较任务，并添加 1–20 条商品链接；任务详情可二次确认删除任务，任务内商品链接随任务一起删除；数据通过 SQLite/Alembic 持久化，服务重启后仍可读取。
- 任务名和商品页标题可编辑；商品链接以短文本呈现，可打开详情或从任务移除。购物车、订单和账号页链接会提示用户核对。
- 未填写新任务名称时，后台仅在创建任务时调用 OpenAI Chat Completions 兼容网关生成品类/用途级比较名称，不把某个商品的品牌或完整标题作为任务名。之后添加、编辑或移除商品不会自动改名，任务名仍可手动编辑。原始 URL 不发送给模型；仅提供 URL 的网页表单尚未读取页面标题，因此该入口需要填写任务名称。
- API 校验 URL、标题和入口字段；创建与加项请求支持幂等重放，相同幂等键配不同内容返回冲突。
- WXT 扩展在淘宝、天猫和京东页面右下方固定显示隔离的悬浮面板；用户可选择或沿用上次任务，单击后把商品链接和可见 DOM/JSON-LD 采集快照提交到本机后台。当前一条真实天猫页面已证明触发与保存有效，但仍缺品牌、规格、选中款式与详情图片字段；工作台标记部分采集；用户可另行启动研究助手，让模型判断哪些缺项影响选择。
- 网页、扩展的请求类型来自 FastAPI OpenAPI 契约；Chrome、Edge 的 MV3 构建分别成功。
- 网页支持查看快照状态、规格/价格原文、证据和缺项；图片内容尚未下载，只存经清理的图片 CDN 路径与元数据。首次天猫实采结果及解析器待复验改动见 [计划](PLAN.md) 和 [当前状态](STATUS.md)。京东真实商品详情采集、不同品类覆盖、商品比较和跨品牌搜索尚未验收。T020 的网页、扩展和任务删除人工验收由用户确认通过。

自动化检查、构建及 Windows localhost 连通结果见 [当前状态](STATUS.md) 与 [T020 探针记录](docs/T020_PROBES.md)。用户已确认 T020 本轮验收通过。真实商品采集、商品比较模型能力、Windows 本机独立恢复和 Docker 伴读尚未验收。

## 已锁定技术版本

依赖完整版本写入 `backend/uv.lock` 和 `frontend/package-lock.json`。当前基线包括 Python 3.10.12、Node.js 24.22.1、FastAPI 0.162.0、SQLAlchemy 2.0.55、Alembic 1.20.1、React 19.3.1、TypeScript 5.9.3、Vite 8.3.5、WXT 0.21.5 和 Ant Design 6.6.8。完整清单与探针结果见 [T020 探针记录](docs/T020_PROBES.md)。

## 环境与路径

- 主开发环境：WSL Ubuntu；通过 Windows 编辑文件。
- WSL 项目目录：/srv/deeptutor/data/user/workspace/my-project/shopping-agent
- Windows 可访问位置：//wsl.localhost/Ubuntu/srv/deeptutor/data/user/workspace/my-project/shopping-agent
- DeepTutor 在 Docker 中使用同一套项目代码伴读；容器内挂载位置、访问权限与文件更新可见性仍需验证。
- 后续需要支持从 GitHub 拉取到 Windows 本机，独立恢复并运行。
- 代码共享不代表运行环境、依赖、浏览器会话或模型配置自动共享。

在 WSL 终端进入项目：

~~~bash
cd /srv/deeptutor/data/user/workspace/my-project/shopping-agent
~~~

### WSL 开发启动

准备 Python 3.10+、uv 和 Node.js 24.22.1（见 `.nvmrc`），在 WSL 开两个终端。

终端一启动后台：

~~~bash
cd /srv/deeptutor/data/user/workspace/my-project/shopping-agent/backend
uv sync --all-groups
uv run playwright install --with-deps chromium
sudo apt-get install --no-install-recommends fonts-noto-cjk
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
~~~

终端二启动网页开发服务器：

~~~bash
cd /srv/deeptutor/data/user/workspace/my-project/shopping-agent/frontend
npm ci
npm run dev
~~~

浏览器打开 `http://127.0.0.1:5173/`。Vite 会把 `/api` 和 `/health` 请求代理到后台。执行生产网页构建后，FastAPI 也会在 `http://127.0.0.1:8000/` 提供网页。

修改 API 数据模型后，从后台目录重新生成共享契约，再在前端生成 TypeScript 类型：

~~~bash
cd /srv/deeptutor/data/user/workspace/my-project/shopping-agent/backend
uv run python -m scripts.export_openapi ../contracts/openapi.json
~~~

~~~bash
cd /srv/deeptutor/data/user/workspace/my-project/shopping-agent/frontend
npm run typegen
npm run typecheck
npm run build:web
npm run build:chrome
npm run build:edge
~~~

Chrome 的未打包目录为 `frontend/.output/chrome-mv3`，Edge 为 `frontend/.output/edge-mv3`。在各浏览器扩展管理页打开开发者模式并选择对应目录可进行本机加载。改动扩展权限或内容脚本后需在扩展管理页重新加载扩展，并刷新已打开的商品页。扩展默认访问 `http://127.0.0.1:8000`；使用 Vite 开发页时，在扩展弹窗的“本地服务地址”中改为 `http://127.0.0.1:5173`。用户确认本轮扩展和任务管理人工验收通过。

扩展最新构建 `0.2.2` 增加共享规格目录读取；原有补采集、连接状态和版本提示保留。后台自动规格核验无需更新扩展或打开商品页；若使用扩展接管并希望获取新版目录，在 `chrome://extensions` 或 `edge://extensions` 重新加载后刷新商品页。后台已完成当前天猫样本的登录后规格选择核验，其他页面覆盖仍待验收，详见 [自动规格核验](docs/AUTOMATIC_VARIANTS.md)。

SQLite 文件位于服务所在系统的 `platformdirs` 用户数据目录。需要指定其他路径时，在启动后台前设置 `SHOPPING_AGENT_DATA_DIR`；不要让 Windows 与 WSL 同时跨文件访问同一个 SQLite 文件。

| 操作 | 命令/入口 | 验证边界 |
| --- | --- | --- |
| 安装依赖 | 后台 `uv sync --all-groups`；前端 `npm ci` | WSL 已执行；Windows 独立恢复未验收 |
| 启动后台与网页 | `uv run uvicorn app.main:app --host 127.0.0.1 --port 8000`；`npm run dev` | 后台迁移、静态网页与 Windows localhost 连通已检查 |
| 构建浏览器扩展 | `npm run build:chrome`；`npm run build:edge` | Chrome、Edge 页面内添加流程已由用户确认本轮验收通过 |
| 配置标题生成 | 设置 `SHOPPING_AGENT_LLM_BASE_URL`、`SHOPPING_AGENT_LLM_MODEL`，密钥通过私有环境变量 `SHOPPING_AGENT_LLM_API_KEY` 提供；本地可放入根目录 `.env` | 当前本机私有配置已设置；模型 ID 为 `gemini-3.8-flash-high`，已有合成标题请求成功记录 |
| 执行应用检查 | `uv run pytest`、Ruff、`npm run typecheck` 及构建脚本 | 后端全套 101 项、扩展通道 6 项通过；Ruff、类型检查、网页及双浏览器构建通过 |

依赖安装说明优先使用 WSL 终端。未来增加 Shell 脚本时，首行必须为 #!/usr/bin/env bash，并在此补充使用实际脚本文件名的 chmod +x 命令。Windows 本机恢复说明需在真实验证后补齐，不能以 WSL 结果替代。

## 文本和 Git 规则

2026-10-29 Windows 原生隔离副本已完成 53 项后台核心检查，以及服务启动、任务保存和备份恢复；DeepTutor 容器可见同版本代码。实际范围、PowerShell 恢复步骤与未验收边界见 [跨平台记录](docs/CROSS_PLATFORM.md)。Windows 浏览器与前端本机构建仍需单独验收。

- 新建与编辑的文件统一 UTF-8、LF；编辑器规则见 [.editorconfig](.editorconfig)。
- Git 规则见 [.gitattributes](.gitattributes)：默认文本 LF，Shell 文件 LF，批处理文件在 Git 检出时使用 CRLF。这是 Git 检出策略；本次新建文件均为 LF。
- 路径使用 pathlib、标准路径 API 或正斜杠，不硬编码 Windows 反斜杠路径；导入、链接和文件名大小写保持一致。
- [.gitignore](.gitignore) 过滤 DeepTutor 缓存、系统元数据、依赖、构建产物和本地认证信息；依赖锁文件应提交。
- .deeptutor/ 由 DeepTutor 管理，项目开发工具与维护脚本不得修改、删除或清理其中内容。
- 已初始化项目 Git 仓库，主分支为 main，origin 为 https://github.com/wxyhaha/shopping-agent.git。规划基线按用户授权提交、推送；实际同步状态以 git status -sb 和远端分支核对结果为准。

在其他设备的目标父目录中获取项目：

~~~bash
git clone https://github.com/wxyhaha/shopping-agent.git
cd shopping-agent
~~~

GitHub 远端仍需后续推送本地功能提交；从 GitHub 克隆暂时不会获得当前工作树所含的 T030/T035/T040 采集与 Agent 能力。Windows 本机独立恢复仍需实测。

## 开发与伴读

Codex 负责开发、运行与验证；DeepTutor 围绕相同版本的代码、关键调用记录和结果教学。每个小功能采用“说明数据流 → 实现 → 解释关键代码 → 学习者修改 → 验证失败场景”的循环。

学习交接需要明确任务编号、相关代码位置、版本或差异、真实运行结果和未理解问题。未验证的自动同步能力不视为已有能力。

## 协作入口

1. [AGENTS.md](AGENTS.md)：共享规则、环境约束和权限边界。
2. [STATUS.md](STATUS.md)：当前实际能力与主要限制。
3. [HANDOFF.md](HANDOFF.md)：最近可恢复检查点。
4. [PLAN.md](PLAN.md)：任务状态唯一台账。
5. [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md)：确认需求、首版边界和待讨论事项。

协作技能来源：用户指定的本地 ai-project-collaboration。项目运行不依赖该技能的本机安装路径。
