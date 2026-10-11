"""
Deploy (overwrite) the new output of update_tool/ onto the matching old table
locations in the original database.

Isolation principles:
  - This script lives inside update_tool/ and never touches the original code.
  - By default the old tables are backed up first to <backup dir>/<relative path>,
    then overwritten -- a problem can be rolled back with one command.
  - For testing, point --root at a sandbox directory: same code path, no touching
    the original data.

## Source segmentation

The 11 segmented tables are written with a source suffix (`<name>.<source>.tsv`);
the 3 merged tables carry none -- see the plan's "core design: isolation model".
So this script must explicitly state which segment to deploy:

  --source=<s>   deploy that source's 11 segmented tables -> for_*/<name>.<s>.tsv
  --merge        deploy the 3 merged tables               -> for_*/<name>.tsv (no suffix)

Running only `--source=trembl` leaves SwissProt's 11 files untouched -- that is
"refresh one source without touching the other".

Usage:
  python update_database.py --source=swiss_prot --dry-run    # print the plan only, write nothing
  python update_database.py --source=trembl --root=<sandbox>  # real overwrite test into the sandbox
  python update_database.py --merge
  python update_database.py --source=trembl --target=output_go.tsv   # update only the named table
  python update_database.py --restore=<backup dir>            # roll old tables back from a backup

Arguments:
  --root=DIR       root of the target old tables, default = parent of update_tool (database root)
  --new-dir=DIR    directory of the new output, default = this script's directory
  --backup-dir=DIR backup directory, default = <new-dir>/_backup_<timestamp>
  --dry-run        print the overwrite plan only
  --target=X[,Y..] update only the named new output files (may be several)
  --no-backup      do not back up before overwriting
  --restore=DIR    rollback mode: copy the backups under DIR back to their --root locations
  --allow-header-change
                   also overwrite when the header changes (the 5 dynamic-width
                   tables already pass by default, see below)
  --allow-shrink   also overwrite when the target row count shrinks by more than half
                   (refused by default, see below)
"""
import csv
import os
import shutil
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from source_registry import SOURCES  # noqa: E402

DEFAULT_ROOT = os.path.dirname(HERE)          # database root directory
# New output directory: matches run_all.py's output locations (--source=<s> -> _src/<s>/, --merge -> _merged/)
DEFAULT_SRC_ROOT = os.path.join(HERE, '_src')
DEFAULT_MERGED_DIR = os.path.join(HERE, '_merged')

# (new output file name, old table relative path, whether segmented by source)
#   segmented=True  -> written to for_*/<name>.<source>.tsv, deployed by --source=<s>
#   segmented=False -> written to for_*/<name>.tsv (no suffix), deployed by --merge
# The segmentation criterion is "does a row correspond to one UniProt entry"; tables that
# aggregate across entries (pairs/compounds/all_nodes) are naturally shared by both sources,
# so cutting them hard would create duplicate rows and duplicate nodes.
MAPPING = [
    ('output_parsed.tsv', 'uniprotkb_terpene_parsed.tsv', True),
    ('output_names_split.tsv', 'for_enzyme_detail/child_tables/uniprotkb_names_split.tsv', True),
    ('output_go.tsv', 'for_enzyme_detail/child_tables/uniprotkb_go.tsv', True),
    ('output_isoform.tsv', 'for_enzyme_detail/child_tables/uniprotkb_isoform_sequences.tsv', True),
    ('output_rhea.tsv', 'for_enzyme_detail/child_tables/uniprotkb_rhea.tsv', True),
    ('output_references.tsv', 'for_enzyme_detail/child_tables/uniprotkb_references.tsv', True),
    ('output_sequence_links.tsv', 'for_enzyme_detail/child_tables/uniprotkb_sequence_links.tsv', True),
    ('output_rhea_summary.tsv', 'for_enzyme_reaction_card/uniprotkb_rhea_summary.tsv', True),
    ('output_enzyme_merged.tsv', 'for_enzyme_reaction_card/uniprotkb_enzyme_merged.tsv', True),
    ('output_terpene_only.tsv', 'for_graph/uniprotkb_terpene_only.tsv', True),
    ('output_master.tsv', 'for_enzyme_detail/uniprotkb_master.tsv', True),
    ('output_terpene_pairs.tsv', 'for_graph/uniprotkb_terpene_pairs.tsv', False),
    ('output_terpene_compounds.tsv', 'for_compound_card/uniprotkb_terpene_compounds.tsv', False),
    ('output_all_nodes.tsv', 'for_graph/all_nodes.tsv', False),
]

# Tables whose column width varies with the data size and does not count as "format
# mismatch". Their header changes are **expected**, so they pass by default and the old/new
# column counts are printed; the other 9 have fixed width, and a header change there means
# upstream changed the format, so it is still blocked.
DYNAMIC_WIDTH = {
    'output_references.tsv',      # column count = the most references of any one entry
    'output_sequence_links.tsv',  # column count = the most EMBL/RefSeq cross-refs of any one entry
    'output_enzyme_merged.tsv',   # column count = the most reactions of any one enzyme
    'output_terpene_pairs.tsv',   # column count = the most enzymes of any one substrate-product pair
    'output_master.tsv',          # aggregates all the child tables above, so its column count changes too
}


def with_source(rel_path, source):
    """Insert the source suffix into the filename of a relative path: for_x/y.tsv -> for_x/y.<source>.tsv"""
    base, ext = os.path.splitext(rel_path)
    return f'{base}.{source}{ext}'


