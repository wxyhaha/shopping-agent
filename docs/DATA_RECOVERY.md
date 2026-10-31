# 私有研究数据备份与恢复

业务数据库和 LangGraph 检查点是两份 SQLite 文件。仅复制业务数据库不足以恢复暂停中的工具上下文；直接复制正在写入的 SQLite 主文件还可能遗漏 WAL 中的数据。

新增管理命令使用 SQLite backup API，短暂持有业务库写锁，拒绝存在 `queued/running` 研究时备份。锁内复制业务库及存在的检查点库，检查完整性并生成带 SHA-256 的清单；备份目录必须不存在。备份失败且没有完整清单的目录不应使用。

备份含用户需求、商品信息、模型输入输出等私有资料，不应提交 Git。工具不备份 `.env`、密钥或浏览器登录会话，也不会访问 `.deeptutor/`。备份和恢复不自动切换当前服务的数据位置。

## 使用方法

在 `backend` 目录运行，路径示例均为项目忽略的 `.runtime/` 子目录。每次选用新的目录名。

```bash
uv run python -m scripts.manage_data backup --destination ../.runtime/backups/manual-001
uv run python -m scripts.manage_data verify --source ../.runtime/backups/manual-001
uv run python -m scripts.manage_data restore --source ../.runtime/backups/manual-001 --destination ../.runtime/restored/manual-001
```

`backup` 默认使用当前配置的数据目录，也可通过 `--source` 指定其他数据目录。`restore` 校验清单、哈希、文件名与数据库完整性后，只恢复到新目录，拒绝覆盖已有数据或接受目录穿越文件名。恢复失败的目录不要启动使用。

恢复后先停止原服务，再将 `SHOPPING_AGENT_DATA_DIR` 指向恢复目录并启动；不要让两套服务同时处理同一研究。以下路径由项目目录解析，不依赖固定 `/srv` 位置：

```bash
SHOPPING_AGENT_DATA_DIR="$(cd ../.runtime/restored/manual-001 && pwd)" uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Windows PowerShell 的设置方式（需已建立 Windows 本机运行环境）：

```powershell
$env:SHOPPING_AGENT_DATA_DIR = (Resolve-Path ../.runtime/restored/manual-001).Path
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

## 验证与限制

2026-10-29 新增 9 项检查通过：业务/检查点双库备份恢复、运行/排队拒绝、暂停研究缺失检查点拒绝、已有目录拒绝、篡改/缺清单/重复条目/路径穿越拒绝，以及真实 LangGraph 暂停任务从备份恢复到新目录后继续并保存引用报告。Windows 原生同组9项也通过；备份连接显式关闭，避免文件占用。

```bash
uv run pytest -q tests/test_data_backup.py
```

已对当前私有业务数据完成一次双库备份，保存于项目忽略目录 `.runtime/backups/2026-10-29-autonomous/`。这不是多进程调度、灾难恢复耗时或跨版本数据库降级的保证。新版本仍使用 Alembic 正向迁移；重要升级前先保留独立备份。

学习练习：解释 WAL、业务记录与检查点各保存什么；备份暂停任务，在独立目录恢复，再用测试中的回答继续。失败案例：修改备份数据库一个字节后再恢复，校验应拒绝且不创建目标目录。
