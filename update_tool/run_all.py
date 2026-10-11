"""
update_tool 全流程编排 —— 来源分段版。

隔离原则: 本脚本及被调用的脚本只读 ../ 原始目录与 chebi_data/, 输出一律写到输出目录。

## 为什么分两个阶段

每个来源的数据必须能**独立产出、独立更新、独立回滚, 互不覆盖**。
而 update_database.py 是 shutil.copy2 整文件覆盖, for_*/*.tsv 每张表只有一个物理位置，
没有按来源分槽 —— 所以分段必须在这里做, 靠"每个来源产出到自己的目录"来隔离。

分段的边界是「一行是否对应一个 UniProt 条目」:

  --source=<s>   条目级步骤 (11 个), 每个来源各跑一遍 -> _src/<s>/
  --merge        跨条目聚合步骤 (3 个), 读全部分段重建 -> _merged/

跨条目聚合不能分段: 一个 底物->产物 对天然会被两个来源的酶共享, 硬按来源切会产生
重复行和重复节点; 化合物更是与来源无关(同一 ChEBI id 拿到同一个 InChI Key)。

## 用法

  python download_uniprot.py                # 一次下载整个检索集, 按 Reviewed 拆成两段
  python run_all.py --source=swiss_prot     # 只刷新 SwissProt 分段, 不动 TrEMBL
  python run_all.py --source=trembl         # 只刷新 TrEMBL 分段, 不动 SwissProt
  python run_all.py --merge                 # 从全部分段重建汇合表

选项:
  --force        已存在的输出也重跑 (默认跳过已存在的)
  --out-dir=DIR  覆盖默认输出目录 (默认 _src/<source>/ 或 _merged/)
  --src-root=DIR 覆盖分段根目录 (默认 <脚本目录>/_src)。--merge 从这里读各来源分段,
                 沙箱试点时配合 --out-dir 把整条链关在试点目录里。
  RAW            统一表路径, 仅 --source 模式有效
                 (默认 <out-dir>/output_uniprot_unified.tsv)

## 安全阀

`--merge` 会先校验: 所有**已登记来源**(source_registry.SOURCES)的分段输出都齐。
缺任何一个就报错退出, 绝不产出残缺的汇合表 —— 否则"只跑了 SwissProt 就汇合"
会覆盖掉原本完整的版本, 这是分段设计里唯一还能静默丢数据的口子。
同时反向检查 `_src/` 下有没有**未登记**的来源目录, 有也报错(登记表漂移的信号)。

网络步骤 (有断点缓存: fetch_isoform, fetch_references, fetch_sequence_links;
无缓存需整体重跑: fetch_rhea, build_terpene_only):
  fetch_isoform, fetch_rhea, fetch_references, fetch_sequence_links, build_terpene_only
离线步骤: parse_names, fetch_go, build_names_split, build_rhea_summary,
  build_enzyme_merged, rebuild_master, build_terpene_pairs,
  build_terpene_compounds, build_all_nodes
"""
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from source_registry import SOURCES  # noqa: E402

# Tool-bundled reference library (maintained by update_chebi_library.py); the tool can be moved anywhere standalone
CHEBI_FULL = os.path.join(HERE, 'chebi_data', 'chebi_full.tsv')
CHEBI_SMILES = os.path.join(HERE, 'chebi_data', 'chebi_smiles.tsv')

SRC_ROOT = os.path.join(HERE, '_src')      # overridable by --src-root
MERGED_DIR = os.path.join(HERE, '_merged')

# master needs its 5 child tables in one directory (fixed file names), so build a staging dir
CHILD_MAP = {
    'output_go.tsv': 'uniprotkb_go.tsv',
    'output_isoform.tsv': 'uniprotkb_isoform_sequences.tsv',
    'output_references.tsv': 'uniprotkb_references.tsv',
    'output_rhea.tsv': 'uniprotkb_rhea.tsv',
    'output_sequence_links.tsv': 'uniprotkb_sequence_links.tsv',
}

# The 11 outputs of the segmentation stage. --merge's safety valve checks each one's existence against this list.
SEGMENT_OUTPUTS = [
    'output_parsed.tsv',
    'output_go.tsv',
    'output_isoform.tsv',
    'output_rhea.tsv',
    'output_references.tsv',
    'output_sequence_links.tsv',
    'output_names_split.tsv',
    'output_rhea_summary.tsv',
    'output_enzyme_merged.tsv',
    'output_terpene_only.tsv',
    'output_master.tsv',
]

