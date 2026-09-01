import csv, re, sys

CHEBI_SMILES_FILE = 'chebi_data/chebi_smiles.tsv'
INPUT = 'uniprotkb_rhea_expanded.tsv'
INPUT_ORIG = 'uniprotkb_TERPENE_AND_reviewed_true_2026_07_12.tsv'

# ---- Load local ChEBI -> SMILES ----
print('Loading ChEBI -> SMILES...', flush=True)
chebi_smiles = {}
with open(CHEBI_SMILES_FILE, 'r', encoding='utf-8') as f:
    for line in f:
        if line.startswith('CHEBI\t'): continue
        parts = line.strip().split('\t', 1)
        if len(parts) == 2:
            chebi_smiles[parts[0]] = parts[1]
print(f'  Loaded: {len(chebi_smiles)} entries', flush=True)

# ---- Load expanded Rhea file ----
rows = []
all_chebi = set()
with open(INPUT, 'r', encoding='utf-8') as f:
    for row in csv.DictReader(f, delimiter='\t'):
        rows.append(row)
        for c in row['ChEBI IDs'].split('; '):
            if c.strip(): all_chebi.add(c.strip())

ok = sum(1 for c in all_chebi if c in chebi_smiles)
missing = sum(1 for c in all_chebi if c not in chebi_smiles)
print(f'ChEBI in data: {len(all_chebi)}  (with SMILES: {ok}, missing: {missing})', flush=True)
if missing:
    print(f'  Missing examples: {[c for c in all_chebi if c not in chebi_smiles][:5]}', flush=True)

# ---- Parse original CC for ChEBI ordering ----
print('Parsing Catalytic activity for ChEBI ordering...', flush=True)
entry_rhea_order = {}

with open(INPUT_ORIG, 'r', encoding='utf-8') as f:
    for row in csv.DictReader(f, delimiter='\t'):
        entry = row['Entry']
        cat = row.get('Catalytic activity', '')
        for part in cat.split('Reaction=')[1:]:
            eq = part.split(';')[0].strip()
            m = re.search(r'RHEA:(\d+)', part)
            if not m or ' = ' not in eq:
                continue
            rid = f'RHEA:{m.group(1)}'
            chebi_part = part.split('Xref=Rhea:')[1] if 'Xref=Rhea:' in part else ''
            chebis = [f'CHEBI:{c}' for c in re.findall(r'CHEBI:(\d+)', chebi_part)]
            left, right = eq.split(' = ', 1)
            n_left = len(left.split(' + '))
            entry_rhea_order[(entry, rid)] = (chebis[:n_left], chebis[n_left:])

# ---- Build reaction SMILES ----
print('Building reaction SMILES...', flush=True)
fields = list(rows[0].keys()) + ['Reaction SMILES', 'Substrate SMILES', 'Product SMILES']

for row in rows:
    entry, rid = row['Entry'], row['Rhea ID']
    key = (entry, rid)
    sub_list, prod_list = [], []

    if key in entry_rhea_order:
        left_c, right_c = entry_rhea_order[key]
        if row['Direction'] == 'right-to-left':
            sub_list = [chebi_smiles.get(c, '') for c in right_c]
            prod_list = [chebi_smiles.get(c, '') for c in left_c]
        else:
            sub_list = [chebi_smiles.get(c, '') for c in left_c]
            prod_list = [chebi_smiles.get(c, '') for c in right_c]

    sub_str = '.'.join(s for s in sub_list if s)
    prod_str = '.'.join(s for s in prod_list if s)
    row['Substrate SMILES'] = sub_str
    row['Product SMILES'] = prod_str
    row['Reaction SMILES'] = f'{sub_str}>>{prod_str}' if sub_str and prod_str else ''

with open(INPUT, 'w', encoding='utf-8', newline='') as f:
    w = csv.DictWriter(f, fieldnames=fields, delimiter='\t', extrasaction='ignore')
    w.writeheader()
    w.writerows(rows)

with_rxn = sum(1 for r in rows if r['Reaction SMILES'])
print(f'\nDone! Rows with reaction SMILES: {with_rxn}/{len(rows)}', flush=True)

for r in rows[:3]:
    print(f"\n{r['Entry']} {r['Rhea ID']}: {r['Direction']}")
    print(f"  Sub: {r['Substrate SMILES'][:150]}")
    print(f"  Prod: {r['Product SMILES'][:150]}")
