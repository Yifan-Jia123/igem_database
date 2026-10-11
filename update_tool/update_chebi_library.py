"""
Update the ChEBI reference library (chebi_full.tsv + chebi_smiles.tsv).

Isolation principle: all output goes to this folder's chebi_data/ (default); never touch ../chebi_data/ (the original directory).
When swapping in new data: just replace the workflow input paths with the new chebi_full.tsv / chebi_smiles.tsv (build_terpene_compounds,
            build_all_nodes use chebi_full; fetch_rhea uses chebi_smiles).

Data sources: ChEBI FTP flat files
  compounds.tsv.gz          : id, name, ..., chebi_accession, ...   (Name + full compound list)
  chemical_data.tsv.gz      : id, compound_id, ..., mass, ..., structure_id  (molecular mass + structure link)
  structure_registry.tsv.gz : id, structure_id, layers(json), ...   (CANONICAL_SMILES)
  structures.tsv.gz         : id, compound_id, ..., standard_inchi, standard_inchi_key,
                              default_structure                       (official InChI Key)
    The last one is build_all_nodes' source for the official InChI Key. It **can only come from ChEBI** --
    an earlier version asked PubChem's /compound/name/ with a ChEBI ID, but that is a synonym fuzzy-match endpoint,
    and several ChEBI numbers collapsed to the same CID, so 27 of 712 entries wrote in another molecule's key.

Output format (same as the old library):
  chebi_full.tsv   : ChEBI ID, Name, SMILES, Molecular Mass, ChEBI URL   (all compounds, sorted by ChEBI ID)
  chebi_smiles.tsv : CHEBI, SMILES                                        (only compounds that have a SMILES)

Usage:
  python update_chebi_library.py [output dir] [--refresh] [--old=<old library path>]
    --refresh        force a re-download of the flat files (skipped by default if they already exist)
    --old=<old lib>  override the backfill source (read-only). SMILES/Mass missing from the new library are backfilled from the old,
                     and curation_overrides.tsv records the patch. Default ../chebi_data/chebi_full.tsv
"""
import csv
import gzip
import json
import os
import re
import sys
import urllib.request


def clean_name(row):
    """Prefer ascii_name (no HTML; Greek letters/minus signs already ASCII, matching the old library format);
       fall back to name with HTML tags stripped when empty."""
    a = row.get('ascii_name', '').strip()
    if a:
        return a
    return re.sub(r'<[^>]+>', '', row.get('name', '')).strip()

OUT_DIR = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith('--') else 'chebi_data'
REFRESH = '--refresh' in sys.argv
# Backfill source: SMILES/Mass missing from the new library are restored from the old reference library (read-only, the original file is untouched)
OLD_LIB = '../chebi_data/chebi_full.tsv'
for a in sys.argv:
    if a.startswith('--old='):
        OLD_LIB = a[len('--old='):]
        break

BASE = 'https://ftp.ebi.ac.uk/pub/databases/chebi/flat_files'
FILES = ['compounds.tsv.gz', 'chemical_data.tsv.gz', 'structure_registry.tsv.gz',
         'structures.tsv.gz']

os.makedirs(OUT_DIR, exist_ok=True)


def download():
    """Download the flat files locally (skip an existing one unless --refresh)."""
    for fname in FILES:
        path = os.path.join(OUT_DIR, fname)
        if os.path.exists(path) and not REFRESH:
            print(f'{fname}: exists, skip')
            continue
        url = f'{BASE}/{fname}'
        print(f'Downloading {fname} ...', flush=True)
        urllib.request.urlretrieve(url, path)
        print(f'  {os.path.getsize(path)/1024/1024:.1f} MB', flush=True)


def read_gz(path):
    with gzip.open(path, 'rt', encoding='utf-8') as f:
        for row in csv.DictReader(f, delimiter='\t'):
            yield row


