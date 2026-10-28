# 最近一次交接

## 当前检查点

- 日期：2026-10-28，Asia/Shanghai。
- 基线：main，HEAD 为 0e222c5；本地跟踪引用未显示领先或落后，未查询实时远端。进入本轮时已有上轮 T010 的五个已跟踪文档修改和一个新架构文档，均属于同一任务并保留演进。
- 用户最新决定：产品质量优先于教学便利及尽快跑通，其他步骤同样优先成熟且适配场景的方案；Chrome、Edge 均需支持。
- 用户要求提交本次成果文档并直接进入下一步；T010 方案作为 T020 实施基线，T010 完成、T020 进行中。依赖版本、模型渠道、采集和恢复适配仍需实测；任务状态只在 PLAN 维护。

## 方案修订

- 建议 React + TypeScript + Vite、React Router、TanStack Query、Ant Design 构建网页；WXT + React + TypeScript 构建 MV3 扩展。
- 保留 Python/FastAPI 后台，使用 Pydantic 校验与共享 OpenAPI 契约；建议 T020 起引入 SQLite + SQLAlchemy + Alembic，刷新和重启不丢任务。
- 采集建议扩展主动读取当前页、平台适配器与 Playwright；Windows 与 WSL 浏览器会话不可假定共享。API 覆盖和最终价格条件需实际验证。
- Agent 优先评估 LangGraph 状态、检查点和人工介入能力，保留项目自己的工具校验、商品判断、价格计算和证据规则。检查点不替代调度、幂等和真实性核验。
- 模型渠道、淘宝/京东采集与检查点恢复的技术探针前移至获授权的 T020；完整功能仍分阶段完成。局部检查与样本评估跟随功能建立，T060 汇总强化和回归。
- 取代原生网页、T020 内存保存以及直到 T060 才持久化的旧建议。产品质量原则写入 AGENTS.md 与需求 R13；学习在真实实现上进行，单独记录掌握情况。

## 改动范围与约束

七个 Markdown：AGENTS.md、README.md、PLAN.md、STATUS.md、HANDOFF.md、docs/REQUIREMENTS.md、docs/ARCHITECTURE.md。待完成文档提交；随后按用户授权进入 T020 编码和依赖安装。尚未调用模型、采集商品或部署。

保留首版全部需求：淘宝/京东、不限品类、双入口、Chrome/Edge、链接比较及跨品牌替代搜索。不询问开发费用预算，不记录秘密值；不改 .deeptutor/、全局 Git、网络、代理或 Docker 配置。

当前没有运行中的购物功能。文件共享、框架文档、类型检查和方案状态均不代表运行可达或商品信息正确。Windows 本机恢复与 Docker 挂载仍未验收。

## 检查与证据

- 查阅 React、Vue、Ant Design、TanStack Query、WXT、SQLite、SQLAlchemy、Alembic、LangGraph、Playwright、FastAPI 和 Gemini 官方资料，来源链接保存在架构文档。
- 本轮文档检查通过：七个 Markdown 文件为 UTF-8、无 BOM、LF、有末尾换行；25 个内部链接存在且大小写正确；差异空白检查通过。定向核对旧方案措辞，内存/原生方案只以已取代的历史说明出现。检查显式排除 .deeptutor/。
- 未执行业务测试、模型渠道/采集探针、浏览器体验、恢复、Windows 或 Docker 验证。

## 下一动作

提交本次文档成果后继续 T020：锁定维护中的兼容依赖，建立 React 网页、WXT 双浏览器扩展、共享 FastAPI 契约、数据库和迁移，加入输入/重复请求/重启检查，并提前验证关键外部能力。私有渠道配置缺失时先推进不依赖它的部分，能力支持仍标记待验证。

不重复询问已经确认的产品优先级和双浏览器范围。本轮授权提交方案文档并进入 T020 实现、依赖安装和测试；部署与后续 Git 交付仍按实际授权执行。
