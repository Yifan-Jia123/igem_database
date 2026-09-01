"""
Fetch sequence cross-references from UniProt REST API and rebuild
sequence_links.tsv with INSDC nucleotide + protein + molecule type columns,
plus links to EMBL/GenBank/DDBJ (shared INSDC IDs).
"""
import csv
import requests
import time
import json
import os
import sys

# 用法: python fetch_sequence_links.py [原始TSV(取Entry列)] [输出] [缓存文件]
# 注意: Entry 列表取自原始 TSV 而非 master, 打破循环依赖 (master 由本表拼装)。
INPUT = sys.argv[1] if len(sys.argv) > 1 else '../uniprotkb_terpene_AND_reviewed_true_2026_07_10.tsv'
OUTPUT = sys.argv[2] if len(sys.argv) > 2 else 'output_sequence_links.tsv'
CACHE_FILE = sys.argv[3] if len(sys.argv) > 3 else '_sequence_links_cache.json'
BATCH_SIZE = 50

# ---- Step 1: collect all entries ----
entries = []
with open(INPUT, 'r', encoding='utf-8') as f:
    for row in csv.DictReader(f, delimiter='\t'):
        entries.append(row['Entry'])
entries = sorted(entries)  # 旧表按 Entry 升序

print(f'Total entries: {len(entries)}')

# ---- Step 2: load existing cache if any ----
all_data = {}
if os.path.exists(CACHE_FILE):
    with open(CACHE_FILE, 'r', encoding='utf-8') as f:
        all_data = json.load(f)
    print(f'Loaded {len(all_data)} cached entries, resuming...')

# ---- Step 3: batch fetch with retry ----
MAX_RETRIES = 5
RETRY_BACKOFF = [2, 5, 10, 20, 40]

