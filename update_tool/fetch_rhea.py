"""
Regenerate the rhea child table (uniprotkb_rhea.tsv).
Format: Entry, Rhea ID, Rhea Link, Equation, Direction, EC Number, Reaction SMILES, ChEBI IDs (equation order)

Data sources:
- Equation / Direction / EC / Rhea ID : parsed from 0712 raw TSV Catalytic activity column
- ChEBI equation-order (L|R)          : Rhea SPARQL sides (reaction -> side -> participant -> compound)
- Reaction SMILES                     : local chebi_data/chebi_smiles.tsv, direction-aware
                                        (substrates/products follow the Direction column, NOT the equation text)
"""
import csv
import re
import requests
import sys
import time

# Usage: python fetch_rhea.py [input raw TSV with Catalytic activity] [chebi_smiles.tsv] [output file]
INPUT = sys.argv[1] if len(sys.argv) > 1 else '../uniprotkb_TERPENE_AND_reviewed_true_2026_07_12.tsv'
CHEBI_SMILES_FILE = sys.argv[2] if len(sys.argv) > 2 else 'chebi_data/chebi_smiles.tsv'
OUTPUT = sys.argv[3] if len(sys.argv) > 3 else 'output_rhea.tsv'

SPARQL_URL = 'https://sparql.rhea-db.org/sparql'
SPARQL_BATCH = 50
SPARQL_DELAY = 0.3


# ---- Step 1: parse Catalytic activity from 0712 raw TSV ----
# Each CATALYTIC ACTIVITY unit: Reaction=...; Xref=Rhea:RHEA:xxxxx, ChEBI:...; EC=...; Evidence=...
#                          [PhysiologicalDirection=...; Xref=Rhea:RHEA:yyyyy; Evidence=...]
# One Reaction block = one child-table row. Direction is the block's immediately following
# PhysiologicalDirection (if any), else 'not specified'.
# Key invariant: one reaction in UniProt CC carries several Rhea IDs -- the RHEA:xxxxx on the
#                Reaction line is the generic form (undirected main reaction), the RHEA:yyyyy on
#                the PhysiologicalDirection line is the direction-specific form. We take only the
#                generic form (the block's first Xref=Rhea:), i.e. the Rhea ID column = generic ID.
def parse_catalytic_activity(cat):
    """Return list of (rhea_id, equation, direction, ec) per reaction block."""
    reactions = []
    for part in cat.split('Reaction=')[1:]:
        # Generic-form Rhea ID: the block's first Xref=Rhea: (the main reaction on the Reaction line)
        m = re.search(r'Xref=Rhea:RHEA:(\d+)', part)
        if not m:
            continue
        rid = f'RHEA:{m.group(1)}'

        # Equation: from Reaction= up to the first ';'
        eq = part.split(';')[0].strip()

        # EC: from EC= up to the next ';'
        ec = ''
        m_ec = re.search(r'EC=([^;]+)', part)
        if m_ec:
            ec = m_ec.group(1).strip()

        # Direction: the first PhysiologicalDirection immediately after this block
        direction = 'not specified'
        m_dir = re.search(r'PhysiologicalDirection=([a-z-]+)', part)
        if m_dir:
            direction = m_dir.group(1)

        reactions.append((rid, eq, direction, ec))

    return reactions

def normalize_ec(ec):
    """EC normalization: strip Rhea/UniProt n-level placeholder suffixes (e.g. 1.1.1.n4 -> 1.1.1.); '--' if missing."""
    if not ec:
        return '--'
    return re.sub(r'n\d+$', '', ec)


print('Step 1: parsing Catalytic activity from 0712 raw TSV...')
rows = []          # (entry, rhea_id, equation, direction, ec, source)
all_rhea = set()
with open(INPUT, 'r', encoding='utf-8') as f:
    for row in csv.DictReader(f, delimiter='\t'):
        entry = row['Entry']
        src = (row.get('Source') or '').strip()
        for rid, eq, direction, ec in parse_catalytic_activity(row.get('Catalytic activity', '')):
            rows.append((entry, rid, eq, direction, ec, src))
            all_rhea.add(rid)

print(f'  Reactions parsed: {len(rows)}  (unique Rhea IDs: {len(all_rhea)})')


# ---- Step 2: fetch ChEBI equation-order + EC via Rhea SPARQL ----
# Each reaction returns (side, name, chebi) per side + ec (the Rhea reaction's EC, a fallback when
# a 0712 block lacks an EC)
def batch_query_sparql(ids):
    values = ' '.join(f'rhea:{rid.split(":")[1]}' for rid in ids)
    query = f'''
    PREFIX rhea: <http://rdf.rhea-db.org/>
    SELECT ?reaction ?side ?name ?chebi ?ec WHERE {{
      VALUES ?reaction {{ {values} }}
      ?reaction rhea:side ?side .
      ?side rhea:contains ?participant .
      ?participant rhea:compound ?compound .
      ?compound rhea:accession ?chebi ;
                rhea:name ?name .
      OPTIONAL {{ ?reaction rhea:ec ?ec . }}
    }}
    '''
    r = requests.get(SPARQL_URL,
                     params={'query': query, 'format': 'json'},
                     headers={'User-Agent': 'Mozilla/5.0'},
                     timeout=60)
    r.raise_for_status()
    out = {}
    seen = {}
    for b in r.json()['results']['bindings']:
        rid = 'RHEA:' + b['reaction']['value'].rstrip('/').split('/')[-1]
        side = b['side']['value'].rstrip('/').split('/')[-1]  # e.g. 54512_L
        item = (side, b['name']['value'], b['chebi']['value'])
        # OPTIONAL ?ec duplicates each row's bindings; de-duplicate by (rid, side, name, chebi)
        seen.setdefault(rid, set())
        if item not in seen[rid]:
            seen[rid].add(item)
            out.setdefault(rid, {'items': [], 'ec': ''})
            out[rid]['items'].append(item)
        if 'ec' in b and not out.get(rid, {}).get('ec'):
            # rhea:ec values look like http://purl.uniprot.org/enzyme/4.2.3.228
            out.setdefault(rid, {'items': [], 'ec': ''})
            out[rid]['ec'] = b['ec']['value'].rstrip('/').split('/')[-1]
    return out


