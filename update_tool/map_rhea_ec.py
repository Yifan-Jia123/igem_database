import csv
import re
import json
import urllib.request
import time

INPUT = 'uniprotkb_terpene_parsed.tsv'
OUTPUT = 'uniprotkb_terpene_with_rhea_ec_map.tsv'

# Cache for Rhea API results
rhea_cache = {}

def lookup_rhea(rhea_id):
    """Query Rhea API for a given Rhea ID, return its EC number(s)."""
    if rhea_id in rhea_cache:
        return rhea_cache[rhea_id]

    numeric_id = rhea_id.replace('RHEA:', '')
    url = f'https://www.rhea-db.org/rhea/{numeric_id}'
    try:
        req = urllib.request.Request(url)
        req.add_header('Accept', 'application/json')
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            # Rhea JSON structure: data['ec'] is the EC number string
            ec = data.get('ec', '')
            rhea_cache[rhea_id] = ec
            return ec
    except Exception as e:
        rhea_cache[rhea_id] = f'ERROR: {e}'
        return rhea_cache[rhea_id]

rows = []
with open(INPUT, 'r', encoding='utf-8') as f:
    reader = csv.DictReader(f, delimiter='\t')
    fieldnames = reader.fieldnames
    for row in reader:
        rows.append(row)

# For each row, look up Rhea→EC mapping
rhea_ids_all = set()
for row in rows:
    for rid in row.get('Rhea ID', '').split():
        rid = rid.strip()
        if rid:
            rhea_ids_all.add(rid)

print(f'Found {len(rhea_ids_all)} unique Rhea IDs. Looking up EC mappings...')
print()

for i, rid in enumerate(sorted(rhea_ids_all)):
    ec = lookup_rhea(rid)
    if (i + 1) % 20 == 0:
        print(f'  {i+1}/{len(rhea_ids_all)} done...')
    time.sleep(0.15)  # Be polite to the API

print(f'  {len(rhea_ids_all)}/{len(rhea_ids_all)} done.')
print()

# Add a Rhea→EC mapping column to each row
new_fields = list(fieldnames) + ['Rhea→EC mapping']

for row in rows:
    mappings = []
    for rid in row.get('Rhea ID', '').split():
        rid = rid.strip()
        if rid:
            ec = rhea_cache.get(rid, '')
            mappings.append(f'{rid} → {ec}')
    row['Rhea→EC mapping'] = '; '.join(mappings)

with open(OUTPUT, 'w', encoding='utf-8', newline='') as f:
    writer = csv.DictWriter(f, fieldnames=new_fields, delimiter='\t', extrasaction='ignore')
    writer.writeheader()
    writer.writerows(rows)

print(f'Done! Written to {OUTPUT}')
print()
print('Sample mappings:')
print('-' * 70)
for row in rows[:5]:
    if row['Rhea→EC mapping']:
        print(f"{row['Entry']}: {row['Rhea→EC mapping']}")
        print()

# Verify: check if any Rhea EC doesn't match what UniProt says
mismatches = 0
for row in rows:
    uni_ecs = set(re.findall(r'EC\s+\d+\.\d+\.\d+\.\S+', row.get('EC numbers', '')))
    rhea_ecs = set()
    for rid in row.get('Rhea ID', '').split():
        rid = rid.strip()
        if rid and rid in rhea_cache:
            ec = rhea_cache[rid]
            if ec and not ec.startswith('ERROR'):
                rhea_ecs.add('EC ' + ec)

    if uni_ecs and rhea_ecs:
        # Rhea's EC might be more specific than UniProt's
        # Just check if there's at least some overlap
        if not (uni_ecs & rhea_ecs):
            # Allow partial matches (e.g. EC 2.5.1.- in UniProt, EC 2.5.1.29 in Rhea)
            uni_prefixes = set(e.rsplit('.', 1)[0] for e in uni_ecs)
            rhea_prefixes = set(e.rsplit('.', 1)[0] for e in rhea_ecs)
            if not (uni_prefixes & rhea_prefixes):
                mismatches += 1

print(f'Verification: {mismatches} rows with potential EC mismatch between UniProt and Rhea')
