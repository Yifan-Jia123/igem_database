"""
Generate uniprotkb_master.tsv (总表: 每个酶一行, 聚合所有子表横向铺开).
Format: Entry, UniProt Link, Recommended Name, Alternative Names, Gene Names,
        Rhea ID_1..N, ..., GO ID_1..N, ..., 核酸序列链接列, 参考文献列

数据来源 (全部参数化, 供新数据工作流复用):
  - 子表 go/isoform_sequences/references/rhea/sequence_links : 由各自 fetch/build 脚本生成
  - Canonical Sequence     : 统一原始 TSV 的 'Sequence' 列 (旧: 0712)
  - Sequence Length        : 统一原始 TSV 的 'Length' 列 (旧: 0716, 已验证 == len(Sequence))
  - Canonical Mass         : 统一原始 TSV 的 'Mass' 列 (旧: 0716)
  - 推荐名/替代名          : parse_names.py 产物 parsed.tsv (与 build_names_split 同逻辑)
  - Gene Names             : 统一原始 TSV 的 'Gene Names (primary)' 列
行序 = union(go, ref, rhea, seq) 的 Entry 字符串升序 (与旧表一致)。

注意: 旧版依赖中间表 output/uniprotkb_integrated.tsv 取 canonical 序列/长度;
      已验证 integrated.Canonical Sequence == 0712.Sequence、长度 == 0716.Length (0 差异),
      故直接改从原始 TSV 取, 去掉中间表依赖。
"""
import csv
import re
import sys
from collections import OrderedDict, defaultdict

# 用法:
#   新 4 参: python rebuild_master.py [child_tables目录] [统一原始TSV] [parsed.tsv] [输出]
#     统一表 = 19 列并集 (含 Sequence/Length/Mass/Gene Names), 三个 raw 来源都读它
#   旧 6 参: python rebuild_master.py [child_tables目录] [0710] [0712] [0716] [parsed.tsv] [输出]
CDIR = sys.argv[1] if len(sys.argv) > 1 else '../for_enzyme_detail/child_tables'
if len(sys.argv) >= 7:
    RAW_0710, RAW_0712, RAW_0716 = sys.argv[2], sys.argv[3], sys.argv[4]
    PARSED = sys.argv[5]
    OUTPUT = sys.argv[6]
else:
    RAW_UNIFIED = sys.argv[2] if len(sys.argv) > 2 else 'output_uniprot_unified.tsv'
    RAW_0710 = RAW_0712 = RAW_0716 = RAW_UNIFIED
    PARSED = sys.argv[3] if len(sys.argv) > 3 else 'output_parsed.tsv'
    OUTPUT = sys.argv[4] if len(sys.argv) > 4 else 'output_master.tsv'


def load_child(name):
    rows = defaultdict(list)
    with open(f'{CDIR}/{name}.tsv', 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f, delimiter='\t')
        for row in reader:
            rows[row['Entry']].append(row)
    return rows, reader.fieldnames


# Load go
go_by_entry, _ = load_child('uniprotkb_go')
go_by_entry = {k: [r for r in v if r.get('GO ID')] for k, v in go_by_entry.items()}

# Load isoforms
iso_by_entry, _ = load_child('uniprotkb_isoform_sequences')

# Load refs (full citation data)
ref_by_entry = {}
ref_fields_raw = []
with open(f'{CDIR}/uniprotkb_references.tsv', 'r', encoding='utf-8') as f:
    reader = csv.DictReader(f, delimiter='\t')
    ref_fields_raw = reader.fieldnames
    for row in reader:
        ref_by_entry[row['Entry']] = row

# Load rhea
rhea_by_entry, _ = load_child('uniprotkb_rhea')

# Load sequence links (split format)
seq_by_entry = {}
seq_fields_raw = []
with open(f'{CDIR}/uniprotkb_sequence_links.tsv', 'r', encoding='utf-8') as f:
    reader = csv.DictReader(f, delimiter='\t')
    seq_fields_raw = reader.fieldnames
    for row in reader:
        seq_by_entry[row['Entry']] = row

