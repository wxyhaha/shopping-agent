# 当前项目状态

T010 已完成。T020 按“产品质量优先、优先成熟方案”进入实施；技术基线与风险探针见 [方案](docs/ARCHITECTURE.md)，任务状态只在 [PLAN.md](PLAN.md) 维护。

- 已有：确认需求、阶段开发与学习计划、共享协作规范、交接入口、技术方案与数据契约草案、文本和 Git 过滤规则。
- 当前可运行的购物功能：无。T020 刚启动，后台、网页、扩展、采集、模型比较和替代搜索尚未实现。
- 目标浏览器：用户已确认 Chrome、Edge 均需支持；尚未实机验收。
- 用户已确认产品质量优先于教学便利和尽快跑通；已写入共享规范及需求。
- 技术建议：React + TypeScript 网页、WXT 扩展、FastAPI + Pydantic 后台、SQLite + SQLAlchemy + Alembic；Agent 优先评估 LangGraph。版本和兼容性尚未验证。
- T020 起持久化并前置核心风险探针与局部检查；T040/T050 同步建设 Agent 恢复和人工介入；T060 汇总强化与回归。
- Gemini 实际协议/模型、依赖版本和详细评估数字：待核实。
- 环境要求：WSL 开发、Windows 编辑，Docker 中 DeepTutor 使用同一套代码，未来支持 Windows 本机拉取运行；浏览器连接和容器挂载效果尚未实测。
- 已有 Git 规划基线提交，主分支为 main，origin 指向 https://github.com/wxyhaha/shopping-agent.git；本轮方案文档尚未提交，实时远端状态未重新查询。
- T020 实施进度、检查结果和下一动作见 [HANDOFF.md](HANDOFF.md)。
- 人工功能体验、学习掌握、Windows 独立运行及容器伴读同步：尚未验收。

恢复工作先读 [AGENTS.md](AGENTS.md) 与 [HANDOFF.md](HANDOFF.md)，不能根据计划条目推断业务功能已完成。