for i in range(0, len(entries), BATCH_SIZE):
    batch = entries[i:i + BATCH_SIZE]
    missing = [e for e in batch if e not in all_data]
    if not missing:
        progress = min(i + BATCH_SIZE, len(entries))
        print(f'  Skipped {progress}/{len(entries)} (already cached)')
        continue

    accessions_str = ','.join(missing)
    url = f'https://rest.uniprot.org/uniprotkb/accessions?accessions={accessions_str}&fields=accession,xref_embl,xref_refseq'

    success = False
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.post(url, headers={'Accept': 'application/json'}, timeout=120)
            if resp.status_code == 200:
                results = resp.json().get('results', [])
                for entry_data in results:
                    acc = entry_data['primaryAccession']
                    embl_refs = []
                    refseq_refs = []

                    for ref in entry_data.get('uniProtKBCrossReferences', []):
                        db = ref['database']
                        props = {p['key']: p['value'] for p in ref.get('properties', [])}

                        if db == 'EMBL':
                            embl_refs.append({
                                'nuc_id': ref['id'],
                                'prot_id': props.get('ProteinId', ''),
                                'molecule': props.get('MoleculeType', ''),
                                'status': props.get('Status', ''),
                            })
                        elif db == 'RefSeq':
                            refseq_refs.append({
                                'prot_id': ref['id'],
                                'nuc_id': props.get('NucleotideSequenceId', ''),
                                'molecule': props.get('MoleculeType', ''),
                            })

                    all_data[acc] = {'embl': embl_refs, 'refseq': refseq_refs}

                returned = {r['primaryAccession'] for r in results}
                for e in missing:
                    if e not in returned:
                        all_data[e] = {'embl': [], 'refseq': []}

                success = True
                break
            else:
                print(f'  Batch {i//BATCH_SIZE+1}: HTTP {resp.status_code}, attempt {attempt+1}/{MAX_RETRIES}')
                if attempt < MAX_RETRIES - 1:
                    time.sleep(RETRY_BACKOFF[attempt])
        except (requests.exceptions.ConnectionError,
                requests.exceptions.Timeout,
                requests.exceptions.ChunkedEncodingError) as e:
            print(f'  Batch {i//BATCH_SIZE+1}: {type(e).__name__}, attempt {attempt+1}/{MAX_RETRIES}')
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_BACKOFF[attempt])

    if not success:
        print(f'  Batch {i//BATCH_SIZE+1}: FAILED after {MAX_RETRIES} retries, saving...')
        with open(CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(all_data, f, ensure_ascii=False)
        exit(1)

    progress = min(i + BATCH_SIZE, len(entries))
    print(f'  Fetched {progress}/{len(entries)} entries ({len(all_data)} loaded)')

    if (i // BATCH_SIZE + 1) % 5 == 0:
        with open(CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(all_data, f, ensure_ascii=False)
        print(f'  [cache saved]')

    if i + BATCH_SIZE < len(entries):
        time.sleep(0.5)

print(f'\nTotal entries with data: {len(all_data)}')

# ---- Step 4: determine max counts and build columns ----
max_embl = max((len(d['embl']) for d in all_data.values()), default=0)
max_refseq = max((len(d['refseq']) for d in all_data.values()), default=0)
print(f'Max EMBL refs per entry: {max_embl}')
print(f'Max RefSeq refs per entry: {max_refseq}')

# INSDC = EMBL/GenBank/DDBJ shared IDs
# Each INSDC ref: Nuc_ID + 3 Nuc_Links + Prot_ID + 3 Prot_Links + Molecule
fields = ['Entry']
for i in range(1, max_embl + 1):
    fields += [
        f'INSDC_Nuc_ID_{i}',
        f'INSDC_Nuc_EMBL_Link_{i}', f'INSDC_Nuc_GenBank_Link_{i}', f'INSDC_Nuc_DDBJ_Link_{i}',
        f'INSDC_Prot_ID_{i}',
        f'INSDC_Prot_EMBL_Link_{i}', f'INSDC_Prot_GenBank_Link_{i}', f'INSDC_Prot_DDBJ_Link_{i}',
        f'INSDC_Molecule_{i}',
    ]
for i in range(1, max_refseq + 1):
    fields += [
        f'RefSeq_Prot_ID_{i}', f'RefSeq_Prot_Link_{i}',
        f'RefSeq_Nuc_ID_{i}', f'RefSeq_Nuc_Link_{i}',
        f'RefSeq_Molecule_{i}',
    ]

# Base URLs
def _valid(v):
    return v and v != '-'

EMBL_BASE    = 'https://www.ebi.ac.uk/ena/browser/view/'        # INSDC nuc + prot
GB_NUC_BASE  = 'https://www.ncbi.nlm.nih.gov/nucleotide/'       # INSDC nuc
GB_PROT_BASE = 'https://www.ncbi.nlm.nih.gov/protein/'          # INSDC prot
DDBJ_NUC_BASE = 'https://getentry.ddbj.nig.ac.jp/getentry/na/'  # INSDC nuc
DDBJ_PROT_BASE = 'https://getentry.ddbj.nig.ac.jp/getentry/aa/' # INSDC prot
NCBI_PROT_BASE = 'https://www.ncbi.nlm.nih.gov/protein/'
NCBI_NUC_BASE  = 'https://www.ncbi.nlm.nih.gov/nucleotide/'

# ---- Step 5: write TSV ----
with open(OUTPUT, 'w', encoding='utf-8', newline='') as f:
    writer = csv.DictWriter(f, fieldnames=fields, delimiter='\t', extrasaction='ignore')
    writer.writeheader()

    for entry in entries:
        data = all_data.get(entry, {'embl': [], 'refseq': []})
        row = {'Entry': entry}

        for idx, ref in enumerate(data['embl']):
            n = idx + 1
            nuc_id = ref['nuc_id']
            prot_id = ref['prot_id']
            row[f'INSDC_Nuc_ID_{n}'] = nuc_id
            row[f'INSDC_Nuc_EMBL_Link_{n}']    = f'{EMBL_BASE}{nuc_id}'    if _valid(nuc_id) else ''
            row[f'INSDC_Nuc_GenBank_Link_{n}']  = f'{GB_NUC_BASE}{nuc_id}' if _valid(nuc_id) else ''
            row[f'INSDC_Nuc_DDBJ_Link_{n}']     = f'{DDBJ_NUC_BASE}{nuc_id}' if _valid(nuc_id) else ''
            row[f'INSDC_Prot_ID_{n}'] = prot_id
            row[f'INSDC_Prot_EMBL_Link_{n}']    = f'{EMBL_BASE}{prot_id}'    if _valid(prot_id) else ''
            row[f'INSDC_Prot_GenBank_Link_{n}']  = f'{GB_PROT_BASE}{prot_id}' if _valid(prot_id) else ''
            row[f'INSDC_Prot_DDBJ_Link_{n}']     = f'{DDBJ_PROT_BASE}{prot_id}' if _valid(prot_id) else ''
            row[f'INSDC_Molecule_{n}'] = ref['molecule']

        for idx, ref in enumerate(data['refseq']):
            n = idx + 1
            row[f'RefSeq_Prot_ID_{n}']  = ref['prot_id']
            row[f'RefSeq_Prot_Link_{n}'] = f'{NCBI_PROT_BASE}{ref["prot_id"]}' if ref['prot_id'] else ''
            row[f'RefSeq_Nuc_ID_{n}']   = ref['nuc_id']
            row[f'RefSeq_Nuc_Link_{n}'] = f'{NCBI_NUC_BASE}{ref["nuc_id"]}'   if ref['nuc_id'] else ''
            row[f'RefSeq_Molecule_{n}'] = ref['molecule']

        writer.writerow(row)

print(f'Done! {len(entries)} rows, {len(fields)} columns -> {OUTPUT}')

# ---- Step 6: stats ----
embl_with_prot = sum(1 for d in all_data.values() if any(r['prot_id'] for r in d['embl']))
embl_total = sum(1 for d in all_data.values() if d['embl'])
print(f'\nStats:')
print(f'  Entries with INSDC refs: {embl_total} ({embl_with_prot} have protein IDs)')
print(f'  Entries with RefSeq refs: {sum(1 for d in all_data.values() if d["refseq"])}')

# ---- Step 7: sample ----
print('\nSample (first entry with INSDC data):')
print('-' * 80)
with open(OUTPUT, 'r', encoding='utf-8') as f:
    reader = csv.DictReader(f, delimiter='\t')
    for row in reader:
        if row.get('INSDC_Nuc_ID_1'):
            print(f"Entry: {row['Entry']}")
            for i in range(1, min(4, max_embl + 1)):
                nuc = row.get(f'INSDC_Nuc_ID_{i}', '')
                prot = row.get(f'INSDC_Prot_ID_{i}', '')
                mol = row.get(f'INSDC_Molecule_{i}', '')
                if nuc:
                    print(f'  #{i}: Nuc={nuc}  Prot={prot}  [{mol}]')
                    print(f'       EMBL:    {row.get(f"INSDC_Nuc_EMBL_Link_{i}", "")}')
                    print(f'       GenBank: {row.get(f"INSDC_Nuc_GenBank_Link_{i}", "")}')
                    print(f'       DDBJ:    {row.get(f"INSDC_Nuc_DDBJ_Link_{i}", "")}')
                    if prot:
                        print(f'       Prot EMBL:    {row.get(f"INSDC_Prot_EMBL_Link_{i}", "")}')
                        print(f'       Prot GenBank: {row.get(f"INSDC_Prot_GenBank_Link_{i}", "")}')
                        print(f'       Prot DDBJ:    {row.get(f"INSDC_Prot_DDBJ_Link_{i}", "")}')
            break

if os.path.exists(CACHE_FILE):
    os.remove(CACHE_FILE)
    print('\nCache file removed.')
