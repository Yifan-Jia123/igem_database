import csv
import re
import requests
import time

INPUT = 'uniprotkb_TERPENE_AND_reviewed_true_2026_07_12.tsv'
OUTPUT = 'uniprotkb_rhea_expanded.tsv'

SPARQL_URL = 'https://sparql.rhea-db.org/sparql'

# ---- Step 1: collect all unique Rhea -> Entry mappings ----
entry_rhea_map = []  # (entry, rhea_id)
all_rhea = set()

with open(INPUT, 'r', encoding='utf-8') as f:
    for row in csv.DictReader(f, delimiter='\t'):
        entry = row['Entry']
        for rid in row['Rhea ID'].split():
            rid = rid.strip()
            if rid:
                entry_rhea_map.append((entry, rid))
                all_rhea.add(rid)

print(f'Total enzyme-rhea pairs: {len(entry_rhea_map)}')
print(f'Unique Rhea IDs: {len(all_rhea)}')

# ---- Step 2: batch query Rhea SPARQL for equations and ChEBI ----
rhea_info = {}  # rhea_id -> (equation, [chebi_ids])

BATCH_SIZE = 50
rhea_list = sorted(all_rhea)

for batch_start in range(0, len(rhea_list), BATCH_SIZE):
    batch = rhea_list[batch_start:batch_start + BATCH_SIZE]

    # Build VALUES clause (no parens for single variable)
    values = ' '.join(f'rhea:{rid.split(":")[1]}' for rid in batch)

    query = f'''
    PREFIX rhea: <http://rdf.rhea-db.org/>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

    SELECT ?reaction ?equation ?chebi WHERE {{
      VALUES ?reaction {{ {values} }}

      # Get equation
      ?reaction rdfs:label ?equation .

      # Get ChEBI via side -> participant -> compound
      ?reaction <http://rdf.rhea-db.org/side> ?side .
      ?side <http://rdf.rhea-db.org/contains> ?participant .
      ?participant <http://rdf.rhea-db.org/compound> ?compound .
      ?compound <http://rdf.rhea-db.org/accession> ?chebi .
    }}
    '''

    try:
        r = requests.get(SPARQL_URL,
                         params={'query': query, 'format': 'json'},
                         headers={'User-Agent': 'Mozilla/5.0'},
                         timeout=60)
        if r.status_code == 200:
            data = r.json()
            for binding in data['results']['bindings']:
                rid_uri = binding['reaction']['value']
                rid_num = rid_uri.rstrip('/').split('/')[-1]
                rid_full = f'RHEA:{rid_num}'
                eq = binding['equation']['value']
                chebi = binding['chebi']['value']

                if rid_full not in rhea_info:
                    rhea_info[rid_full] = [eq, set()]
                rhea_info[rid_full][1].add(chebi)
        else:
            print(f'  SPARQL error for batch {batch_start}: HTTP {r.status_code}')
    except Exception as e:
        print(f'  Error for batch {batch_start}: {e}')

    print(f'  Progress: {min(batch_start + BATCH_SIZE, len(rhea_list))}/{len(rhea_list)} ({len(rhea_info)} reactions resolved)')
    time.sleep(0.3)

# Convert sets to sorted strings
for rid in rhea_info:
    rhea_info[rid][1] = '; '.join(sorted(rhea_info[rid][1]))

# ---- Step 3: write output ----
with open(OUTPUT, 'w', encoding='utf-8', newline='') as f:
    w = csv.writer(f, delimiter='\t')
    w.writerow(['Entry', 'Rhea ID', 'Equation', 'ChEBI IDs'])
    for entry, rid in entry_rhea_map:
        info = rhea_info.get(rid, ['?', '?'])
        w.writerow([entry, rid, info[0], info[1]])

print(f'\nDone! Written to {OUTPUT}')
print(f'Resolved reactions: {len(rhea_info)} / {len(all_rhea)}')

# Sample
print('\nSample rows:')
with open(OUTPUT, 'r', encoding='utf-8') as f:
    for i, line in enumerate(f):
        if i < 8:
            print(line.rstrip())
