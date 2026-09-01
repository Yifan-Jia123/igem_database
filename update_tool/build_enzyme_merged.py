"""
Generate uniprotkb_enzyme_merged.tsv (每个酶一行, 多反应横向铺开 _1.._N).
Format: Entry, Organism, Protein name, Rhea ID_1..N, EC number_1..N, Equation_1..N, Direction_1..N

Source:
  - Rhea ID / EC number / Equation / Direction : rhea child table (按 entry 分组, 组内保持 rhea 行序)
  - Organism / Protein name                    : 原始 UniProt TSV (同 build_rhea_summary.py 的切分)
行序 = Entry 字符串升序 (旧表如此)。
"""
import csv
import re
import sys
from collections import OrderedDict, defaultdict

# 用法: python build_enzyme_merged.py [rhea子表] [原始TSV] [输出]
RHEA_INPUT = sys.argv[1] if len(sys.argv) > 1 else '../for_enzyme_detail/child_tables/uniprotkb_rhea.tsv'
RAW_INPUT = sys.argv[2] if len(sys.argv) > 2 else '../uniprotkb_terpene_AND_reviewed_true_2026_07_10.tsv'
OUTPUT = sys.argv[3] if len(sys.argv) > 3 else 'output_enzyme_merged.tsv'


def clean_protein_name(raw):
    return re.split(r'\s*\(', raw, maxsplit=1)[0].strip().rstrip(',').rstrip()


# ---- 原始 TSV: Entry -> (Organism, 干净蛋白名) ----
info = {}
with open(RAW_INPUT, 'r', encoding='utf-8') as f:
    for row in csv.DictReader(f, delimiter='\t'):
        info[row['Entry']] = {
            'org': row.get('Organism', ''),
            'pn': clean_protein_name(row.get('Protein names', '')),
        }

# ---- 按 entry 分组 rhea 反应 ----
rxns = defaultdict(list)  # entry -> [row, ...]
with open(RHEA_INPUT, 'r', encoding='utf-8') as f:
    for row in csv.DictReader(f, delimiter='\t'):
        rxns[row['Entry']].append(row)

max_n = max((len(v) for v in rxns.values()), default=0)
print(f'Entries with reactions: {len(rxns)}, max reactions: {max_n}')

# ---- 组装 ----
fields = ['Entry', 'Organism', 'Protein name']
for i in range(1, max_n + 1):
    fields += [f'Rhea ID_{i}', f'EC number_{i}', f'Equation_{i}', f'Direction_{i}']

rows = []
for entry in sorted(rxns.keys()):  # 字符串升序
    it = info.get(entry, {'org': '', 'pn': ''})
    row = OrderedDict()
    row['Entry'] = entry
    row['Organism'] = it['org']
    row['Protein name'] = it['pn']
    for i in range(1, max_n + 1):
        if i <= len(rxns[entry]):
            r = rxns[entry][i - 1]
            row[f'Rhea ID_{i}'] = r['Rhea ID']
            row[f'EC number_{i}'] = r['EC Number']
            row[f'Equation_{i}'] = r['Equation']
            row[f'Direction_{i}'] = r['Direction']
        else:
            for c in ['Rhea ID', 'EC number', 'Equation', 'Direction']:
                row[f'{c}_{i}'] = ''
    rows.append(row)

with open(OUTPUT, 'w', encoding='utf-8', newline='') as f:
    w = csv.DictWriter(f, delimiter='\t', fieldnames=fields)
    w.writeheader()
    w.writerows(rows)

print(f'Done! {len(rows)} rows, {len(fields)} cols -> {OUTPUT}')
