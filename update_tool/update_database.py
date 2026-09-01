"""
把 update_tool/ 的新输出部署(覆盖)到原始数据库的对应旧表位置。

隔离原则:
  - 本脚本写在 update_tool/ 内, 不碰原始代码。
  - 默认把旧表先备份到 <备份目录>/<相对路径>, 再覆盖 —— 出问题可一键回滚。
  - 默认只在列头与旧表完全一致时才覆盖(形式对上), 否则跳过并报告。
  - 测试时用 --root 指向一个沙箱目录, 走同一条代码路径, 不碰原始数据。

用法:
  python update_database.py --dry-run            # 只打印计划, 不写任何文件 (隔离测试第一步)
  python update_database.py --root=update_tool/_sandbox   # 对沙箱做真实覆盖测试
  python update_database.py --target=output_master.tsv      # 只更新指定表(可逗号分隔/重复给)
  python update_database.py --no-backup          # 不备份直接覆盖 (不推荐)
  python update_database.py --restore=<备份目录> # 用历史备份回滚旧表

参数:
  --root=DIR       目标旧表所在根目录, 默认 = update_tool 的上级 (数据库根)
  --new-dir=DIR    新输出所在目录, 默认 = 本脚本所在目录
  --backup-dir=DIR 备份目录, 默认 = <new-dir>/_backup_<时间戳>
  --dry-run        只打印将执行的覆盖计划
  --target=X[,Y..] 只更新指定新输出文件(可多个)
  --no-backup      覆盖前不备份
  --restore=DIR    回滚模式: 把 DIR 下的备份复制回 --root 对应位置
"""
import csv
import os
import shutil
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_ROOT = os.path.dirname(HERE)          # 数据库根目录
DEFAULT_NEW_DIR = HERE                        # 新输出目录

# 新输出文件名 -> 原始旧表相对路径
MAPPING = [
    ('output_parsed.tsv', 'uniprotkb_terpene_parsed.tsv'),
    ('output_names_split.tsv', 'for_enzyme_detail/child_tables/uniprotkb_names_split.tsv'),
    ('output_go.tsv', 'for_enzyme_detail/child_tables/uniprotkb_go.tsv'),
    ('output_isoform.tsv', 'for_enzyme_detail/child_tables/uniprotkb_isoform_sequences.tsv'),
    ('output_rhea.tsv', 'for_enzyme_detail/child_tables/uniprotkb_rhea.tsv'),
    ('output_references.tsv', 'for_enzyme_detail/child_tables/uniprotkb_references.tsv'),
    ('output_sequence_links.tsv', 'for_enzyme_detail/child_tables/uniprotkb_sequence_links.tsv'),
    ('output_rhea_summary.tsv', 'for_enzyme_reation_card/uniprotkb_rhea_summary.tsv'),
    ('output_enzyme_merged.tsv', 'for_enzyme_reation_card/uniprotkb_enzyme_merged.tsv'),
    ('output_terpene_only.tsv', 'for_graph/uniprotkb_terpene_only.tsv'),
    ('output_terpene_pairs.tsv', 'for_graph/uniprotkb_terpene_pairs.tsv'),
    ('output_terpene_compounds.tsv', 'for_compound_card/uniprotkb_terpene_compounds.tsv'),
    ('output_all_nodes.tsv', 'for_graph/all_nodes.tsv'),
    ('output_master.tsv', 'for_enzyme_detail/uniprotkb_master.tsv'),
]


def parse_args(argv):
    args = {
        'root': DEFAULT_ROOT,
        'new_dir': DEFAULT_NEW_DIR,
        'backup_dir': None,
        'dry_run': False,
        'targets': None,        # None = 全部
        'no_backup': False,
        'restore': None,
    }
    targets = []
    for a in argv:
        if a == '--dry-run':
            args['dry_run'] = True
        elif a.startswith('--root='):
            args['root'] = os.path.abspath(a[len('--root='):])
        elif a.startswith('--new-dir='):
            args['new_dir'] = os.path.abspath(a[len('--new-dir='):])
        elif a.startswith('--backup-dir='):
            args['backup_dir'] = os.path.abspath(a[len('--backup-dir='):])
        elif a.startswith('--target='):
            targets += [t.strip() for t in a[len('--target='):].split(',') if t.strip()]
        elif a == '--no-backup':
            args['no_backup'] = True
        elif a.startswith('--restore='):
            args['restore'] = os.path.abspath(a[len('--restore='):])
    args['targets'] = targets or None
    return args


