"""
Generate uniprotkb_enzyme_merged.tsv (one row per enzyme, multiple reactions spread across _1.._N).
Format: Entry, Organism, Protein name, Rhea ID_1..N, EC number_1..N, Equation_1..N, Direction_1..N

Source:
  - Rhea ID / EC number / Equation / Direction : rhea child table (grouped by entry, rhea row order preserved within a group)
  - Organism / Protein name                    : raw UniProt TSV (same split as build_rhea_summary.py)
Row order = ascending string order of Entry (as the old table did).
"""
import csv
import os
import re
import sys
from collections import OrderedDict, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from protein_name import split_recommended_name  # noqa: E402

# Usage: python build_enzyme_merged.py [rhea child] [raw TSV] [output]
RHEA_INPUT = sys.argv[1] if len(sys.argv) > 1 else '../for_enzyme_detail/child_tables/uniprotkb_rhea.tsv'
RAW_INPUT = sys.argv[2] if len(sys.argv) > 2 else '../uniprotkb_terpene_AND_reviewed_true_2026_07_10.tsv'
OUTPUT = sys.argv[3] if len(sys.argv) > 3 else 'output_enzyme_merged.tsv'

# The split logic is extracted to protein_name.py, shared with parse_names / build_rhea_summary.
clean_protein_name = split_recommended_name


# ---- raw TSV: Entry -> (Organism, clean protein name, source) ----
info = {}
with open(RAW_INPUT, 'r', encoding='utf-8') as f:
    for row in csv.DictReader(f, delimiter='\t'):
        info[row['Entry']] = {
            'org': row.get('Organism', ''),
            'pn': clean_protein_name(row.get('Protein names', '')),
            'src': (row.get('Source') or '').strip(),
        }

# ---- group rhea reactions by entry ----
rxns = defaultdict(list)  # entry -> [row, ...]
with open(RHEA_INPUT, 'r', encoding='utf-8') as f:
    for row in csv.DictReader(f, delimiter='\t'):
        rxns[row['Entry']].append(row)

max_n = max((len(v) for v in rxns.values()), default=0)
print(f'Entries with reactions: {len(rxns)}, max reactions: {max_n}')

# ---- assemble ----
fields = ['Entry', 'Organism', 'Protein name']
for i in range(1, max_n + 1):
    fields += [f'Rhea ID_{i}', f'EC number_{i}', f'Equation_{i}', f'Direction_{i}']
# 'Source' goes in the last column: the ETL side reads by column name, so an appended column does not break existing reads.
fields += ['Source']

rows = []
for entry in sorted(rxns.keys()):  # ascending string order
    it = info.get(entry, {'org': '', 'pn': '', 'src': ''})
    row = OrderedDict()
    row['Entry'] = entry
    row['Organism'] = it['org']
    row['Protein name'] = it['pn']
    row['Source'] = it['src']
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