print('\nStep 2: fetching ChEBI order + EC from Rhea SPARQL...')
rhea_side = {}  # rhea_id -> {'L': [(name, chebi)...], 'R': [...], 'ec': str}
rhea_list = sorted(all_rhea)
for batch_start in range(0, len(rhea_list), SPARQL_BATCH):
    batch = rhea_list[batch_start:batch_start + SPARQL_BATCH]
    for attempt in range(3):
        try:
            result = batch_query_sparql(batch)
            for rid, info in result.items():
                sides = {'L': [], 'R': [], 'ec': info['ec']}
                for side, name, chebi in info['items']:
                    key = 'L' if side.endswith('_L') else ('R' if side.endswith('_R') else None)
                    if key:
                        sides[key].append((name, chebi))
                rhea_side[rid] = sides
            break
        except Exception as e:
            if attempt < 2:
                time.sleep(3 * (attempt + 1))
            else:
                print(f'  Batch {batch_start} FAILED: {e}')
    print(f'  Progress: {min(batch_start + SPARQL_BATCH, len(rhea_list))}/{len(rhea_list)}')
    if batch_start + SPARQL_BATCH < len(rhea_list):
        time.sleep(SPARQL_DELAY)


def get_sides(rid):
    """Return (left_list, right_list) of ChEBI IDs in SPARQL return order (order within a side preserved)."""
    sides = rhea_side.get(rid)
    if not sides:
        return [], []
    return [c for n, c in sides['L']], [c for n, c in sides['R']]


# ---- Step 3: load ChEBI -> SMILES ----
print('\nStep 3: loading ChEBI -> SMILES...')
chebi_smiles = {}
with open(CHEBI_SMILES_FILE, 'r', encoding='utf-8') as f:
    for line in f:
        if line.startswith('CHEBI\t'):
            continue
        parts = line.strip().split('\t', 1)
        if len(parts) == 2:
            chebi_smiles[parts[0]] = parts[1]
print(f'  Loaded {len(chebi_smiles)} ChEBI SMILES')


# ---- Step 4: assemble ChEBI order + reaction SMILES (direction-aware) ----
# The ChEBI order column = substrates;... | products;... (follows the Direction column, not the equation text).
# Order within a side = the Rhea SPARQL side return order (includes GENERIC macromolecule compounds).
print('Step 4: assembling ChEBI order + reaction SMILES...')
smiles_hit = 0
chebi_hit = 0
for i, (entry, rid, equation, direction, ec, src) in enumerate(rows):
    left_c, right_c = get_sides(rid)

    # SMILES substrates/products follow the Direction column
    if direction == 'right-to-left':
        sub_c, prod_c = right_c, left_c
    else:  # left-to-right / not specified
        sub_c, prod_c = left_c, right_c

    chebi_order = '; '.join(sub_c) + ' | ' + '; '.join(prod_c) if (sub_c or prod_c) else ''
    if chebi_order:
        chebi_hit += 1

    sub_list = [chebi_smiles.get(c, '') for c in sub_c]
    prod_list = [chebi_smiles.get(c, '') for c in prod_c]
    sub_str = '.'.join(s for s in sub_list if s)
    prod_str = '.'.join(s for s in prod_list if s)
    rxn_smiles = f'{sub_str}>>{prod_str}' if sub_str and prod_str else ''
    if rxn_smiles:
        smiles_hit += 1

    # EC: the 0712 block's EC= wins; if missing, use the Rhea reaction's EC; if still missing, '--';
    # strip the n suffix uniformly
    if not ec:
        ec = rhea_side.get(rid, {}).get('ec', '')
    ec = normalize_ec(ec)

    rid_num = rid.split(':')[1]
    rows[i] = (entry, rid, f'https://www.rhea-db.org/rhea/{rid_num}',
               equation, direction, ec, rxn_smiles, chebi_order, src)

print(f'  Rows with reaction SMILES: {smiles_hit}/{len(rows)}')
print(f'  Rows with ChEBI order: {chebi_hit}/{len(rows)}')


# ---- Step 5: write ----
print('\nStep 5: writing output...')
with open(OUTPUT, 'w', encoding='utf-8', newline='') as f:
    w = csv.writer(f, delimiter='\t')
    w.writerow(['Entry', 'Rhea ID', 'Rhea Link', 'Equation', 'Direction',
                'EC Number', 'Reaction SMILES', 'ChEBI IDs (equation order)', 'Source'])
    for entry, rid, link, eq, direction, ec, rxn, chebi, src in rows:
        w.writerow([entry, rid, link, eq, direction, ec, rxn, chebi, src])

print(f'Done! {len(rows)} rows -> {OUTPUT}')
