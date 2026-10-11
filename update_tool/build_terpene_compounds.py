"""
Generate uniprotkb_terpene_compounds.tsv (the base table for the compound card table).
Format: ChEBI ID, Name, SMILES, Molecular Mass, ChEBI URL

Construction logic:
  - compound set = all Substrate/Product ChEBI in terpene_only, de-duplicated, with GENERIC:* removed
  - each row = the whole row for the matching ChEBI ID in chebi_full.tsv (its 5 columns as-is)
  - order = ascending ChEBI ID string (same as the old table, verified 0 diff)
"""
import csv
import sys

# Usage: python build_terpene_compounds.py [terpene_only] [chebi_full] [output]
TERPENE_INPUT = sys.argv[1] if len(sys.argv) > 1 else '../for_graph/uniprotkb_terpene_only.tsv'
CHEBI_INPUT = sys.argv[2] if len(sys.argv) > 2 else 'chebi_data/chebi_full.tsv'
OUTPUT = sys.argv[3] if len(sys.argv) > 3 else 'output_terpene_compounds.tsv'

# ---- compound set ----
comps = set()
with open(TERPENE_INPUT, 'r', encoding='utf-8') as f:
    for r in csv.DictReader(f, delimiter='\t'):
        for c in (r['Substrate ChEBI'], r['Product ChEBI']):
            if c and not c.startswith('GENERIC'):
                comps.add(c)
print(f'Distinct non-GENERIC compounds: {len(comps)}')

# ---- filter chebi_full ----
rows = []
with open(CHEBI_INPUT, 'r', encoding='utf-8') as f:
    for r in csv.DictReader(f, delimiter='\t'):
        if r['ChEBI ID'] in comps:
            rows.append(r)
rows.sort(key=lambda r: r['ChEBI ID'])
print(f'Matched in chebi_full: {len(rows)}')

fields = ['ChEBI ID', 'Name', 'SMILES', 'Molecular Mass', 'ChEBI URL']
with open(OUTPUT, 'w', encoding='utf-8', newline='') as f:
    w = csv.DictWriter(f, delimiter='\t', fieldnames=fields)
    w.writeheader()
    w.writerows(rows)

print(f'Done! {len(rows)} rows -> {OUTPUT}')
