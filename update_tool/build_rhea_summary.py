"""
Generate uniprotkb_rhea_summary.tsv (每反应一行 + Organism/Protein name).
Format: Entry, Organism, Protein name, Rhea ID, EC number, Equation, Direction

Source:
  - Rhea ID / EC number / Equation / Direction : rhea child table (uniprotkb_rhea.tsv)
  - Organism / Protein name                    : 原始 UniProt TSV (Protein names 首个 '(' 前切分，
                                               去掉尾随逗号；与旧表比对 0 差异)
行序 = rhea child 行序。
"""
import csv
import re
import sys

# 用法: python build_rhea_summary.py [rhea子表] [原始TSV] [输出]
RHEA_INPUT = sys.argv[1] if len(sys.argv) > 1 else '../for_enzyme_detail/child_tables/uniprotkb_rhea.tsv'
RAW_INPUT = sys.argv[2] if len(sys.argv) > 2 else '../uniprotkb_terpene_AND_reviewed_true_2026_07_10.tsv'
OUTPUT = sys.argv[3] if len(sys.argv) > 3 else 'output_rhea_summary.tsv'


def clean_protein_name(raw):
    """Protein names 在第一个 '(' 前切分, 去尾随逗号与空白。"""
    return re.split(r'\s*\(', raw, maxsplit=1)[0].strip().rstrip(',').rstrip()


# ---- 原始 TSV: Entry -> (Organism, 干净蛋白名) ----
info = {}
with open(RAW_INPUT, 'r', encoding='utf-8') as f:
    for row in csv.DictReader(f, delimiter='\t'):
        info[row['Entry']] = {
            'org': row.get('Organism', ''),
            'pn': clean_protein_name(row.get('Protein names', '')),
        }

# ---- rhea 子表逐行输出 ----
rows = []
with open(RHEA_INPUT, 'r', encoding='utf-8') as f:
    for row in csv.DictReader(f, delimiter='\t'):
        e = row['Entry']
        it = info.get(e, {'org': '', 'pn': ''})
        rows.append({
            'Entry': e,
            'Organism': it['org'],
            'Protein name': it['pn'],
            'Rhea ID': row['Rhea ID'],
            'EC number': row['EC Number'],
            'Equation': row['Equation'],
            'Direction': row['Direction'],
        })

fields = ['Entry', 'Organism', 'Protein name', 'Rhea ID', 'EC number', 'Equation', 'Direction']

with open(OUTPUT, 'w', encoding='utf-8', newline='') as f:
    w = csv.DictWriter(f, delimiter='\t', fieldnames=fields)
    w.writeheader()
    w.writerows(rows)

print(f'Done! {len(rows)} rows -> {OUTPUT}')
