# T020 技术探针记录

日期：2026-10-28（Asia/Shanghai）。这是 T020 实施检查点，不代表完整采集、商品比较或 Agent 功能已交付。网页内容均按不可信资料处理；平台页面探针只提取页面状态和有限元数据，没有执行页面脚本。

## 依赖与构建

WSL 环境使用 Python 3.10.12、Node.js 24.22.1、uv 0.13.6。依赖由 `backend/uv.lock` 与 `frontend/package-lock.json` 锁定。当前锁定版本包括：FastAPI 0.162.0、Pydantic 2.14.1、SQLAlchemy 2.0.55、Alembic 1.20.1、React 19.3.1、TypeScript 5.9.3、Vite 8.3.5、WXT 0.21.5、Ant Design 6.6.8、TanStack Query 5.105.0、React Router 7.18.5、LangGraph 1.2.18 与 langgraph-checkpoint-sqlite 3.1.2。

- `npm install` / `npm audit`：安装成功，npm 报告 0 个已知漏洞。
- 网页生产构建成功；设计系统保持在单一 479.09 kB chunk，构建无 chunk size 警告；React 与路由/请求仍按配置独立分块。
- WXT 分别成功生成 `chrome-mv3` 和 `edge-mv3` Manifest V3 构建。
- 实际生成 OpenAPI JSON 和 TypeScript 类型；前端双 tsconfig 类型检查通过。
- 用户已在 Chrome 和 Edge 安装扩展，并确认本轮 T020 验收通过；构建检查与用户人工验收分别记录，未单独留存逐项浏览器录像或运行日志。

## 后台、持久化与双入口契约

- SQLite + SQLAlchemy + Alembic 首次迁移成功。数据库默认位于服务所在系统的数据目录，可通过 `SHOPPING_AGENT_DATA_DIR` 单独指定；自动化检查使用临时目录，当前 WSL 服务使用默认持久化目录。
- FastAPI 核心检查：8 项通过，覆盖创建与读取任务、非法 URL、创建幂等重放/内容冲突、加项幂等及任务作用域冲突、缺失任务、后台重启后数据保留。
- Ruff 检查与格式检查通过。
- 服务从 WSL `127.0.0.1:8000` 启动后，WSL 健康检查与网页静态入口均返回成功；Windows PowerShell 请求 `http://127.0.0.1:8000/health/ready` 也返回 `ready`。
- 本轮新增站点悬浮入口：WXT content script 匹配淘宝、天猫、京东；Shadow DOM 隔离页面样式。面板仅开放识别出的商品详情页，可沿用最近任务、切换任务或建新任务；页面标题本地预览，点击添加后由 service worker 校验发送页与当前发送者一致，再写入 loopback API。扩展增加 local service host permission。用户截图显示首版 overlay 相对页面底部定位，顶部视口看不到；现将面板改为 `position: fixed` 并重新生成 Chrome/Edge 构建。用户随后确认看到了面板，并在本轮明确确认验收通过。
- 旧版 Chrome/Edge 扩展不含悬浮面板；本轮构建后用户重新加载并看到面板，随后确认验收通过。此前弹出页确认流程的描述保留为历史版本行为，当前工作流不再跳转 `/capture` 确认。
- 新增 `DELETE /api/v1/tasks/{task_id}`，任务删除通过 SQLAlchemy relationship cascade 一并删除所属商品条目；任务详情页需二次确认，删除成功后清除详情缓存、刷新任务列表并返回首页。新路由已出现在 OpenAPI，WSL 服务 readiness 为 `ready`；前端类型检查及网页、Chrome、Edge 构建成功。未执行本轮测试套件；用户已明确确认本轮验收通过。

## LangGraph 检查点

运行 `backend/scripts/probe_langgraph_checkpoint.py` 成功。图到人工确认节点暂停，关闭 SQLite saver 后重新打开同一临时数据库，继续图并拿到 `approved` 决策及恢复后的步骤状态。

