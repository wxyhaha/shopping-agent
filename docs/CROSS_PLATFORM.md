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

### Windows 侧模型网关与真实研究

迁移后补齐本机私有 `.env`（仅复制变量，不打印值，`git check-ignore` 确认为 `.gitignore:12`）。数据从 `.runtime\restored-data\` 移到默认目录 `%LOCALAPPDATA%\shopping-agent`，启动不再需要 `SHOPPING_AGENT_DATA_DIR`。随后验证：

- `GET /models` 返回 200，3.42 秒。
- 真实工具调用协议：模型返回 `read_product_evidence`，6.69 秒，用量 2126 tokens。
- 对任务 `6dfbb000` 发起真实研究：**completed**，72 秒，4 次模型调用、4 次工具调用，单次最慢 32.05 秒（上限 60 秒）。
- 最终报告 `outcome=recommendation`，`price_comparisons` 首次真实持久化：山泽 ¥52.37 与绿联 ¥55.60 同为“优惠前”口径，差额 ¥3.23，以较高标价为分母 5.81%；15 m 同维度单位价 ¥3.491333/m 与 ¥3.706667/m，每米差 ¥0.215333。每项金额、规格与价格均带证据引用。
- 报告中把“六类千兆 / 扁平 / 15m”限定为已对齐的共同维度，未称为同款；把“升级纯铜芯”“传输快且稳”标为商家文案；到手价、AWG、屏蔽层与品牌字段列为未知。

本次运行的两次 `submit_report` 事件是设计内的“先核算后定稿”，已把事件标签区分为“核算价格并核对报告（待定稿）”与“（已定稿）”，避免时间线误读为重复提交。

### 仍待验证

- Windows 原生前端构建、静态网页由 Windows 服务提供、任务保存在 Windows 侧均已在本次执行；Windows 上的 Chrome/Edge 扩展实际加载与真实平台采集仍未验收。
- 真实研究仍只覆盖天猫单一平台与网线品类；淘宝、京东真实采集和替代搜索的真实平台结果仍待验证。
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

网页开发参考 README 的 Windows 步骤。2026-10-31 已在 Windows 实机完成：`npm run build:web`、`build:chrome`、`build:edge` 三项构建通过；由 Windows 服务提供 `frontend/dist` 静态页，`/`、`/favicon.svg`、5 个 JS/CSS 资源、SPA 深链接 `/tasks/<id>` 与同源 `/api/v1/tasks` 均返回 200。用无头 Chromium 实际渲染首页与任务详情页，`#root` 已挂载、最近任务列出 5 项、任务页可见 3 074 字符，控制台错误与失败请求均为 0。Playwright Chromium 已在该 Windows 环境安装，不再沿用 WSL 浏览器路径。

私有模型配置应在本机单独配置；备份与恢复见 [数据恢复](DATA_RECOVERY.md)。测试通过不需要复制用户密钥、会话或购物数据。

## DeepTutor 共享代码

只读 `docker inspect` 确认现有 `deeptutor` 容器挂载：宿主 `/srv/deeptutor/data/user/workspace` → 容器 `/workspace`，另有数据目录 → `/app/data`。因此本项目在容器中的实际位置是 `/workspace/my-project/shopping-agent`。

对 `backend/app/research_agent.py` 与新增 `docs/ALTERNATIVE_SEARCH.md` 分别在宿主和容器执行 SHA-256，结果逐项相同，确认容器可见本轮代码及文档。未修改 Docker 配置或 `.deeptutor/`，也未据此声称 DeepTutor 已理解或完成教学。

伴读交接可使用 [替代搜索](ALTERNATIVE_SEARCH.md) 和 [数据恢复](DATA_RECOVERY.md)，结合源码与真实模型合成运行记录。容器能读取文件不等于容器内已经安装项目运行依赖。