# ---- argument parsing ----
FORCE = '--force' in sys.argv
MODE = None          # 'source' | 'merge'
SOURCE = None
OUT_DIR = None

for a in sys.argv[1:]:
    if a.startswith('--source='):
        MODE, SOURCE = 'source', a[len('--source='):].strip()
    elif a == '--merge':
        MODE = 'merge'
    elif a.startswith('--out-dir='):
        OUT_DIR = os.path.abspath(a[len('--out-dir='):])
    elif a.startswith('--src-root='):
        SRC_ROOT = os.path.abspath(a[len('--src-root='):])

if MODE is None:
    sys.exit(__doc__.strip() + '\n\n错误: 必须指定 --source=<s> 或 --merge')
if MODE == 'source' and SOURCE not in SOURCES:
    sys.exit(f'--source 必须是 {SOURCES} 之一 (登记在 source_registry.py), 收到 {SOURCE!r}')

if OUT_DIR is None:
    OUT_DIR = os.path.join(SRC_ROOT, SOURCE) if MODE == 'source' else MERGED_DIR

# Unified-table path: only meaningful in --source mode
RAW = None
if MODE == 'source':
    given = next((a for a in sys.argv[1:] if not a.startswith('--')), None)
    RAW = os.path.abspath(given) if given else os.path.join(OUT_DIR, 'output_uniprot_unified.tsv')


def P(name):
    return os.path.join(OUT_DIR, name)


def S(name):
    return os.path.join(HERE, name)


def run_step(label, out, cmd):
    if os.path.exists(out) and not FORCE:
        print(f'[skip] {label} (exists; --force 重跑)')
        return
    print(f'\n===== {label} =====')
    subprocess.run(cmd, check=True)


# ---------------------------------------------------------------------------
# Stage 1: entry-level steps (segmented by source)
# ---------------------------------------------------------------------------
def stage_source():
    os.makedirs(OUT_DIR, exist_ok=True)
    if not os.path.exists(RAW):
        sys.exit(f'找不到统一表: {RAW}\n先跑 python download_uniprot.py 下载 (它一次写好两个分段)。')

    steps = [
        ('output_parsed.tsv',   [sys.executable, S('parse_names.py'), RAW, P('output_parsed.tsv')]),
        ('output_go.tsv',       [sys.executable, S('fetch_go.py'), RAW, P('output_go.tsv')]),
        ('output_isoform.tsv',  [sys.executable, S('fetch_isoform.py'), RAW, P('output_isoform.tsv')]),
        ('output_rhea.tsv',     [sys.executable, S('fetch_rhea.py'), RAW, CHEBI_SMILES, P('output_rhea.tsv')]),
        ('output_references.tsv', [sys.executable, S('fetch_references.py'), RAW, P('output_references.tsv')]),
        ('output_sequence_links.tsv', [sys.executable, S('fetch_sequence_links.py'), RAW, P('output_sequence_links.tsv')]),
        ('output_names_split.tsv', [sys.executable, S('build_names_split.py'), P('output_parsed.tsv'), P('output_names_split.tsv')]),
        ('output_rhea_summary.tsv', [sys.executable, S('build_rhea_summary.py'), P('output_rhea.tsv'), RAW, P('output_rhea_summary.tsv')]),
        ('output_enzyme_merged.tsv', [sys.executable, S('build_enzyme_merged.py'), P('output_rhea.tsv'), RAW, P('output_enzyme_merged.tsv')]),
        ('output_terpene_only.tsv', [sys.executable, S('build_terpene_only.py'), P('output_rhea.tsv'), P('output_terpene_only.tsv')]),
    ]

    print(f'模式  : --source={SOURCE}')
    print(f'输出目录: {OUT_DIR}')
    print(f'统一表 RAW: {RAW}')

    for label, cmd in steps:
        run_step(label, P(label), cmd)

    # master needs its 5 child tables in one directory (fixed file names), so build a staging dir
    staging = P('child_tables')
    print('\n===== master (stage child tables) =====')
    os.makedirs(staging, exist_ok=True)
    for src, dst in CHILD_MAP.items():
        src_path = P(src)
        if os.path.exists(src_path):
            shutil.copy(src_path, os.path.join(staging, dst))
        else:
            print(f'  WARN: 缺 {src}, master 里对应的列会全空')
    run_step('output_master.tsv', P('output_master.tsv'),
             [sys.executable, S('rebuild_master.py'), staging, RAW,
              P('output_parsed.tsv'), P('output_master.tsv')])


