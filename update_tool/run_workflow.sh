#!/usr/bin/env bash
# 一键启动脚本 (bash/Git Bash): 全流程 download_uniprot.py -> run_all.py --force -> update_database.py
# 用法:
#   ./run_workflow.sh              # 按默认配置跑完整流程
#   ./run_workflow.sh --dry-run    # 只打印将执行的命令, 不实际运行
# 环境变量 (去掉注释即生效, 优先级高于 run_workflow.py 里的默认值):
#   export UNI_DOWNLOAD_DIR="..."   # 下载目录
#   export UNI_OUTPUT_DIR="..."     # 输出目录
#   export UNI_BACKUP_DIR="..."     # 备份目录 (留空=自动 _backup_<时间戳>)
#   export UNI_TARGET_ROOT="..."    # 部署目标根 (旧表所在根目录)
#   export UNI_QUERY="(terpene) AND reviewed:true"
#   export UNI_DRY_RUN=0            # 1 = 只打印不执行
#   export UNI_SKIP_DOWNLOAD=0      # 1 = 跳过下载
#   export UNI_SKIP_UPDATE=0        # 1 = 跳过部署

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ---------- 环境变量设置 (默认值; 反注释某行即可覆盖) ----------
# export UNI_DOWNLOAD_DIR="${SCRIPT_DIR}"
# export UNI_OUTPUT_DIR="${SCRIPT_DIR}"
# export UNI_BACKUP_DIR=""
# export UNI_TARGET_ROOT="$(dirname "${SCRIPT_DIR}")"
# export UNI_QUERY="(terpene) AND reviewed:true"
# export UNI_DRY_RUN=0
# export UNI_SKIP_DOWNLOAD=0
# export UNI_SKIP_UPDATE=0
# -----------------------------------------------------------------

cd "${SCRIPT_DIR}"
PYTHONIOENCODING=utf-8 python run_workflow.py "$@"
