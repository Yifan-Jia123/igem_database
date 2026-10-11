"""
Generate uniprotkb_rhea_summary.tsv (one row per reaction + Organism/Protein name).
Format: Entry, Organism, Protein name, Rhea ID, EC number, Equation, Direction

Source:
  - Rhea ID / EC number / Equation / Direction : rhea child table (uniprotkb_rhea.tsv)
  - Organism / Protein name                    : raw UniProt TSV (split at the first '(' of Protein names,
                                               trailing comma removed; 0 diff vs the old table on comparison)
Row order = rhea child row order.
"""
import csv
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from protein_name import split_recommended_name  # noqa: E402

# Usage: python build_rhea_summary.py [rhea child] [raw TSV] [output]
RHEA_INPUT = sys.argv[1] if len(sys.argv) > 1 else '../for_enzyme_detail/child_tables/uniprotkb_rhea.tsv'
RAW_INPUT = sys.argv[2] if len(sys.argv) > 2 else '../uniprotkb_terpene_AND_reviewed_true_2026_07_10.tsv'
OUTPUT = sys.argv[3] if len(sys.argv) > 3 else 'output_rhea_summary.tsv'

# The split logic is extracted to protein_name.py, shared with parse_names / build_enzyme_merged.
# What was inlined here used to be "split at the first '('", which turned names starting with (+)-/(E,E)-
# into an empty string (measured on TrEMBL slices: 1.9% hit), and disagreed with parse_names' EC-first behavior.
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

# ---- output the rhea child table row by row ----
rows = []
with open(RHEA_INPUT, 'r', encoding='utf-8') as f:
    for row in csv.DictReader(f, delimiter='\t'):
        e = row['Entry']
        it = info.get(e, {'org': '', 'pn': '', 'src': ''})
        rows.append({
            'Entry': e,
            'Organism': it['org'],
            'Protein name': it['pn'],
            'Rhea ID': row['Rhea ID'],
            'EC number': row['EC Number'],
            'Equation': row['Equation'],
            'Direction': row['Direction'],
            'Source': it['src'],
        })

# 'Source' goes in the last column: the ETL side reads everything by column name, so an appended column does not break existing reads.
fields = ['Entry', 'Organism', 'Protein name', 'Rhea ID', 'EC number', 'Equation', 'Direction',
          'Source']

with open(OUTPUT, 'w', encoding='utf-8', newline='') as f:
    w = csv.DictWriter(f, delimiter='\t', fieldnames=fields)
    w.writeheader()
    w.writerows(rows)

print(f'Done! {len(rows)} rows -> {OUTPUT}')