# ---------------------------------------------------------------------------
# Stage 2: cross-entry aggregation (merged from all segments)
# ---------------------------------------------------------------------------
def check_segments():
    """Safety valve: every registered source's segment output must be present, and there must be no unregistered directories under _src/."""
    problems = []
    for s in SOURCES:
        seg_dir = os.path.join(SRC_ROOT, s)
        if not os.path.isdir(seg_dir):
            problems.append(f'  来源 {s}: 分段目录不存在 ({seg_dir}), 先跑 --source={s}')
            continue
        missing = [f for f in SEGMENT_OUTPUTS if not os.path.exists(os.path.join(seg_dir, f))]
        if missing:
            problems.append(f'  来源 {s}: 缺 {len(missing)} 个产出 -> {", ".join(missing)}')

    registered = set(SOURCES)
    if os.path.isdir(SRC_ROOT):
        for name in sorted(os.listdir(SRC_ROOT)):
            if os.path.isdir(os.path.join(SRC_ROOT, name)) and name not in registered:
                problems.append(f'  {name}: _src/ 下有该来源目录但未登记在 source_registry.SOURCES '
                                f'({list(SOURCES)}) —— 登记表与磁盘漂移, 先补齐登记再汇合')

    if problems:
        sys.exit('--merge 拒绝执行, 分段不完整:\n' + '\n'.join(problems) +
                 '\n\n残缺汇合会覆盖掉原本完整的版本, 所以这里直接退出。')


def concat_terpene_only():
    """Concatenate the sources' terpene_only into one file for the merge steps.

    build_terpene_pairs / _compounds / _all_nodes consume only terpene_only.
    Concatenation is safe: pairs are grouped by (Substrate ChEBI, Product ChEBI) and
    de-duplicated by Entry within a group, so the same enzyme from two sources is not
    double-counted by the concatenation.
    """
    out_path = P('output_terpene_only.tsv')
    header = None
    n = 0
    with open(out_path, 'w', encoding='utf-8', newline='') as fout:
        for s in SOURCES:
            p = os.path.join(SRC_ROOT, s, 'output_terpene_only.tsv')
            with open(p, 'r', encoding='utf-8', newline='') as fin:
                first = fin.readline()
                if header is None:
                    header = first
                    fout.write(first)
                elif first != header:
                    sys.exit(f'分段表头不一致, 拒绝拼接:\n  {SRC_ROOT}/{SOURCES[0]}\n  {p}')
                for line in fin:
                    fout.write(line)
                    n += 1
    print(f'拼接 {len(SOURCES)} 个来源 -> {out_path} ({n} 行)')
    return out_path


def stage_merge():
    check_segments()
    os.makedirs(OUT_DIR, exist_ok=True)

    print(f'模式  : --merge')
    print(f'输出目录: {OUT_DIR}')
    print(f'来源  : {", ".join(SOURCES)} (全部已就位)')

    # The merge steps' input = the concatenation of each source's terpene_only. It is
    # re-concatenated on every merge, otherwise a changed source segment with a stale
    # concatenation would leave the merged tables inconsistent with the segments.
    merged_only = concat_terpene_only()

    steps = [
        ('output_terpene_pairs.tsv',
         [sys.executable, S('build_terpene_pairs.py'), merged_only, P('output_terpene_pairs.tsv')]),
        ('output_terpene_compounds.tsv',
         [sys.executable, S('build_terpene_compounds.py'), merged_only, CHEBI_FULL, P('output_terpene_compounds.tsv')]),
        ('output_all_nodes.tsv',
         [sys.executable, S('build_all_nodes.py'), merged_only, CHEBI_FULL, P('output_all_nodes.tsv')]),
    ]
    for label, cmd in steps:
        run_step(label, P(label), cmd)


if MODE == 'source':
    stage_source()
else:
    stage_merge()

print('\n完成!')