def parse_args(argv):
    args = {
        'root': DEFAULT_ROOT,
        'new_dir': None,        # None = derived from the mode; see derive_new_dir below and --new-dir=
        'backup_dir': None,
        'dry_run': False,
        'targets': None,        # None = all
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

    # The new-file directory is derived from the mode -- without deriving it, new_dir would
    # stay at update_tool/ itself, so every table would report "new output missing" and be
    # skipped, yet exit code 0 would print "done: 0 updated": it looks like "nothing to
    # deploy" rather than an error. Worse, output_*.tsv left over in update_tool/ before the
    # rework would be deployed as some source's segment (silently writing wrong values).
    # An explicit --new-dir= still takes precedence.
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
        # Segmented tables are written with a source suffix; merged tables have none
        if is_segmented:
            rel_target = with_source(rel_target, a['source'])
        old_path = os.path.join(a['root'], rel_target)

        # 1) The new output must exist
        if not os.path.exists(new_path):
            skipped.append((new_name, '新输出不存在'))
            print(f'  [跳过] {new_name}  新输出不存在 ({new_path})')
            continue

        # 2) Header check. A missing old table is treated as a brand-new table, deployed directly.
        if os.path.exists(old_path):
            nh = header(new_path)
            oh = header(old_path)
            if nh != oh:
                # A dynamic-width table's column count is meant to change -- let it pass, but
                # print the count change so a silently disappearing new column is visible in
                # the log too.
                if new_name in DYNAMIC_WIDTH or a['allow_header_change']:
                    why = '宽度动态(预期)' if new_name in DYNAMIC_WIDTH else '--allow-header-change'
                    print(f'  [列头变化] {new_name} -> {rel_target}  {why}')
                    print(f'         列数 {len(oh)} -> {len(nh)}')
                    if len(nh) < len(oh):
                        lost = [c for c in oh if c not in set(nh)]
                        print(f'         WARN 减少的列 ({len(lost)}): {lost[:8]}{" ..." if len(lost) > 8 else ""}')
                    header_changed.append((new_name, len(oh), len(nh)))
                else:
                    # A fixed-width table's header changed = upstream changed the format; stop for a human to look, do not deploy silently.
                    skipped.append((new_name, '列头不一致'))
                    print(f'  [跳过] {new_name} -> {rel_target}  列头不一致')
                    print(f'         新({len(nh)}): {nh[:4]}...')
                    print(f'         旧({len(oh)}): {oh[:4]}...')
                    print(f'         宽度固定的表不该变列头; 确认无误后加 --allow-header-change 重跑')
                    continue

        # 3) Empty-table guard. Overwriting a populated old table with an empty one = silently
        #    wiping the database, must be blocked. But for a **new** table (target does not
        #    exist yet) 0 rows may be an honest result -- e.g. a source genuinely has no
        #    isoform data. Then pass with a warning, otherwise that source's segment file
        #    could never be written, and the ETL would error on the missing segment file,
        #    reporting "there was never any data" as "the pipeline broke".
        n_new = count_rows(new_path)
        old_exists = os.path.exists(old_path)
        n_old = count_rows(old_path) if old_exists else None
        if n_new == 0:
            if old_exists and n_old:
                skipped.append((new_name, '新输出为空'))
                print(f'  [跳过] {new_name}  新输出无数据行 (旧表有 {n_old} 行, 拒绝用空表覆盖)')
                continue
            print(f'  [空表] {new_name} -> {rel_target}  0 数据行, 按全新空段部署')

        # A row-count collapse is the signal most like "silently losing data", and the
        # overwrite is destructive (even with a backup). So it is **refused** by default;
        # a human must look and explicitly allow it. Typical trigger: --merge-ing a
        # pilot-scale segment onto the real root, which would swap the complete
        # pairs/compounds/all_nodes for a one-hour slice. A normal full update only grows
        # (enzymes 996 -> 96k) and should not reach here.
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

        # 4) Back up the old table (preserving the relative path)
        if not a['no_backup'] and os.path.exists(old_path):
            bak = os.path.join(a['backup_dir'], rel_target)
            os.makedirs(os.path.dirname(bak), exist_ok=True)
            shutil.copy2(old_path, bak)

        # 5) Overwrite
        os.makedirs(os.path.dirname(old_path), exist_ok=True)
        shutil.copy2(new_path, old_path)
        print(f'  [更新] {new_name} -> {rel_target}{row_note}')
        updated.append(new_name)

    # Stale leftovers: the set of suffix-less segment copies from before the rework. The ETL
    # now reads only the suffixed segment files, so these copies are **no longer read by
    # anything** and keeping them only makes people think segmentation did not take effect.
    # Still not deleted automatically -- deleting files is irreversible, and before a full
    # run passes they are the only fallback reference.
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

    # "Nothing deployed, and every reason was new output missing" is almost always a wrong
    # path, not genuinely no data. Must exit non-zero, otherwise the caller (or a human)
    # would take the no-op for a successful empty update.
    if skipped and not updated and all(w == '新输出不存在' for _, w in skipped):
        print(f'\n错误: {len(skipped)} 张表全部因「新输出不存在」被跳过, 没有可部署的东西')
        print(f'      新输出目录: {a["new_dir"]}')
        print('      --source=<s> 默认读 _src/<s>/, --merge 默认读 _merged/; '
              '已按预期跑过 run_all 的话, 检查该目录是否真的有输出。')
        sys.exit(1)


if __name__ == '__main__':
    main()
