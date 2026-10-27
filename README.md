# Shopping Agent

个人购物辅助 Agent 项目，同时用于学习 Python 与 Agent 应用开发，并积累可用于求职的工程作品。项目名称暂用 Shopping Agent。

## 首版目标

支持淘宝与京东，商品品类不设产品限制。本地网页和浏览器扩展同时纳入首版，共用后台、任务与商品数据。

核心流程：提供商品链接或通过扩展加入商品 → 确认具体规格与使用需求 → 阅读文字、参数和详情图片 → 比较已有商品 → 搜索类似规格的其他品牌商品 → 核实候选 → 展示有来源依据的对比与建议。

详细需求与未决事项见 [需求说明](docs/REQUIREMENTS.md)。分阶段范围、学习任务、验收草案和任务状态见 [开发计划](PLAN.md)。

## 当前实际能力

当前仅有协作、需求和计划文档，以及跨平台文本与 Git 过滤配置。

尚无业务源码、网页、浏览器扩展、模型调用、依赖清单、锁文件或启动脚本；没有完成真实商品采集或 Windows 运行验收。以 [当前状态](STATUS.md) 为准。

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

技术栈尚待讨论。以下入口不预先编造命令：

| 操作 | 当前入口 | 验证边界 |
| --- | --- | --- |
| 安装与恢复依赖 | 待 T010 确定技术栈并在实现中生成依赖清单、锁文件 | 未执行 |
| 启动后台与网页 | 待实现 | 未执行 |
| 加载浏览器扩展 | 待确定目标浏览器并实现 | 未执行 |
| 配置 Gemini | 待核实调用地址、协议、模型标识和能力 | 未执行 |
| 执行应用检查与打包 | 待实现 | 未执行 |

依赖安装说明优先使用 WSL 终端。未来增加 Shell 脚本时，首行必须为 #!/usr/bin/env bash，并在此补充使用实际脚本文件名的 chmod +x 命令。Windows 本机恢复说明需在真实验证后补齐，不能以 WSL 结果替代。

## 文本和 Git 规则

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

以上命令只获取项目文件；当前没有可安装或启动的购物应用。Windows 本机恢复与业务运行仍需在实现后实测。

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