def header(path):
    with open(path, 'r', encoding='utf-8') as f:
        return next(csv.reader(f, delimiter='\t'))


def count_rows(path):
    with open(path, 'r', encoding='utf-8') as f:
        return sum(1 for _ in f) - 1


def do_restore(restore_dir, root):
    print(f'[restore] 从 {restore_dir} 回滚到 {root}')
    n = 0
    for walk_root, _, files in os.walk(restore_dir):
        for fn in files:
            bak = os.path.join(walk_root, fn)
            rel = os.path.relpath(bak, restore_dir)
            dst = os.path.join(root, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(bak, dst)
            print(f'  restored {rel}')
            n += 1
    print(f'[restore] 完成, 共 {n} 个文件回滚')


def main():
    a = parse_args(sys.argv[1:])

    if a['restore']:
        do_restore(a['restore'], a['root'])
        return

    if not a['backup_dir']:
        stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        a['backup_dir'] = os.path.join(a['new_dir'], f'_backup_{stamp}')

    mode = 'DRY-RUN(仅打印, 不写入)' if a['dry_run'] else '执行'
    print(f'更新数据库: 根={a["root"]}  新输出={a["new_dir"]}')
    print(f'模式: {mode}  备份目录: {a["backup_dir"] if not a["no_backup"] else "(不备份)"}\n')

    updated, skipped = [], []
    for new_name, rel_target in MAPPING:
        if a['targets'] and new_name not in a['targets']:
            continue

        new_path = os.path.join(a['new_dir'], new_name)
        old_path = os.path.join(a['root'], rel_target)

        # 1) 新输出必须存在
        if not os.path.exists(new_path):
            skipped.append((new_name, '新输出不存在'))
            print(f'  [跳过] {new_name}  新输出不存在')
            continue

        # 2) 旧表存在时才校验列头; 旧表不存在视为全新表, 直接部署
        if os.path.exists(old_path):
            nh = header(new_path)
            oh = header(old_path)
            if nh != oh:
                skipped.append((new_name, '列头不一致'))
                print(f'  [跳过] {new_name} -> {rel_target}  列头不一致')
                print(f'         新: {nh[:4]}...')
                print(f'         旧: {oh[:4]}...')
                continue

        # 3) 新输出不能是空表(至少 1 行数据)
        n_new = count_rows(new_path)
        if n_new == 0:
            skipped.append((new_name, '新输出为空'))
            print(f'  [跳过] {new_name}  新输出无数据行')
            continue

        n_old = count_rows(old_path) if os.path.exists(old_path) else None
        row_note = f'  行数 {n_new}' + (f' (旧 {n_old})' if n_old is not None else ' (新表)')

        if a['dry_run']:
            print(f'  [计划] {new_name} -> {rel_target}{row_note}')
            updated.append(new_name)
            continue

        # 4) 备份旧表(保留相对路径)
        if not a['no_backup'] and os.path.exists(old_path):
            bak = os.path.join(a['backup_dir'], rel_target)
            os.makedirs(os.path.dirname(bak), exist_ok=True)
            shutil.copy2(old_path, bak)

        # 5) 覆盖
        os.makedirs(os.path.dirname(old_path), exist_ok=True)
        shutil.copy2(new_path, old_path)
        print(f'  [更新] {new_name} -> {rel_target}{row_note}')
        updated.append(new_name)

    print(f'\n完成: {len(updated)} 个已更新' + (f' (dry-run)' if a['dry_run'] else '') +
          f', {len(skipped)} 个跳过')
    for name, why in skipped:
        print(f'  - {name}: {why}')
    if not a['dry_run'] and not a['no_backup'] and updated:
        print(f'备份保存在: {a["backup_dir"]} (回滚: python update_database.py --restore=该目录)')


if __name__ == '__main__':
    main()
