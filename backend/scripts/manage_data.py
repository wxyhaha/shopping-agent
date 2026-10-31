"""Back up idle task/checkpoint databases or restore into an unused directory."""

import argparse
from pathlib import Path

from app.config import get_settings
from app.data_backup import BackupError, backup_data, restore_data, verify_backup


def main():
    parser = argparse.ArgumentParser(description="购物研究私有数据库备份与恢复")
    parser.add_argument("operation", choices=["backup", "verify", "restore"])
    parser.add_argument("--source", type=Path, help="数据目录或备份目录；backup 默认使用当前配置")
    parser.add_argument("--destination", type=Path, help="新建的备份或恢复目录")
    args = parser.parse_args()
    if args.operation != "backup" and args.source is None:
        parser.error("verify/restore 需要 --source")
    if args.operation != "verify" and args.destination is None:
        parser.error("backup/restore 需要 --destination")
    try:
        if args.operation == "backup":
            result = backup_data(args.source or get_settings().data_dir, args.destination)
        elif args.operation == "verify":
            result = verify_backup(args.source)
        else:
            result = restore_data(args.source, args.destination)
    except (BackupError, OSError) as exc:
        parser.exit(
            1,
            (str(exc) if isinstance(exc, BackupError) else "文件操作失败，请检查目录权限。") + "\n",
        )
    print(f"{args.operation} 完成：{len(result['files'])} 个数据库；不包含浏览器登录态。")


if __name__ == "__main__":
    main()
