"""
Fetch InChI Keys for all compounds in all_nodes.tsv using PubChem PUG REST API.
Also fetches InChI and SMILES for completeness.
"""
import csv
import requests
import time
import json
import os

INPUT = 'for_graph/all_nodes.tsv'
OUTPUT = 'for_graph/all_nodes.tsv'
CACHE_FILE = 'for_graph/_inchikey_cache.json'
BATCH_INTERVAL = 0.25  # PubChem rate limit: ~4-5/sec

# ---- Step 1: read compounds ----
compounds = []
with open(INPUT, 'r', encoding='utf-8') as f:
    for row in csv.DictReader(f, delimiter='\t'):
        compounds.append(row)

print(f'Total compounds: {len(compounds)}')

# ---- Step 2: load cache ----
cache = {}
if os.path.exists(CACHE_FILE):
    with open(CACHE_FILE, 'r', encoding='utf-8') as f:
        cache = json.load(f)
    print(f'Loaded {len(cache)} cached entries')

# ---- Step 3: fetch from PubChem ----
to_fetch = [c for c in compounds if c['ChEBI ID'] not in cache]

if to_fetch:
    print(f'Fetching {len(to_fetch)} compounds...')
    for idx, comp in enumerate(to_fetch):
        chebi_id = comp['ChEBI ID']
        try:
            # PubChem PUG REST: search by name (ChEBI ID as synonym)
            url = f'https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{chebi_id}/property/InChIKey,InChI,CanonicalSMILES/JSON'
            resp = requests.get(url, timeout=30)
            if resp.status_code == 200:
                data = resp.json()
                props = data.get('PropertyTable', {}).get('Properties', [])
                if props:
                    p = props[0]
                    cache[chebi_id] = {
                        'inchikey': p.get('InChIKey', ''),
                        'inchi': p.get('InChI', ''),
                        'smiles': p.get('CanonicalSMILES', ''),
                        'cid': str(p.get('CID', '')),
                    }
                else:
                    cache[chebi_id] = {'inchikey': '', 'inchi': '', 'smiles': '', 'cid': ''}
            else:
                print(f'  {chebi_id}: HTTP {resp.status_code}')
                cache[chebi_id] = {'inchikey': '', 'inchi': '', 'smiles': '', 'cid': ''}
        except Exception as ex:
            print(f'  {chebi_id}: {ex}')
            cache[chebi_id] = {'inchikey': '', 'inchi': '', 'smiles': '', 'cid': ''}

        if (idx + 1) % 50 == 0:
            found = sum(1 for v in cache.values() if v.get('inchikey'))
            print(f'  {idx+1}/{len(to_fetch)} ({found} with InChI Key)')
            with open(CACHE_FILE, 'w', encoding='utf-8') as f:
                json.dump(cache, f, ensure_ascii=False)

        time.sleep(BATCH_INTERVAL)

    # Final cache save
    with open(CACHE_FILE, 'w', encoding='utf-8') as f:
        json.dump(cache, f, ensure_ascii=False)
else:
    print('All cached, no fetch needed')

# ---- Step 4: stats ----
found = sum(1 for v in cache.values() if v.get('inchikey'))
missing = sum(1 for v in cache.values() if not v.get('inchikey'))
print(f'\nFound InChI Key: {found}, Missing: {missing}')

# ---- Step 5: write output ----
fields = ['ChEBI ID', 'Name', 'InChI Key', 'InChI', 'SMILES', 'PubChem CID']

with open(OUTPUT, 'w', encoding='utf-8', newline='') as f:
    writer = csv.DictWriter(f, fieldnames=fields, delimiter='\t', extrasaction='ignore')
    writer.writeheader()
    for comp in compounds:
        chebi_id = comp['ChEBI ID']
        info = cache.get(chebi_id, {})
        row = {
            'ChEBI ID': chebi_id,
            'Name': comp['Name'],
            'InChI Key': info.get('inchikey', ''),
            'InChI': info.get('inchi', ''),
            'SMILES': info.get('smiles', ''),
            'PubChem CID': info.get('cid', ''),
        }
        writer.writerow(row)

print(f'Written: {len(compounds)} rows, 6 cols -> {OUTPUT}')

# Sample
print('\nSample:')
print('-' * 80)
with open(OUTPUT, 'r', encoding='utf-8') as f:
    for i, row in enumerate(csv.DictReader(f, delimiter='\t')):
        if i >= 5: break
        cid = row['ChEBI ID']
        nm = row['Name']
        ik = row['InChI Key']
        sm = row.get('SMILES', 'N/A')
        print(f'{cid}: {nm}')
        print(f'  InChI Key: {ik}')
        print(f'  SMILES:    {sm[:80]}')

# Clean up
if os.path.exists(CACHE_FILE):
    os.remove(CACHE_FILE)
    print(f'\nCache removed.')
