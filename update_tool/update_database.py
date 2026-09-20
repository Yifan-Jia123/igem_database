"""
把 update_tool/ 的新输出部署(覆盖)到原始数据库的对应旧表位置。

隔离原则:
  - 本脚本写在 update_tool/ 内, 不碰原始代码。
  - 默认把旧表先备份到 <备份目录>/<相对路径>, 再覆盖 —— 出问题可一键回滚。
  - 测试时用 --root 指向一个沙箱目录, 走同一条代码路径, 不碰原始数据。

## 来源分段

分段的 11 张表落盘带来源后缀 (`<name>.<source>.tsv`), 汇合的 3 张不带 —— 见
plan「核心设计: 隔离模型」。所以本脚本必须显式指定部署哪一段:

  --source=<s>   部署该来源的 11 张分段表 -> for_*/<name>.<s>.tsv
  --merge        部署 3 张汇合表         -> for_*/<name>.tsv (无后缀)

只跑 `--source=trembl` 时 SwissProt 的 11 个文件原样不动 —— 这就是「刷新一个来源
不动另一个来源」。

用法:
  python update_database.py --source=swiss_prot --dry-run    # 只打印计划, 不写文件
  python update_database.py --source=trembl --root=<沙箱>     # 对沙箱做真实覆盖测试
  python update_database.py --merge
  python update_database.py --source=trembl --target=output_go.tsv   # 只更新指定表
  python update_database.py --restore=<备份目录>              # 用历史备份回滚旧表

参数:
  --root=DIR       目标旧表所在根目录, 默认 = update_tool 的上级 (数据库根)
  --new-dir=DIR    新输出所在目录, 默认 = 本脚本所在目录
  --backup-dir=DIR 备份目录, 默认 = <new-dir>/_backup_<时间戳>
  --dry-run        只打印将执行的覆盖计划
  --target=X[,Y..] 只更新指定新输出文件(可多个)
  --no-backup      覆盖前不备份
  --restore=DIR    回滚模式: 把 DIR 下的备份复制回 --root 对应位置
  --allow-header-change
                   列头变化时也覆盖 (宽度动态的 5 张表已默认放行, 见下)
  --allow-shrink   目标行数缩减过半时也覆盖 (默认拒绝, 见下)
"""
import csv
import os
import shutil
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from source_registry import SOURCES  # noqa: E402

DEFAULT_ROOT = os.path.dirname(HERE)          # 数据库根目录
# 新输出目录: 与 run_all.py 的落盘位置一致 (--source=<s> -> _src/<s>/, --merge -> _merged/)
DEFAULT_SRC_ROOT = os.path.join(HERE, '_src')
DEFAULT_MERGED_DIR = os.path.join(HERE, '_merged')

# (新输出文件名, 原始旧表相对路径, 是否按来源分段)
#   分段=True  -> 落盘 for_*/<name>.<source>.tsv, 由 --source=<s> 部署
#   分段=False -> 落盘 for_*/<name>.tsv (无后缀), 由 --merge 部署
# 分段的判据是「一行是否对应一个 UniProt 条目」; 跨条目聚合的表(pairs/compounds/all_nodes)
# 天然被两个来源共享, 硬切会产生重复行和重复节点。
MAPPING = [
    ('output_parsed.tsv', 'uniprotkb_terpene_parsed.tsv', True),
    ('output_names_split.tsv', 'for_enzyme_detail/child_tables/uniprotkb_names_split.tsv', True),
    ('output_go.tsv', 'for_enzyme_detail/child_tables/uniprotkb_go.tsv', True),
    ('output_isoform.tsv', 'for_enzyme_detail/child_tables/uniprotkb_isoform_sequences.tsv', True),
    ('output_rhea.tsv', 'for_enzyme_detail/child_tables/uniprotkb_rhea.tsv', True),
    ('output_references.tsv', 'for_enzyme_detail/child_tables/uniprotkb_references.tsv', True),
    ('output_sequence_links.tsv', 'for_enzyme_detail/child_tables/uniprotkb_sequence_links.tsv', True),
    ('output_rhea_summary.tsv', 'for_enzyme_reation_card/uniprotkb_rhea_summary.tsv', True),
    ('output_enzyme_merged.tsv', 'for_enzyme_reation_card/uniprotkb_enzyme_merged.tsv', True),
    ('output_terpene_only.tsv', 'for_graph/uniprotkb_terpene_only.tsv', True),
    ('output_master.tsv', 'for_enzyme_detail/uniprotkb_master.tsv', True),
    ('output_terpene_pairs.tsv', 'for_graph/uniprotkb_terpene_pairs.tsv', False),
    ('output_terpene_compounds.tsv', 'for_compound_card/uniprotkb_terpene_compounds.tsv', False),
    ('output_all_nodes.tsv', 'for_graph/all_nodes.tsv', False),
]