这证明当前锁定版本的 SQLite 检查点具备该最小暂停/恢复行为；它没有接入产品任务运行器，也不验证调度、崩溃时工具重放、恰好一次写入或运行记录，因此 T040/T060 仍需做端到端设计与验证。LangGraph 官方将 checkpointer 用于线程内图状态；SQLite saver 定位于本地流程，生产集成需另行评估：[Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)、[Checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers)。

## Gemini 渠道

用户确认现有网关使用 OpenAI Chat Completions 兼容协议，任务标题模型 ID 为 `gemini-3.8-flash-high`。私有配置现已位于忽略的本机 `.env`；曾用合成页面标题完成一次标题请求，返回 `家用千兆网线10米比较`，未创建任务或使用真实商品数据。不要记录或输出密钥值。

本轮修订提示词，要求名称概括品类/用途而非单个商品，排除品牌、店铺、平台、型号、SKU。任务名只在创建时生成一次，之后商品变化不触发 AI 改名，用户仍可手动编辑。用含品牌的合成单商品标题调用修订后的提示词，网关返回“家用网线比较”；没有创建或修改任务。只发送页面标题，不发送原始 URL；直接粘贴链接的网页入口尚未读取页面标题，因此留空任务名时会提示补充标题。模型用量字段、商品分析所需能力仍未验证；商品分析模型流程不属于本轮实现。

## 淘宝、天猫与京东页面

本轮从 WSL 以普通 HTTP 请求检查三个公开商品地址，没有登录态，也没有执行浏览器脚本：

| 平台 | 样例 | 观察到的响应 | 判断与限制 |
| --- | --- | --- | --- |
| 淘宝 | [商品页](https://item.taobao.com/item.htm?id=564898007928) | HTTP 200，约 5 KB；无 HTML 标题或可见正文 | 不能据此读取商品。可能是空壳、动态加载或访问限制，原因未确认 |
| 天猫 | [商品页](https://detail.tmall.com/item.htm?id=607391279050) | HTTP 200，约 5 KB；无 HTML 标题或可见正文 | 不能据此读取商品。需要浏览器会话/人工验证等后续探针 |
| 京东 | [商品页](https://item.jd.com/product/IQNLUypvkK91XglT2EZttQ.html) | HTTP 200，约 119 KB；HTML 标题包含型号 `KJ320F-JSQ3U1`；未见 `og:*` 商品元数据或 JSON-LD | 静态标题可见不等于规格、所选 SKU、优惠条件、到手价和详情图覆盖可用；尚未验证页面动态数据与登录态 |

这是小规模匿名 WSL 抓取，不是平台适配结论或正式采集器。暂未验证 Windows Chrome/Edge 的真实登录页；不把状态码 200 当作采集成功。

## T020 验收结论与后续能力边界

- 用户确认本轮 T020 验收通过；没有单独留存逐项浏览器录像或独立运行日志。此项为用户人工验收结论。
- 窄屏、键盘和屏幕阅读器专项可访问性评估未做。
- 淘宝/天猫登录态页面中 SKU、参数、价格条件与详情图覆盖；京东动态数据与地区/优惠条件，均属于尚未交付的 T030 商品采集能力。
- Gemini 私有渠道能力；LangGraph 检查点与实际任务执行器的集成。
- Windows 本机独立恢复、DeepTutor 容器挂载和项目冷启动。

## 本地工作台运行时异常

用户截图显示 `http://127.0.0.1:8000/` 空白，Console 报 `Cannot destructure property 'ESC' of 'v' as it is undefined`，位置在设计系统 bundle。构建产物中设计系统被 `maxSize` 拆成多个相互导入的 chunk，导致下拉菜单键盘常量初始化异常。移除该拆分后重新执行 `npm run build:web` 成功，最新输出为单一 479.09 kB 设计系统 chunk，无 chunk size 警告；后台 readiness 返回 `ready`，根页面和 JS 入口均 HTTP 200。用户之后确认本轮 T020 人工验收通过；未单独留存浏览器控制台复核记录。