def build():
    # 1) compound_id -> (chebi_accession, name)
    acc2name = {}   # chebi_accession -> name
    acc2id = {}     # chebi_accession -> int compound id
    print('Parsing compounds.tsv.gz ...', flush=True)
    for row in read_gz(os.path.join(OUT_DIR, 'compounds.tsv.gz')):
        acc = row['chebi_accession']
        cid = int(row['id'])
        acc2id[acc] = cid
        if acc:
            acc2name[acc] = clean_name(row)

    # 2) chemical_data: compound_id -> mass ; compound_id -> structure_id
    id2mass = {}
    id2struct = {}
    print('Parsing chemical_data.tsv.gz ...', flush=True)
    for row in read_gz(os.path.join(OUT_DIR, 'chemical_data.tsv.gz')):
        cid = int(row['compound_id'])
        if row['mass']:
            id2mass[cid] = row['mass']
        if row['structure_id']:
            id2struct[cid] = int(row['structure_id'])

    # 3) structure_id -> SMILES
    struct2smi = {}
    print('Parsing structure_registry.tsv.gz ...', flush=True)
    for row in read_gz(os.path.join(OUT_DIR, 'structure_registry.tsv.gz')):
        sid = int(row['structure_id'])
        if sid in struct2smi:
            continue
        try:
            layers = json.loads(row['layers'])
            smi = layers.get('CANONICAL_SMILES', '')
            if smi:
                struct2smi[sid] = smi
        except Exception:
            pass

    # 4) assemble
    chebi_full = {}   # accession -> row
    for acc in acc2name:
        cid = acc2id.get(acc)
        smi = struct2smi.get(id2struct.get(cid)) if cid and cid in id2struct else ''
        mass = id2mass.get(cid, '') if cid else ''
        chebi_full[acc] = {
            'ChEBI ID': acc,
            'Name': acc2name[acc],
            'SMILES': smi,
            'Molecular Mass': mass,
            'ChEBI URL': f'https://www.ebi.ac.uk/chebi/{acc}',
        }
    # If there is no compound list, degrade to filling in by structure
    if not chebi_full:
        print('WARN: compounds.tsv empty')
        return

    # 4.5) Backfill overrides: where the new library lacks a SMILES/Mass that the old one has, restore the old value (manual override patch)
    overrides = []
    if OLD_LIB and os.path.exists(OLD_LIB):
        old = {}
        with open(OLD_LIB, encoding='utf-8') as f:
            for row in csv.DictReader(f, delimiter='\t'):
                old[row['ChEBI ID']] = row
        for acc, row in chebi_full.items():
            o = old.get(acc)
            if not o:
                continue
            for field in ('SMILES', 'Molecular Mass'):
                if not row[field] and o.get(field):
                    overrides.append((acc, field, o[field]))
                    row[field] = o[field]
        if overrides:
            print(f'\nOverrides backfilled from {OLD_LIB}: {len(overrides)} cells')
            for acc, field, val in overrides:
                print(f'  {acc} {field}: {val[:80]}')
            with open(os.path.join(OUT_DIR, 'curation_overrides.tsv'), 'w',
                      encoding='utf-8', newline='') as f:
                f.write('ChEBI ID\tField\tValue\n')
                for acc, field, val in overrides:
                    f.write(f'{acc}\t{field}\t{val}\n')
            print(f'  Patch log -> {OUT_DIR}/curation_overrides.tsv')
        else:
            print('\nNo overrides needed (新库与旧库缺失面无重叠)')
    else:
        print(f'\nWARN: old library not found at {OLD_LIB}, no backfill')

    # 5) write chebi_full.tsv (all compounds, sorted by ChEBI ID)
    rows = sorted(chebi_full.values(), key=lambda r: r['ChEBI ID'])
    out = os.path.join(OUT_DIR, 'chebi_full.tsv')
    fields = ['ChEBI ID', 'Name', 'SMILES', 'Molecular Mass', 'ChEBI URL']
    with open(out, 'w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, delimiter='\t', fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print(f'chebi_full.tsv: {len(rows)} rows -> {out}')

    # 6) write chebi_smiles.tsv (only compounds with a SMILES)
    smi_rows = sorted(((r['ChEBI ID'], r['SMILES']) for r in rows if r['SMILES']), key=lambda x: x[0])
    out2 = os.path.join(OUT_DIR, 'chebi_smiles.tsv')
    with open(out2, 'w', encoding='utf-8', newline='') as f:
        f.write('CHEBI\tSMILES\n')
        for acc, smi in smi_rows:
            f.write(f'{acc}\t{smi}\n')
    print(f'chebi_smiles.tsv: {len(smi_rows)} rows -> {out2}')


if __name__ == '__main__':
    download()
    build()
    print('\n完成! 新参考库在', os.path.abspath(OUT_DIR))
