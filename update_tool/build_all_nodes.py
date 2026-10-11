"""
Generate all_nodes.tsv (graph node table).
Format: ChEBI ID, Name, InChI Key, InChI Key Derived

Construction:
  - Compound set = all Substrate/Product ChEBI from terpene_only, de-duplicated, GENERIC:* dropped
  - Name        = Name from chebi_full.tsv
  - InChI Key   = ChEBI official standard_inchi_key (the default_structure=true row in
                  structures.tsv.gz). The authoritative value, and the column published externally.
  - InChI Key Derived = computed by RDKit from this row's SMILES. The SMILES is the same one written
                  to compound.smiles (chebi_full.tsv and uniprotkb_terpene_compounds.tsv measured
                  byte-for-byte identical on 730/730), so it is guaranteed consistent with the
                  structure in the database. Across the 710 comparable rows the two columns
                  disagree on only 2 (CHEBI:231826 / CHEBI:53643 -- ChEBI's own smiles and
                  standard_inchi contradict each other), and both keys resolve for those two.
                  The second column exists precisely to avoid arbitrating these 2 rows.
  - Sort        = ChEBI ID string ascending (same as the old table)

When the 4th argument is an existing all_nodes file, reuse its official InChI Key column
(for recovery/verification). The derived column is **always computed fresh** -- it is a local
calculation with no reason to skip, and "reusing an old file missing the column" silently
produces an empty column.

History: before 2026-10 the InChI Key was fetched by asking PubChem's /compound/name/ endpoint
with the ChEBI ID. That is a **synonym fuzzy-match** endpoint, and multiple ChEBI IDs collapse to
the same CID -- of 712 rows with a value, 27 got another molecule's key (5 colliding pairs, 4 of
them differing even in the connectivity layer). That network path has been removed entirely; do
not restore it.
"""
import csv
import gzip
import os
import sys

# Usage: python build_all_nodes.py [terpene_only] [chebi_full] [output] [optional: existing all_nodes to reuse its official keys]
TERPENE_INPUT = sys.argv[1] if len(sys.argv) > 1 else '../for_graph/uniprotkb_terpene_only.tsv'
CHEBI_INPUT = sys.argv[2] if len(sys.argv) > 2 else 'chebi_data/chebi_full.tsv'
OUTPUT = sys.argv[3] if len(sys.argv) > 3 else 'output_all_nodes.tsv'
REUSE = sys.argv[4] if len(sys.argv) > 4 else ''

# structures.tsv.gz sits in chebi_data/ alongside chebi_full.tsv -- derived from the input path, not hard-coded to a local path
STRUCTURES_FILE = os.path.join(os.path.dirname(os.path.abspath(CHEBI_INPUT)), 'structures.tsv.gz')

# Print the detail once parse failures exceed this count (avoids an unending screenful while not hiding the problem)
MAX_REPORT_FAILED = 20


def _load_rdkit():
    """Lazy import: the recovery path via the 4th argument does not need rdkit."""
    try:
        from rdkit import Chem, RDLogger
    except ImportError:
        sys.exit('算派生 InChI Key 需要 rdkit:  pip install rdkit\n'
                 '(若只想复用已有产物, 传第 4 个参数指向它)')
    # Parse warnings are noisy, but failures are not missed -- they are counted as None and printed below
    RDLogger.DisableLog('rdApp.*')
    return Chem


def load_official_keys(chebi_ids):
    """Read standard_inchi_key from the default_structure row of ChEBI's official structures.tsv.gz."""
    if not os.path.exists(STRUCTURES_FILE):
        sys.exit(f'缺少参考库: {STRUCTURES_FILE}\n'
                 f'先跑:  python {os.path.join(os.path.dirname(os.path.abspath(__file__)), "update_chebi_library.py")}')
    wanted = set(chebi_ids)
    keys = {}
    with gzip.open(STRUCTURES_FILE, 'rt', encoding='utf-8') as f:
        for row in csv.DictReader(f, delimiter='\t'):
            if row.get('default_structure') != 'true':
                continue
            acc = row.get('compound_id', '').strip()
            if not acc:
                continue
            # Bare number here (15377); our database uses CHEBI:15377
            if not acc.startswith('CHEBI:'):
                acc = 'CHEBI:' + acc
            if acc not in wanted:
                continue
            key = row.get('standard_inchi_key', '').strip()
            if key:
                keys[acc] = key
    return keys


