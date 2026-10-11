"""
Generate uniprotkb_terpene_only.tsv (one row per reaction: substrate/product each take the first main compound).
Format: Entry, Rhea ID, Direction, Substrate, Substrate ChEBI, Product, Product ChEBI

Construction logic (verified 0 diff against the old table):
  - substrate/product ChEBI : the rhea child's 'ChEBI IDs (equation order)' column (direction-aware substrate|product),
                      take the first non-CHEBI:33019 (diphosphate) compound on each side
  - substrate/product name : take segments from the direction-aware side of the Equation text, match the segment by clean name (Rhea rhea:name),
                      keeping the stoichiometric prefix and compartment suffix (e.g. '3 isopentenyl diphosphate', 'abscisate(out)')
                      for right-to-left, the substrate comes from the equation's right side and the product from the left
  - Direction column : old-table format -- keep only 'not specified', the rest blank
Row order = rhea child row order.
"""
import csv
import re
import sys
import time

import requests

# Usage: python build_terpene_only.py [rhea child] [output]
RHEA_INPUT = sys.argv[1] if len(sys.argv) > 1 else '../for_enzyme_detail/child_tables/uniprotkb_rhea.tsv'
OUTPUT = sys.argv[2] if len(sys.argv) > 2 else 'output_terpene_only.tsv'

SPARQL_URL = 'https://sparql.rhea-db.org/sparql'
SPARQL_BATCH = 100
SPARQL_DELAY = 0.3
MAX_ATTEMPTS = 8
BACKOFF = [2, 5, 10, 20, 30, 30, 30, 30]

# Cofactor exclusion: diphosphate (the only compound observed skipped in the old table)
EXCLUDE_COFACTOR = {'CHEBI:33019'}


def clean_name(seg):
    """Equation segment -> clean compound name: strip the stoichiometric prefix '3 ', strip the trailing compartment '(out)'."""
    seg = re.sub(r'^\d+ ', '', seg)
    seg = re.sub(r'\([^()]*\)$', '', seg).strip()
    return seg


def side_first(comps):
    """Return the first non-cofactor compound on a side."""
    for c in comps:
        if c not in EXCLUDE_COFACTOR:
            return c
    return comps[0] if comps else ''


# ---- Step 1: parse rhea child ----
print('Step 1: parsing rhea child...')
rows = []          # (entry, rhea_id, direction, sub_chebi, prod_chebi, sub_segs, prod_segs)
all_chebi = set()
with open(RHEA_INPUT, 'r', encoding='utf-8') as f:
    for row in csv.DictReader(f, delimiter='\t'):
        direction = row['Direction']
        out_dir = 'not specified' if direction == 'not specified' else ''

        left, _, right = row['ChEBI IDs (equation order)'].partition(' | ')
        sub_comps = [c.strip() for c in left.split(';') if c.strip()]
        prod_comps = [c.strip() for c in right.split(';') if c.strip()]
        sub_chebi = side_first(sub_comps)
        prod_chebi = side_first(prod_comps)
        all_chebi.update(c for c in (sub_chebi, prod_chebi) if c)

        # Equation segment: the matching direction-aware side (right-to-left => substrate = equation right, product = equation left)
        eq_left, _, eq_right = row['Equation'].partition(' = ')
        ls = [s.strip() for s in eq_left.split(' + ')]
        rs = [s.strip() for s in eq_right.split(' + ')]
        if direction == 'right-to-left':
            sub_segs, prod_segs = rs, ls
        else:
            sub_segs, prod_segs = ls, rs

        rows.append((row['Entry'], row['Rhea ID'], out_dir,
                     sub_chebi, prod_chebi, sub_segs, prod_segs,
                     (row.get('Source') or '').strip()))

print(f'  Reactions: {len(rows)}, unique compounds: {len(all_chebi)}')


