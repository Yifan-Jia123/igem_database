"""
update_tool 全流程编排: 输入新导出的原始 UniProt TSV -> 生成全部 13 张最终表。

隔离原则: 本脚本及被调用的脚本全部只读 ../ 原始目录, 输出一律写到输出目录 (默认本文件夹)。
换新数据时: 把新导出覆盖到对应的原始 TSV 路径 (或改下面默认参数), 运行 python run_all.py。

默认输入 (只读):
  RAW_UNIFIED : output_uniprot_unified.tsv (download_uniprot.py 下载的 19 列统一全列表,
                 = 旧三导出 0710/0712/0716 全部维度的并集, 已验证与三导出逐格 0 差异)
  CHEBI_FULL / CHEBI_SMILES : <脚本目录>/chebi_data/* (工具自带参考库, 只读)

网络步骤 (会访问 UniProt REST / Rhea SPARQL / PubChem; 有断点缓存的:
  fetch_isoform, fetch_references, fetch_sequence_links, build_all_nodes;
  无缓存、需整体重跑的: fetch_rhea, build_terpene_only):
  fetch_isoform, fetch_rhea, fetch_references, fetch_sequence_links,
  build_terpene_only (Rhea 短名), build_all_nodes (InChI Key)
离线步骤: parse_names, fetch_go (读统一表 'Gene Ontology (biological process)' 列, BP-only),
  build_names_split, build_rhea_summary, build_enzyme_merged,
  build_terpene_pairs, build_terpene_compounds, rebuild_master

用法:
  python download_uniprot.py                     # 1) 下载统一全列表 (新数据时重跑)
  python run_all.py [--force] [--out-dir=DIR] [RAW]   # 2) 已有输出自动跳过; --force 强制全部重跑
                                                     #    --out-dir: 输出目录 (默认本脚本目录)
                                                     #    RAW: 统一表路径 (默认 <out-dir>/output_uniprot_unified.tsv)
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# ---- 参数解析 ----
FORCE = '--force' in sys.argv
OUT_DIR = HERE
for a in sys.argv[1:]:
    if a.startswith('--out-dir='):
        OUT_DIR = os.path.abspath(a[len('--out-dir='):])
# RAW: 第一个非 -- 参数; 相对路径按当前工作目录解析 (默认 <out-dir>/output_uniprot_unified.tsv)
RAW = next((a for a in sys.argv[1:] if not a.startswith('--')), None)
if RAW is None:
    RAW = os.path.join(OUT_DIR, 'output_uniprot_unified.tsv')
else:
    RAW = os.path.abspath(RAW)

# ---- 路径助手: 输出文件 / 脚本 / 静态参考库 ----
def P(name):
    return os.path.join(OUT_DIR, name)

def S(name):
    return os.path.join(HERE, name)

# 工具自带参考库(update_chebi_library.py 维护), 工具可独立搬到任何位置
CHEBI_FULL = os.path.join(HERE, 'chebi_data', 'chebi_full.tsv')
CHEBI_SMILES = os.path.join(HERE, 'chebi_data', 'chebi_smiles.tsv')

os.makedirs(OUT_DIR, exist_ok=True)

# ---- 每步: (输出文件, [命令]) ----
STEPS = [
    ('output_parsed.tsv',       [sys.executable, S('parse_names.py'), RAW, P('output_parsed.tsv')]),
    ('output_go.tsv',           [sys.executable, S('fetch_go.py'), RAW, P('output_go.tsv')]),
    ('output_isoform.tsv',      [sys.executable, S('fetch_isoform.py'), RAW, P('output_isoform.tsv')]),
    ('output_rhea.tsv',         [sys.executable, S('fetch_rhea.py'), RAW, CHEBI_SMILES, P('output_rhea.tsv')]),
    ('output_references.tsv',   [sys.executable, S('fetch_references.py'), RAW, P('output_references.tsv')]),
    ('output_sequence_links.tsv', [sys.executable, S('fetch_sequence_links.py'), RAW, P('output_sequence_links.tsv')]),
    ('output_names_split.tsv',  [sys.executable, S('build_names_split.py'), P('output_parsed.tsv'), P('output_names_split.tsv')]),
    ('output_rhea_summary.tsv', [sys.executable, S('build_rhea_summary.py'), P('output_rhea.tsv'), RAW, P('output_rhea_summary.tsv')]),
    ('output_enzyme_merged.tsv',[sys.executable, S('build_enzyme_merged.py'), P('output_rhea.tsv'), RAW, P('output_enzyme_merged.tsv')]),
    ('output_terpene_only.tsv', [sys.executable, S('build_terpene_only.py'), P('output_rhea.tsv'), P('output_terpene_only.tsv')]),
    ('output_terpene_pairs.tsv',[sys.executable, S('build_terpene_pairs.py'), P('output_terpene_only.tsv'), P('output_terpene_pairs.tsv')]),
    ('output_terpene_compounds.tsv', [sys.executable, S('build_terpene_compounds.py'), P('output_terpene_only.tsv'), CHEBI_FULL, P('output_terpene_compounds.tsv')]),
    ('output_all_nodes.tsv',    [sys.executable, S('build_all_nodes.py'), P('output_terpene_only.tsv'), CHEBI_FULL, P('output_all_nodes.tsv')]),
]

# master 需要 5 个子表放在一个目录里 (固定文件名), 建 staging 目录
STAGING = P('child_tables')
CHILD_MAP = {
    'output_go.tsv': 'uniprotkb_go.tsv',
    'output_isoform.tsv': 'uniprotkb_isoform_sequences.tsv',
    'output_references.tsv': 'uniprotkb_references.tsv',
    'output_rhea.tsv': 'uniprotkb_rhea.tsv',
    'output_sequence_links.tsv': 'uniprotkb_sequence_links.tsv',
}
MASTER_OUT = P('output_master.tsv')


def run_step(name, out, cmd):
    if os.path.exists(out) and not FORCE:
        print(f'[skip] {name} (exists)')
        return
    print(f'\n===== {name} =====')
    subprocess.run(cmd, check=True)


print(f'输出目录: {OUT_DIR}')
print(f'统一表 RAW: {RAW}')
print(f'ChEBI: {CHEBI_FULL}')

for out, cmd in STEPS:
    run_step(os.path.basename(out), out, cmd)

# master: 先组装 staging 子表目录
print('\n===== master (stage child tables) =====')
os.makedirs(STAGING, exist_ok=True)
for src, dst in CHILD_MAP.items():
    src_path = P(src)
    if os.path.exists(src_path):
        import shutil
        shutil.copy(src_path, os.path.join(STAGING, dst))
        print(f'  staged {src} -> {STAGING}/{dst}')
    else:
        print(f'  WARN missing {src}')

if os.path.exists(MASTER_OUT) and not FORCE:
    print('[skip] output_master.tsv (exists)')
else:
    print('===== rebuild_master =====')
    subprocess.run([sys.executable, S('rebuild_master.py'), STAGING, RAW,
                    P('output_parsed.tsv'), MASTER_OUT], check=True)

print('\n完成! 最终表:')
for out, _ in STEPS:
    if os.path.exists(out):
        print(f'  {out}')
if os.path.exists(MASTER_OUT):
    print(f'  {MASTER_OUT}')