# Load canonical sequence from 0712, length+mass from 0716
canonical = {}
with open(RAW_0712, 'r', encoding='utf-8') as f:
    for row in csv.DictReader(f, delimiter='\t'):
        canonical[row['Entry']] = (row.get('Sequence', ''), '')
with open(RAW_0716, 'r', encoding='utf-8') as f:
    for row in csv.DictReader(f, delimiter='\t'):
        e = row['Entry']
        if e in canonical:
            seq, _ = canonical[e]
            canonical[e] = (seq, row.get('Length', ''))

# Load parsed names from parse_names.py output (corrected split)
protein_names = {}
with open(PARSED, 'r', encoding='utf-8') as f:
    for row in csv.DictReader(f, delimiter='\t'):
        e = row['Entry']
        rec_name = row.get('Recommended name', '')
        alt_names_str = row.get('Alternative names', '')
        ec_str = row.get('EC numbers', '')

        alt_names_list = [n.strip() for n in alt_names_str.split(';') if n.strip()]
        ecs = [ec.strip().replace('EC ', '') for ec in ec_str.split(';') if ec.strip()]

        # 推荐名 + 第一个 EC
        if ecs:
            rec_full = f'{rec_name} (EC {ecs[0]})'
        else:
            rec_full = rec_name

        # 替代名按顺序配剩余的 EC
        alt_parts = []
        for i, alt in enumerate(alt_names_list):
            ec_idx = min(i + 1, len(ecs) - 1) if len(ecs) > 1 else 0
            if ecs:
                alt_parts.append(f'{alt} (EC {ecs[ec_idx]})')
            else:
                alt_parts.append(alt)

        protein_names[e] = (rec_full, '; '.join(alt_parts))

# Load gene names (primary) + mass from raw TSVs
gene_names = {}
with open(RAW_0710, 'r', encoding='utf-8') as f:
    for row in csv.DictReader(f, delimiter='\t'):
        gene_names[row['Entry']] = row.get('Gene Names (primary)', '')

uniprot_mass = {}
with open(RAW_0716, 'r', encoding='utf-8') as f:
    for row in csv.DictReader(f, delimiter='\t'):
        uniprot_mass[row['Entry']] = row.get('Mass', '')

# Max counts
max_rhea = max((len(v) for v in rhea_by_entry.values()), default=0)
max_go = max((len(v) for v in go_by_entry.values()), default=0)
max_iso = max((len(v) for v in iso_by_entry.values()), default=0)

max_insdc = max((int(re.search(r'_(\d+)$', f).group(1)) for f in seq_fields_raw if f.startswith('INSDC_Nuc_ID_')), default=0)
max_refseq = max((int(re.search(r'_(\d+)$', f).group(1)) for f in seq_fields_raw if f.startswith('RefSeq_Prot_ID_')), default=0)

all_entries = sorted(set(list(go_by_entry.keys()) + list(ref_by_entry.keys()) +
                          list(rhea_by_entry.keys()) + list(seq_by_entry.keys())))

seq_cols = [f for f in seq_fields_raw if f != 'Entry']

# Build fields: Entry > Names > Rhea > Protein Seq > Nucleic Seq > GO > Refs
fields = ['Entry', 'UniProt Link', 'Recommended Name', 'Alternative Names', 'Gene Names']

# Rhea
for i in range(1, max_rhea + 1):
    fields += [f'Rhea ID_{i}', f'Rhea Link_{i}', f'Equation_{i}',
               f'Direction_{i}', f'EC Number_{i}',
               f'Reaction SMILES_{i}', f'ChEBI IDs_{i}']

