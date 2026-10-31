# WSL、Windows 与 DeepTutor 验证记录

## 2026-10-31 主开发位置迁移到 Windows

用户确认改为 Windows 端开发，DeepTutor 伴读推迟到开发完成后。迁移目标：Windows 为主开发目录，WSL 只保留同步镜像供容器读取。

### 迁移前状态

- 源码基线 `main` / `e2295bd`；迁移前本地领先远端 3 个提交，另有 33 个未提交变更。
- 私有数据在 WSL 的 `/root/.local/share/shopping-agent/`：业务库 4.4 MB、LangGraph 检查点 11 MB、淘宝登录态 604 KB、历史备份 5.4 MB。
- 仓库文件属主为 `root`，WSL 与 Windows 两侧 git 均报 `dubious ownership`；Windows 经 UNC 路径写入报 `ENOTSUP`。

### 执行步骤与结果

1. 补齐文档中过期的检查数表述后，将 33 个变更提交为 `e2295bd` 并推送；远端 `0e222c5..e2295bd` 更新成功，本地与远端随即一致。
2. 用 `scripts.manage_data backup` 生成双库备份并通过 `verify`；登录态单独打包为 `browser-sessions.tar.gz`。备份存放在忽略目录 `.runtime/backups/migrate-to-windows/`。
3. Windows 侧 `git clone` 到 `D:\studyPlace\shopping-agent`，HEAD 为 `e2295bd`。检出文件保持 LF，抽查 `README.md` 与 `backend/app/main.py` 的 CRLF 计数为 0。
4. 工具链：`uv 0.13.6` 装入用户 Python 3.12；前端 `npm ci` 237 包。
   - nvm4w 配置 `node_mirror: https://npmmirror.com/mirrors/node`，该镜像缺 `24.22.1`，安装报“not yet released”。改用官方源下载，SHA-256 与官方 `SHASUMS256.txt` 一致后解压到 `D:\software\nvm\v24.22.1`，`nvm use 24.22.1` 生效。未修改 nvm 全局镜像设置。
   - `playwright install chromium` 下载 headless shell 118.1 MiB 成功。
5. 用 `scripts.manage_data restore` 恢复到 `.runtime\restored-data`，两库 `PRAGMA quick_check` 为 `ok`；行数与 WSL 一致：任务 5、商品 8、快照 20、运行 22，迁移版本 `20261028_0004`，快照平台仍全部为 `tmall`。
6. 以 `SHOPPING_AGENT_DATA_DIR` 指向该恢复目录启动后台：readiness 为 `ready`，`/` 返回 200，5 个任务可读，`collection/status` 显示浏览器可用、解析器已构建、无需打开商品页。

### 平台差异导致的两处测试修正（`b86f5d1`）

Windows 首次运行全套出现 2 项失败，经定位均为平台语义差异，不是产品缺陷：

- `test_hung_worker_deadline_and_cancel_reap_descendants`：Windows worker 导入 Playwright 并启动 Chromium 约需 2.9 秒，原 3 秒期限与进程启动竞争，表现为随机失败（重跑三次中前两次通过、第三次失败）。期限改为 7 秒，断言余量同比放宽；挂起 90 秒仍远大于期限。取消分支仍依赖 marker 出现，未削弱回收检查。
- `test_user_login_single_window_save_private_state_and_reuse`：Windows 不暴露 POSIX 权限位，`chmod` 只切换只读属性，`st_mode` 恒为 `0o666`。私有机会话文件的 `0o600` 校验改为仅在 POSIX 主机执行。

修正后 Windows 全套 **100 项通过、1 项跳过**（跳过项需要可见桌面），连续三次分段运行稳定；Linux 复跑对应文件 7 项通过，两个平台未出现回归。

### 仍待验证

- Windows 原生前端构建、静态网页由 Windows 服务提供、任务保存在 Windows 侧均已在本次执行；Windows 上的 Chrome/Edge 扩展实际加载与真实平台采集仍未验收。
- `.env` 未随迁移复制，Windows 侧模型配置需自行准备；未复制任何凭据进仓库。
- 备份包 `.runtime/backups/migrate-to-windows/` 与恢复目录含私有数据，均处于忽略目录，不进入 Git。

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
