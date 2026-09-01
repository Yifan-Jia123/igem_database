"""
Download ChEBI flat files and build a local CHEBI ID -> SMILES mapping.
Downloads ~50MB total, then local lookup is instant.
"""
import csv, gzip, json, os, sys

CHEBI_DIR = 'chebi_data'
os.makedirs(CHEBI_DIR, exist_ok=True)

BASE = 'https://ftp.ebi.ac.uk/pub/databases/chebi/flat_files'

files = {
    'compounds.tsv.gz': f'{BASE}/compounds.tsv.gz',
    'chemical_data.tsv.gz': f'{BASE}/chemical_data.tsv.gz',
    'structure_registry.tsv.gz': f'{BASE}/structure_registry.tsv.gz',
}

# ---- Step 1: download ----
import urllib.request

for fname, url in files.items():
    path = os.path.join(CHEBI_DIR, fname)
    if os.path.exists(path):
        print(f'{fname} already exists, skipping...', flush=True)
        continue
    print(f'Downloading {fname}...', flush=True)
    urllib.request.urlretrieve(url, path)
    print(f'  Done: {os.path.getsize(path) / 1024 / 1024:.1f} MB', flush=True)

# ---- Step 2: build mappings ----
print('\nBuilding ChEBI -> SMILES mapping...', flush=True)

# Map: compound_id (int) -> chebi_accession (str like "CHEBI:15377")
compound_to_chebi = {}
with gzip.open(os.path.join(CHEBI_DIR, 'compounds.tsv.gz'), 'rt', encoding='utf-8') as f:
    for row in csv.DictReader(f, delimiter='\t'):
        compound_to_chebi[int(row['id'])] = row['chebi_accession']

print(f'  Compounds: {len(compound_to_chebi)}', flush=True)

# Map: compound_id -> structure_id
compound_to_structure = {}
with gzip.open(os.path.join(CHEBI_DIR, 'chemical_data.tsv.gz'), 'rt', encoding='utf-8') as f:
    for row in csv.DictReader(f, delimiter='\t'):
        sid = row.get('structure_id', '')
        if sid:
            compound_to_structure[int(row['compound_id'])] = int(sid)

print(f'  Compounds with structure: {len(compound_to_structure)}', flush=True)

# Map: structure_id -> SMILES
structure_to_smiles = {}
with gzip.open(os.path.join(CHEBI_DIR, 'structure_registry.tsv.gz'), 'rt', encoding='utf-8') as f:
    for row in csv.DictReader(f, delimiter='\t'):
        layers_raw = row.get('layers', '{}')
        try:
            layers = json.loads(layers_raw)
            smi = layers.get('CANONICAL_SMILES', '')
            if smi:
                structure_to_smiles[int(row['structure_id'])] = smi
        except:
            pass

print(f'  Structures with SMILES: {len(structure_to_smiles)}', flush=True)

# ---- Step 3: final mapping CHEBI:xxxxx -> SMILES ----
chebi_to_smiles = {}
for comp_id, chebi_acc in compound_to_chebi.items():
    struct_id = compound_to_structure.get(comp_id)
    if struct_id and struct_id in structure_to_smiles:
        chebi_to_smiles[chebi_acc] = structure_to_smiles[struct_id]

print(f'  Final ChEBI -> SMILES: {len(chebi_to_smiles)}', flush=True)

# ---- Step 4: save as simple TSV ----
output = os.path.join(CHEBI_DIR, 'chebi_smiles.tsv')
with open(output, 'w', encoding='utf-8') as f:
    f.write('CHEBI\tSMILES\n')
    for chebi, smi in sorted(chebi_to_smiles.items()):
        f.write(f'{chebi}\t{smi}\n')

print(f'\nSaved to {output}')
print(f'Size: {os.path.getsize(output) / 1024 / 1024:.1f} MB')
