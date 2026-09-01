"""
一键启动脚本: 覆盖全流程 download_uniprot.py -> run_all.py --force -> update_database.py。

配置方式 (优先级: 环境变量 > 下方 CONFIG 默认值):
  可用环境变量 (Windows cmd: set VAR=值; Git Bash: export VAR=值):
    UNI_DOWNLOAD_DIR   下载目录   (统一表输出位置)            默认 = 本脚本目录
    UNI_OUTPUT_DIR     输出目录   (run_all 的 14 张表输出)    默认 = 本脚本目录
    UNI_BACKUP_DIR     备份目录   (部署前旧表备份位置, 留空=自动 _backup_<时间戳>) 默认 = 空
    UNI_TARGET_ROOT    目标根     (部署覆盖的旧表根目录)      默认 = 数据库根 (本脚本上级)
    UNI_QUERY          UniProt 检索词                         默认 = '(terpene) AND reviewed:true'
    UNI_DRY_RUN        是否 --dry-run (1/true/yes)            默认 = 0 (直接执行)
    UNI_SKIP_DOWNLOAD  跳过下载 (1/true/yes)                  默认 = 0
    UNI_SKIP_UPDATE    跳过部署 (1/true/yes)                  默认 = 0

用法:
  python run_workflow.py            # 按默认配置跑完整流程
  python run_workflow.py --dry-run  # 只打印将执行的命令, 不实际运行
  # 示例: 只下载+重建, 不部署
  #   export UNI_SKIP_UPDATE=1 && python run_workflow.py
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)

# ================== 配置 (可直接改, 环境变量会覆盖) ==================
CONFIG = {
    'download_dir': HERE,                            # 下载目录
    'output_dir':   HERE,                            # 输出目录
    'backup_dir':   '',                              # 备份目录 (空 = update_database 自动生成)
    'target_root':  REPO_ROOT,                       # 部署目标根 (数据库根)
    'query':        '(terpene) AND reviewed:true',   # UniProt 检索词
    'dry_run':      False,                           # 是否只打印不执行
    'skip_download': False,                          # 跳过下载
    'skip_update': False,                            # 跳过部署
}

# ================== 环境变量覆盖 ==================
ENV_MAP = {
    'download_dir': 'UNI_DOWNLOAD_DIR',
    'output_dir':   'UNI_OUTPUT_DIR',
    'backup_dir':   'UNI_BACKUP_DIR',
    'target_root':  'UNI_TARGET_ROOT',
    'query':        'UNI_QUERY',
    'dry_run':      'UNI_DRY_RUN',
    'skip_download': 'UNI_SKIP_DOWNLOAD',
    'skip_update':  'UNI_SKIP_UPDATE',
}
_TRUE = {'1', 'true', 'yes', 'on'}

def env_bool(v):
    return v.strip().lower() in _TRUE

for key, env in ENV_MAP.items():
    v = os.environ.get(env)
    if v is None:
        continue
    if key in ('dry_run', 'skip_download', 'skip_update'):
        CONFIG[key] = env_bool(v)
    elif key == 'backup_dir' and v.strip() == '':
        CONFIG[key] = ''
    elif v.strip():
        CONFIG[key] = os.path.abspath(v)

# CLI: --dry-run
if '--dry-run' in sys.argv:
    CONFIG['dry_run'] = True

C = CONFIG
DOWNLOAD_DIR = C['download_dir']
OUTPUT_DIR = C['output_dir']
UNIFIED = os.path.join(DOWNLOAD_DIR, 'output_uniprot_unified.tsv')


def run(label, cmd):
    if C['dry_run']:
        print(f'  [DRY-RUN] {label}: ' + ' '.join(cmd))
        return True
    print(f'\n===== {label} =====')
    print('  ' + ' '.join(cmd))
    return subprocess.run(cmd, check=True).returncode == 0


def main():
    print('=' * 62)
    print('萜烯酶数据库 · 全流程启动')
    print('=' * 62)
    print(f'下载目录   : {DOWNLOAD_DIR}')
    print(f'输出目录   : {OUTPUT_DIR}')
    print(f'备份目录   : {C["backup_dir"] or "(自动 _backup_<时间戳>)"}')
    print(f'部署目标根 : {C["target_root"]}')
    print(f'UniProt检索: {C["query"]}')
    print(f'dry-run    : {C["dry_run"]}   跳过下载: {C["skip_download"]}   跳过部署: {C["skip_update"]}')
    print('-' * 62)

    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # ① 下载统一表
    if C['skip_download']:
        print(f'[跳过] 下载 (UNI_SKIP_DOWNLOAD=1), 直接使用 {UNIFIED}')
    else:
        run('download_uniprot.py (下载统一表)',
            [sys.executable, os.path.join(HERE, 'download_uniprot.py'),
             f'--query={C["query"]}', UNIFIED])

    # ② 全量重建
    run('run_all.py --force (重建全部表)',
        [sys.executable, os.path.join(HERE, 'run_all.py'),
         '--force', f'--out-dir={OUTPUT_DIR}', UNIFIED])

    # ③ 部署到原始数据库 (update_database 带备份/列头校验/回滚)
    if C['skip_update']:
        print(f'\n[跳过] 部署 (UNI_SKIP_UPDATE=1)。新表在 {OUTPUT_DIR}, 可稍后手动:')
        print(f'        python update_database.py --new-dir={OUTPUT_DIR}')
    else:
        cmd = [sys.executable, os.path.join(HERE, 'update_database.py'),
               f'--root={C["target_root"]}', f'--new-dir={OUTPUT_DIR}']
        if C['backup_dir']:
            cmd.append(f'--backup-dir={C["backup_dir"]}')
        if C['dry_run']:
            cmd.append('--dry-run')
        run('update_database.py (部署到原始数据库)', cmd)

    print('-' * 62)
    if C['dry_run']:
        print('dry-run 结束: 以上命令均未执行。')
    else:
        print('全流程结束。部署前的旧表备份可用于 --restore 回滚。')


if __name__ == '__main__':
    main()