# Protein: canonical + isoforms
fields += ['Canonical Sequence', 'Sequence Length', 'Canonical Mass']
for i in range(1, max_iso + 1):
    fields += [f'Isoform ID_{i}', f'Isoform Length_{i}',
               f'Isoform Mass_{i}', f'Isoform Sequence_{i}']

# Nucleic seq links
fields += seq_cols

# GO
for i in range(1, max_go + 1):
    fields += [f'GO ID_{i}', f'GO Term_{i}', f'GO Link_{i}']

# References
ref_cols = [f for f in ref_fields_raw if f != 'Entry']
fields += ref_cols

# Build rows
rows = []
for entry in all_entries:
    row = OrderedDict()
    row['Entry'] = entry
    row['UniProt Link'] = f'https://www.uniprot.org/uniprotkb/{entry}'
    rec, alt = protein_names.get(entry, ('', ''))
    row['Recommended Name'] = rec
    row['Alternative Names'] = alt
    row['Gene Names'] = gene_names.get(entry, '')

    # Rhea
    rxns = rhea_by_entry.get(entry, [])
    for i in range(1, max_rhea + 1):
        if i <= len(rxns):
            r = rxns[i-1]
            row[f'Rhea ID_{i}'] = r['Rhea ID']
            row[f'Rhea Link_{i}'] = r['Rhea Link']
            row[f'Equation_{i}'] = r['Equation']
            row[f'Direction_{i}'] = r['Direction']
            row[f'EC Number_{i}'] = r['EC Number']
            row[f'Reaction SMILES_{i}'] = r['Reaction SMILES']
            row[f'ChEBI IDs_{i}'] = r.get('ChEBI IDs (equation order)', '')
        else:
            for c in ['Rhea ID','Rhea Link','Equation','Direction','EC Number','Reaction SMILES','ChEBI IDs']:
                row[f'{c}_{i}'] = ''

    # Protein: canonical + isoforms
    seq, slen = canonical.get(entry, ('', ''))
    row['Canonical Sequence'] = seq
    row['Sequence Length'] = slen
    row['Canonical Mass'] = uniprot_mass.get(entry, '')

    isos = iso_by_entry.get(entry, [])
    for i in range(1, max_iso + 1):
        if i <= len(isos):
            iso = isos[i-1]
            row[f'Isoform ID_{i}'] = iso['Isoform_ID']
            row[f'Isoform Length_{i}'] = iso['Isoform Length']
            row[f'Isoform Mass_{i}'] = iso['Isoform Mass']
            row[f'Isoform Sequence_{i}'] = iso['Sequence']
        else:
            for c in ['Isoform ID','Isoform Length','Isoform Mass','Isoform Sequence']:
                row[f'{c}_{i}'] = ''

    # Nucleic seq links
    sq = seq_by_entry.get(entry, {})
    for c in seq_cols:
        row[c] = sq.get(c, '')

    # GO
    gos = go_by_entry.get(entry, [])
    for i in range(1, max_go + 1):
        if i <= len(gos):
            g = gos[i-1]
            row[f'GO ID_{i}'] = g['GO ID']
            row[f'GO Term_{i}'] = g['GO Term']
            row[f'GO Link_{i}'] = g['GO Link']
        else:
            for c in ['GO ID','GO Term','GO Link']:
                row[f'{c}_{i}'] = ''

    # References
    ref_row = ref_by_entry.get(entry, {})
    for c in ref_cols:
        row[c] = ref_row.get(c, '')

    rows.append(row)

with open(OUTPUT, 'w', encoding='utf-8', newline='') as f:
    w = csv.DictWriter(f, delimiter='\t', fieldnames=fields, extrasaction='ignore')
    w.writeheader()
    w.writerows(rows)

has_seq = sum(1 for r in rows if r['Canonical Sequence'])
has_mass = sum(1 for r in rows if r['Canonical Mass'])
print(f'{len(rows)} enzymes, {len(fields)} columns -> {OUTPUT}')
print(f'With canonical sequence: {has_seq}, With canonical mass: {has_mass}')