def reuse_official_keys(path):
    with open(path, 'r', encoding='utf-8') as f:
        return {r['ChEBI ID']: r['InChI Key'].strip()
                for r in csv.DictReader(f, delimiter='\t')
                if r.get('InChI Key', '').strip()}


def compute_derived_keys(chebi_ids, smiles_by_id):
    Chem = _load_rdkit()
    keys, failed = {}, []
    for cid in chebi_ids:
        smi = smiles_by_id.get(cid, '')
        mol = Chem.MolFromSmiles(smi) if smi else None
        if mol is None:
            # No SMILES and failed parse both land here: neither yields a derived key, and neither should be silent
            failed.append(cid)
            keys[cid] = ''
            continue
        keys[cid] = Chem.MolToInchiKey(mol) or ''
    return keys, failed


# ---- Step 1: compound set + names + SMILES ----
comps, smiles = {}, {}
with open(TERPENE_INPUT, 'r', encoding='utf-8') as f:
    for r in csv.DictReader(f, delimiter='\t'):
        for c in (r['Substrate ChEBI'], r['Product ChEBI']):
            if c and not c.startswith('GENERIC'):
                comps.setdefault(c, '')
with open(CHEBI_INPUT, 'r', encoding='utf-8') as f:
    for r in csv.DictReader(f, delimiter='\t'):
        if r['ChEBI ID'] in comps:
            comps[r['ChEBI ID']] = r['Name']
            smiles[r['ChEBI ID']] = (r.get('SMILES') or '').strip()
chebi_ids = sorted(comps.keys())
print(f'Distinct non-GENERIC compounds: {len(chebi_ids)}')

# ---- Step 2a: official InChI Key ----
if REUSE and os.path.exists(REUSE):
    official = reuse_official_keys(REUSE)
    print(f'Reused {len(official)} official InChI Keys from {REUSE}')
else:
    official = load_official_keys(chebi_ids)
    print(f'Official InChI Keys from {os.path.basename(STRUCTURES_FILE)}: {len(official)}/{len(chebi_ids)}')

# ---- Step 2b: derived InChI Key (RDKit, computed from this row's SMILES) ----
derived, failed = compute_derived_keys(chebi_ids, smiles)
print(f'Derived InChI Keys: {sum(1 for k in derived.values() if k)}/{len(chebi_ids)}'
      f' (无 SMILES 或解析失败 {len(failed)})')
if failed:
    print('  解析不出来: ' + ', '.join(failed[:MAX_REPORT_FAILED])
          + (f' ... 另 {len(failed) - MAX_REPORT_FAILED} 个' if len(failed) > MAX_REPORT_FAILED else ''))

# Print the disagreement detail directly -- normally just those 2 rows; anything else means stop and look
both = [c for c in chebi_ids if official.get(c) and derived.get(c)]
diff = [c for c in both if official[c] != derived[c]]
print(f'两列都有值 {len(both)} 条, 其中分歧 {len(diff)} 条' + (': ' + ', '.join(diff) if diff else ''))

# ---- Step 3: output ----
fields = ['ChEBI ID', 'Name', 'InChI Key', 'InChI Key Derived']
with open(OUTPUT, 'w', encoding='utf-8', newline='') as f:
    w = csv.writer(f, delimiter='\t')
    w.writerow(fields)
    for cid in chebi_ids:
        w.writerow([cid, comps[cid], official.get(cid, ''), derived.get(cid, '')])

print(f'Done! {len(chebi_ids)} rows -> {OUTPUT}')
