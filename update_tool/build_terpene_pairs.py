"""
Generate uniprotkb_terpene_pairs.tsv (one row per substrate->product pair, listing all enzymes that catalyze it).
Format: Substrate, Substrate ChEBI, Product, Product ChEBI,
        Enzyme_1..N, Rhea ID_1..N, Direction_1..N

Source: uniprotkb_terpene_only.tsv, grouped by (Substrate ChEBI, Product ChEBI).
  - de-dup enzymes within a group: for multiple reactions of the same enzyme catalyzing the same pair, Rhea IDs are joined in ascending order with '; ', and Direction takes that enzyme's first reaction's value
  - enzyme order within a group = ascending string order of Entry
  - group order = ascending (Substrate ChEBI string, Product ChEBI string) (same as the old table)
  - Substrate/Product name: strip the leading stoichiometric prefix 'N ' (e.g. '3 isopentenyl diphosphate' -> 'isopentenyl diphosphate'),
    stereo-descriptor parentheses are kept ((2E)-farnesyl ...)
"""
import csv
import re
import sys
from collections import OrderedDict

# Usage: python build_terpene_pairs.py [terpene_only] [output]
INPUT = sys.argv[1] if len(sys.argv) > 1 else 'output_terpene_only.tsv'
OUTPUT = sys.argv[2] if len(sys.argv) > 2 else 'output_terpene_pairs.tsv'


def rhea_key(rid):
    return int(rid.split(':')[1])


def strip_stoich(name):
    """Strip the leading stoichiometric prefix 'N ' (stereo-descriptor parentheses kept)."""
    return re.sub(r'^\d+ ', '', name)


# ---- single pass: (sub, prod) -> {entry: [(rid, direction), ...]}, recording the first name ----
pairs = OrderedDict()  # key -> {'sub': name, 'prod': name, 'enzymes': {entry: [(rid,dir)]}}
with open(INPUT, 'r', encoding='utf-8') as f:
    for row in csv.DictReader(f, delimiter='\t'):
        key = (row['Substrate ChEBI'], row['Product ChEBI'])
        if key not in pairs:
            pairs[key] = {'sub': row['Substrate'], 'prod': row['Product'],
                          'enzymes': {}}
        pairs[key]['enzymes'].setdefault(row['Entry'], []).append(
            (row['Rhea ID'], row['Direction']))

max_n = max((len(v['enzymes']) for v in pairs.values()), default=0)
print(f'Unique substrate->product pairs: {len(pairs)}, max enzymes per pair: {max_n}')

# ---- assemble ----
fields = ['Substrate', 'Substrate ChEBI', 'Product', 'Product ChEBI']
for i in range(1, max_n + 1):
    fields += [f'Enzyme_{i}', f'Rhea ID_{i}', f'Direction_{i}']

out_rows = []
for (sub_chebi, prod_chebi) in sorted(pairs.keys()):  # ascending group strings
    meta = pairs[(sub_chebi, prod_chebi)]
    enzymes = sorted(meta['enzymes'].keys())  # ascending Entry strings within a group
    row = OrderedDict()
    row['Substrate'] = strip_stoich(meta['sub'])
    row['Substrate ChEBI'] = sub_chebi
    row['Product'] = strip_stoich(meta['prod'])
    row['Product ChEBI'] = prod_chebi
    for i in range(1, max_n + 1):
        if i <= len(enzymes):
            entry = enzymes[i - 1]
            rxns = meta['enzymes'][entry]
            row[f'Enzyme_{i}'] = entry
            row[f'Rhea ID_{i}'] = '; '.join(sorted({r for r, _ in rxns}, key=rhea_key))
            # Direction: 'not specified' only when all of this enzyme's reactions are 'not specified', else '' (same as the old table)
            row[f'Direction_{i}'] = ('not specified' if all(d == 'not specified' for _, d in rxns) else '')
        else:
            for c in ['Enzyme', 'Rhea ID', 'Direction']:
                row[f'{c}_{i}'] = ''
    out_rows.append(row)

with open(OUTPUT, 'w', encoding='utf-8', newline='') as f:
    w = csv.DictWriter(f, delimiter='\t', fieldnames=fields)
    w.writeheader()
    w.writerows(out_rows)

print(f'Done! {len(out_rows)} rows, {len(fields)} cols -> {OUTPUT}')