# ---- Step 2: Rhea SPARQL fetch of rhea:name (Rhea short name, same as fetch_rhea.py) ----
def batch_query_names(ids):
    quoted = ', '.join(f'"{i}"' for i in ids)
    query = f'''
    PREFIX rhea: <http://rdf.rhea-db.org/>
    SELECT ?acc ?name WHERE {{
      ?compound rhea:accession ?acc ;
                rhea:name ?name .
      FILTER(?acc IN ({quoted}))
    }}
    '''
    r = requests.get(SPARQL_URL,
                     params={'query': query, 'format': 'json'},
                     headers={'User-Agent': 'Mozilla/5.0'},
                     timeout=60)
    r.raise_for_status()
    out = {}
    for b in r.json()['results']['bindings']:
        acc = b['acc']['value']
        if acc not in out:
            out[acc] = b['name']['value']
    return out


print('Step 2: fetching names from Rhea SPARQL...')
name_map = {}
chebi_list = sorted(all_chebi)
for i in range(0, len(chebi_list), SPARQL_BATCH):
    batch = chebi_list[i:i + SPARQL_BATCH]
    for attempt in range(MAX_ATTEMPTS):
        try:
            name_map.update(batch_query_names(batch))
            break
        except Exception as e:
            if attempt < MAX_ATTEMPTS - 1:
                time.sleep(BACKOFF[attempt])
            else:
                print(f'  Batch {i} FAILED: {e}')
    print(f'  Progress: {min(i + SPARQL_BATCH, len(chebi_list))}/{len(chebi_list)}')
    if i + SPARQL_BATCH < len(chebi_list):
        time.sleep(SPARQL_DELAY)

missing = all_chebi - set(name_map)
print(f'  Compounds with names: {len(name_map)}, missing: {len(missing)}')
for m in list(missing)[:10]:
    print(f'    MISSING NAME: {m}')


def display_name(segments, rhea_name):
    """Match an equation segment whose clean name ==/contains the Rhea short name; return the segment as-is (keeping stoichiometry/compartment)."""
    if not rhea_name:
        return ''
    for s in segments:
        cn = clean_name(s)
        if cn == rhea_name or rhea_name in cn or cn in rhea_name:
            return s
    return ''


# ---- Step 3: assemble output ----
print('Step 3: writing output...')
fields = ['Entry', 'Rhea ID', 'Direction', 'Substrate', 'Substrate ChEBI',
          'Product', 'Product ChEBI', 'Source']
# Rows with both ChEBI sides empty must be dropped before writing.
# Cause: when a Step 2 SPARQL batch fails, that reaction's ChEBI cannot be resolved, so sub_chebi
# and prod_chebi are both '' -- all such rows fall into the same key ('', ''), and downstream
# build_terpene_pairs collapses them into **one row**, then stuffs tens of thousands of enzymes into
# that row's Enzyme_1..N, producing one malformed record -- neither a valid pathway nor something
# that fits the downstream column width. Better one row too few than one row of junk.
dropped_both, dropped_one = 0, 0
with open(OUTPUT, 'w', encoding='utf-8', newline='') as f:
    w = csv.writer(f, delimiter='\t')
    w.writerow(fields)
    for entry, rid, direction, sub_chebi, prod_chebi, sub_segs, prod_segs, src in rows:
        if not sub_chebi and not prod_chebi:
            dropped_both += 1
            continue
        if not sub_chebi or not prod_chebi:
            # One-side-missing rows are still written (the other side's info is valid, and their
            # pairs key (sub, prod) differs from fully-populated rows), but they are counted and
            # reported -- abnormal growth means SPARQL is losing batches.
            dropped_one += 1
        w.writerow([entry, rid, direction,
                    display_name(sub_segs, name_map.get(sub_chebi, '')), sub_chebi,
                    display_name(prod_segs, name_map.get(prod_chebi, '')), prod_chebi,
                    src])

print(f'Done! {len(rows) - dropped_both} rows -> {OUTPUT}')
if dropped_both:
    print(f'  丢弃双侧 ChEBI 全空的行: {dropped_both} (SPARQL 未解析出化合物的反应)')
if dropped_one:
    print(f'  单侧 ChEBI 缺失(仍写出): {dropped_one}')