# 列宽随数据规模变化、不构成「格式不一致」的表。它们的列头变化是**预期**的,
# 默认放行并打印新旧列数; 其余 9 张宽度固定, 列头变化说明上游改了格式, 仍然拦住。
DYNAMIC_WIDTH = {
    'output_references.tsv',      # 列数 = 该条目最多几条文献
    'output_sequence_links.tsv',  # 列数 = 该条目最多几条 EMBL/RefSeq 交叉引用
    'output_enzyme_merged.tsv',   # 列数 = 该酶最多几个反应
    'output_terpene_pairs.tsv',   # 列数 = 某一对底物-产物最多几个酶
    'output_master.tsv',          # 聚合上述所有子表, 列数必然跟着变
}


def with_source(rel_path, source):
    """给相对路径的文件名插进来源后缀: for_x/y.tsv -> for_x/y.<source>.tsv"""
    base, ext = os.path.splitext(rel_path)
    return f'{base}.{source}{ext}'


def parse_args(argv):
    args = {
        'root': DEFAULT_ROOT,
        'new_dir': None,        # None = 由模式推导; 见下方 derive_new_dir 与 --new-dir=
        'backup_dir': None,
        'dry_run': False,
        'targets': None,        # None = 全部
        'no_backup': False,
        'restore': None,
        'source': None,
        'merge': False,
        'allow_header_change': False,
        'allow_shrink': False,
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
        elif a == '--allow-header-change':
            args['allow_header_change'] = True
        elif a == '--allow-shrink':
            args['allow_shrink'] = True
        elif a.startswith('--source='):
            args['source'] = a[len('--source='):].strip()
        elif a == '--merge':
            args['merge'] = True
        elif a.startswith('--restore='):
            args['restore'] = os.path.abspath(a[len('--restore='):])
    args['targets'] = targets or None

    if args['restore']:
        return args
    if args['source'] and args['merge']:
        sys.exit('--source 与 --merge 只能选一个')
    if not args['source'] and not args['merge']:
        sys.exit('必须指定 --source=<s> 或 --merge\n'
                 f'  --source=<s> 部署该来源的分段表 (登记在 source_registry.py: {SOURCES})\n'
                 '  --merge      部署汇合表')
    if args['source'] and args['source'] not in SOURCES:
        sys.exit(f'--source 必须是 {SOURCES} 之一, 收到 {args["source"]!r}')

    # 新文件目录由模式推导 —— 不推导的话 new_dir 会停在 update_tool/ 本身, 于是
    # 每张表都报「新输出不存在」并被跳过, 却以退出码 0 打印「完成: 0 个已更新」:
    # 看起来像「没有要部署的东西」而不是错误。更糟的是 update_tool/ 根下若留有
    # 改造前的 output_*.tsv, 会被当成某个来源的分段部署下去(静默写错值)。
    # 显式 --new-dir= 仍然优先。
    if args['new_dir'] is None:
        args['new_dir'] = (DEFAULT_MERGED_DIR if args['merge']
                           else os.path.join(DEFAULT_SRC_ROOT, args['source']))
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

    seg_mode = not a['merge']
    scope = f'--source={a["source"]} (分段 11 张, 落盘带 .{a["source"]} 后缀)' if seg_mode \
            else '--merge (汇合 3 张, 落盘无后缀)'
    mode = 'DRY-RUN(仅打印, 不写入)' if a['dry_run'] else '执行'
    print(f'更新数据库: 根={a["root"]}  新输出={a["new_dir"]}')
    print(f'模式: {scope}')
    print(f'      {mode}  备份目录: {a["backup_dir"] if not a["no_backup"] else "(不备份)"}\n')

    updated, skipped, header_changed, stale = [], [], [], []
    for new_name, rel_target, is_segmented in MAPPING:
        if is_segmented != seg_mode:
            continue
        if a['targets'] and new_name not in a['targets']:
            continue

        new_path = os.path.join(a['new_dir'], new_name)
        # 分段表落盘带来源后缀; 汇合表无后缀
        if is_segmented:
            rel_target = with_source(rel_target, a['source'])
        old_path = os.path.join(a['root'], rel_target)

        # 1) 新输出必须存在
        if not os.path.exists(new_path):
            skipped.append((new_name, '新输出不存在'))
            print(f'  [跳过] {new_name}  新输出不存在 ({new_path})')
            continue

        # 2) 列头校验。旧表不存在视为全新表, 直接部署。
        if os.path.exists(old_path):
            nh = header(new_path)
            oh = header(old_path)
            if nh != oh:
                # 宽度动态的表列数本来就该变 —— 放行, 但把列数变化打出来,
                # 这样「新列静默消失」也会在日志里看得见。
                if new_name in DYNAMIC_WIDTH or a['allow_header_change']:
                    why = '宽度动态(预期)' if new_name in DYNAMIC_WIDTH else '--allow-header-change'
                    print(f'  [列头变化] {new_name} -> {rel_target}  {why}')
                    print(f'         列数 {len(oh)} -> {len(nh)}')
                    if len(nh) < len(oh):
                        lost = [c for c in oh if c not in set(nh)]
                        print(f'         WARN 减少的列 ({len(lost)}): {lost[:8]}{" ..." if len(lost) > 8 else ""}')
                    header_changed.append((new_name, len(oh), len(nh)))
                else:
                    # 宽度固定的表列头变了 = 上游改了格式, 停下让人看, 不静默部署。
                    skipped.append((new_name, '列头不一致'))
                    print(f'  [跳过] {new_name} -> {rel_target}  列头不一致')
                    print(f'         新({len(nh)}): {nh[:4]}...')
                    print(f'         旧({len(oh)}): {oh[:4]}...')
                    print(f'         宽度固定的表不该变列头; 确认无误后加 --allow-header-change 重跑')
                    continue

        # 3) 空表守卫。用空表覆盖有数据的旧表 = 静默清库, 必须拦住。
        #    但**新表**(目标还不存在)时 0 行可能是诚实结果 —— 例如某个来源确实
        #    没有 isoform 数据。这时放行并告警, 否则该来源的分段文件永远落不了盘,
        #    ETL 会因为找不到分段落盘文件而报错, 把「本来就没数据」误报成「流程坏了」。
        n_new = count_rows(new_path)
        old_exists = os.path.exists(old_path)
        n_old = count_rows(old_path) if old_exists else None
        if n_new == 0:
            if old_exists and n_old:
                skipped.append((new_name, '新输出为空'))
                print(f'  [跳过] {new_name}  新输出无数据行 (旧表有 {n_old} 行, 拒绝用空表覆盖)')
                continue
            print(f'  [空表] {new_name} -> {rel_target}  0 数据行, 按全新空段部署')

        # 行数塌缩 = 最像「静默丢数据」的信号, 而且覆盖是破坏性的(虽有备份)。
        # 所以默认**拒绝**, 要人看过再显式放行。典型触发场景: 拿试点规模的分段
        # 去 --merge 到真实根目录, 会把完整的 pairs/compounds/all_nodes 换成一小时片。
        # 正常全量更新只会增长(酶 996 -> 96k), 不该走到这里。
        shrink = ''
        if n_old and n_new < n_old * 0.5:
            if not a['allow_shrink']:
                skipped.append((new_name, f'行数缩减过半 {n_old} -> {n_new}'))
                print(f'  [跳过] {new_name} -> {rel_target}  行数缩减过半 {n_old} -> {n_new}')
                print(f'         确认这是预期的话, 加 --allow-shrink 重跑')
                continue
            shrink = f'  WARN 行数缩减 {n_old} -> {n_new} (已 --allow-shrink 放行)'
        row_note = f'  行数 {n_new}' + (f' (旧 {n_old})' if n_old is not None else ' (新表)') + shrink

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

    # 陈旧残留: 改造前那套无后缀的分段副本。ETL 已改成只读带后缀的分段文件,
    # 所以这些副本**不再被任何东西读到**, 留着只会让人以为分段没生效。
    # 仍然不自动删 —— 删文件不可逆, 而且全量跑通前它们是唯一的回退参照。
    if seg_mode:
        for new_name, rel_target, is_segmented in MAPPING:
            if not is_segmented:
                continue
            legacy = os.path.join(a['root'], rel_target)
            if os.path.exists(legacy):
                stale.append(rel_target)

    print(f'\n完成: {len(updated)} 个已更新' + (f' (dry-run)' if a['dry_run'] else '') +
          f', {len(skipped)} 个跳过')
    for name, why in skipped:
        print(f'  - {name}: {why}')
    for name, old_n, new_n in header_changed:
        print(f'  * {name}: 列头已变 {old_n} -> {new_n} 列')
    if stale:
        print(f'\nWARN 检测到 {len(stale)} 个改造前的无后缀副本仍在 for_* 里:')
        for p in stale:
            print(f'  - {p}')
        print('   ETL 已改成只读带 .<source> 后缀的分段文件, 这些无后缀副本不再被读到。')
        print('   全量跑通并验证过之前先别删 (它们是回退参照)。')
    if not a['dry_run'] and not a['no_backup'] and updated:
        print(f'备份保存在: {a["backup_dir"]} (回滚: python update_database.py --restore=该目录)')

    # 「一张都没部署, 且全部原因是新输出不存在」几乎总是路径指错了, 不是真的没数据。
    # 必须以非 0 退出, 否则调用方(或人)会把 no-op 当成成功的空更新。
    if skipped and not updated and all(w == '新输出不存在' for _, w in skipped):
        print(f'\n错误: {len(skipped)} 张表全部因「新输出不存在」被跳过, 没有可部署的东西')
        print(f'      新输出目录: {a["new_dir"]}')
        print('      --source=<s> 默认读 _src/<s>/, --merge 默认读 _merged/; '
              '已按预期跑过 run_all 的话, 检查该目录是否真的有输出。')
        sys.exit(1)


if __name__ == '__main__':
    main()
