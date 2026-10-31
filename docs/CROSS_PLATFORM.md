# WSL、Windows 与 DeepTutor 验证记录

## 2026-10-29 实测范围

本轮使用当前未提交工作树的源码快照，在 Windows 临时目录建立独立副本；不使用 WSL 的虚拟环境、SQLite 或登录态，不复制 `.env`。后台按 `uv.lock` 安装，Python 3.12.7、uv 0.13.6。

- Windows 原生 53 项检查通过：价格 28 项、研究 8 项、任务 API 8 项、备份恢复 9 项，包含真实 LangGraph 暂停后重启续跑、跨目录备份恢复。
- Windows 原生 Uvicorn 启动通过，readiness、任务创建/读取和备份/恢复 CLI 均成功；网页生产静态文件可通过该 Windows 服务访问。静态文件由 WSL 构建后复制，本轮未验证 Windows 原生 Node 构建。
- 修复 Windows 实测发现的 SQLite 文件占用：连接的上下文管理器只提交/回滚事务，现在备份与测试显式关闭连接，文件可释放。
- WSL 网页与 Chrome/Edge 构建、扩展 6 项检查通过。浏览器合成页面检查在 WSL Chromium 执行；本轮未重新验收 Windows Chrome/Edge 实际加载扩展、商品采集或原生 Windows Chromium 搜索。
- 最终 WSL 后端全套101项通过；保留一条既有 Starlette HTTP 状态常量弃用提示。Windows可通过 `127.0.0.1:8000` 访问当前WSL服务，readiness为ready。

临时验收副本路径保存在忽略文件 `.runtime/windows-acceptance-path.txt`。它是隔离验收环境，不替换当前 WSL 项目。GitHub 尚未同步本轮改动，直接从远端克隆不能取得这些未提交功能。

## Windows 后台恢复入口

以下命令在包含当前源码和锁文件的项目根目录执行，需已安装 Python 3.12。只在项目忽略目录安装工具，不改全局 Python 依赖：

```powershell
py -3.12 -m venv .runtime/windows-tools
& ./.runtime/windows-tools/Scripts/python.exe -m pip install uv==0.13.6
& ./.runtime/windows-tools/Scripts/uv.exe sync --project backend --locked --all-groups --python 3.12
Push-Location backend
& ./.venv/Scripts/python.exe -m pytest -q tests/test_price_comparison.py tests/test_research_agent.py tests/test_tasks_api.py tests/test_data_backup.py
& ./.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
Pop-Location
```

后台默认使用 Windows 自己的应用数据目录。另开 PowerShell 验证：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health/ready
```

网页开发仍参考 README 的 Node/前端步骤。使用已生成的 `frontend/dist/` 时，后台可直接提供静态网页；需要后台自主浏览时，另在对应 Windows 环境安装 Playwright Chromium，不能沿用 WSL 浏览器路径。本轮没有声称该 Windows 浏览器步骤通过。

私有模型配置应在本机单独配置；备份与恢复见 [数据恢复](DATA_RECOVERY.md)。测试通过不需要复制用户密钥、会话或购物数据。

## DeepTutor 共享代码

只读 `docker inspect` 确认现有 `deeptutor` 容器挂载：宿主 `/srv/deeptutor/data/user/workspace` → 容器 `/workspace`，另有数据目录 → `/app/data`。因此本项目在容器中的实际位置是 `/workspace/my-project/shopping-agent`。

对 `backend/app/research_agent.py` 与新增 `docs/ALTERNATIVE_SEARCH.md` 分别在宿主和容器执行 SHA-256，结果逐项相同，确认容器可见本轮代码及文档。未修改 Docker 配置或 `.deeptutor/`，也未据此声称 DeepTutor 已理解或完成教学。

伴读交接可使用 [替代搜索](ALTERNATIVE_SEARCH.md) 和 [数据恢复](DATA_RECOVERY.md)，结合源码与真实模型合成运行记录。容器能读取文件不等于容器内已经安装项目运行依赖。
