"""
Regenerate the isoform sequences child table (uniprotkb_isoform_sequences.tsv).
Format: Entry, Isoform_ID, Isoform Length, Isoform Mass, Canonical Sequence,
        Canonical Length, Canonical Mass, Sequence
"""
import csv
import requests
import time
import json
import os
import sys

# 用法: python fetch_isoform.py [输入原始TSV] [输出文件]
INPUT = sys.argv[1] if len(sys.argv) > 1 else '../uniprotkb_terpene_AND_reviewed_true_2026_07_10.tsv'
OUTPUT = sys.argv[2] if len(sys.argv) > 2 else 'output_isoform.tsv'
CACHE_FILE = '_isoform_cache.json'
BATCH_SIZE = 50
MAX_RETRIES = 5
BACKOFF = [2, 5, 10, 20, 40]

# ---- Step 1: collect entries ----
entries = []
with open(INPUT, 'r', encoding='utf-8') as f:
    for row in csv.DictReader(f, delimiter='\t'):
        entries.append(row['Entry'])
print(f'Total entries: {len(entries)}')

# ---- Step 2: load cache ----
iso_data = {}
if os.path.exists(CACHE_FILE):
    with open(CACHE_FILE, 'r', encoding='utf-8') as f:
        iso_data = json.load(f)
    print(f'Loaded {len(iso_data)} cached entries')

# ---- Step 3: batch fetch definitions (ALTERNATIVE PRODUCTS + canonical seq) ----
for i in range(0, len(entries), BATCH_SIZE):
    batch = entries[i:i + BATCH_SIZE]
    missing = [e for e in batch if e not in iso_data]
    if not missing:
        continue

    acc_str = ','.join(missing)
    url = f'https://rest.uniprot.org/uniprotkb/accessions?accessions={acc_str}&fields=accession,cc_alternative_products,sequence'
    success = False
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.post(url, headers={'Accept': 'application/json'}, timeout=120)
            if resp.status_code == 200:
                for entry_data in resp.json().get('results', []):
                    acc = entry_data['primaryAccession']
                    # canonical sequence
                    seq_obj = entry_data.get('sequence', {})
                    canon = {
                        'seq': seq_obj.get('value', ''),
                        'length': str(seq_obj.get('length', '')),
                        'mass': str(seq_obj.get('molWeight', '')),
                    }
                    # isoforms
                    iso_ids = []
                    for c in entry_data.get('comments', []):
                        if c.get('commentType') == 'ALTERNATIVE PRODUCTS':
                            for iso in c.get('isoforms', []):
                                iso_ids.extend(iso.get('isoformIds', []))
                    iso_data[acc] = {'canon': canon, 'iso_ids': iso_ids}
                for e in missing:
                    if e not in iso_data:
                        iso_data[e] = {'canon': {'seq': '', 'length': '', 'mass': ''}, 'iso_ids': []}
                success = True
                break
            else:
                if attempt < MAX_RETRIES - 1:
                    time.sleep(BACKOFF[attempt])
        except Exception as ex:
            if attempt < MAX_RETRIES - 1:
                time.sleep(BACKOFF[attempt])
    if not success:
        print(f'  Batch {i//BATCH_SIZE+1} FAILED.')
        with open(CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(iso_data, f, ensure_ascii=False)
        exit(1)

    print(f'  Fetched defs {min(i+BATCH_SIZE, len(entries))}/{len(entries)}')
    if i + BATCH_SIZE < len(entries):
        time.sleep(0.4)

# ---- Step 4: fetch isoform sequences ----
# 氨基酸平均残基质量（Da）
AA_MASS = {
    'A': 71.0788, 'R': 156.1875, 'N': 114.1038, 'D': 115.0886,
    'C': 103.1388, 'E': 129.1155, 'Q': 128.1307, 'G': 57.0519,
    'H': 137.1411, 'I': 113.1594, 'L': 113.1594, 'K': 128.1741,
    'M': 131.1926, 'F': 147.1766, 'P': 97.1167, 'S': 87.0782,
    'T': 101.1051, 'W': 186.2132, 'Y': 163.1760, 'V': 99.1326,
}

def calc_mass(seq):
    """计算蛋白质平均质量（考虑 H2O）"""
    if not seq:
        return ''
    mass = sum(AA_MASS.get(aa, 110.0) for aa in seq)
    mass += 18.0153  # H2O
    return round(mass, 1)

total_iso = 0
entries_with_iso = 0
for idx, entry in enumerate(entries):
    info = iso_data.get(entry, {'canon': {'seq': '', 'length': '', 'mass': ''}, 'iso_ids': []})
    canon_seq = info['canon']['seq']
    if not info['iso_ids']:
        continue
    entries_with_iso += 1
    # 从缓存拿已获取的 isoform 序列
    info.setdefault('iso_seqs', {})
    for iso_id in info['iso_ids']:
        if iso_id in info['iso_seqs']:
            continue
        # 下载 isoform 序列
        try:
            url = f'https://rest.uniprot.org/uniprotkb/{iso_id}.fasta'
            r = requests.get(url, timeout=30)
            if r.status_code == 200 and r.text.strip():
                seq = ''.join(l.strip() for l in r.text.splitlines() if not l.startswith('>'))
                info['iso_seqs'][iso_id] = seq
            else:
                info['iso_seqs'][iso_id] = ''
        except Exception:
            info['iso_seqs'][iso_id] = ''
        time.sleep(0.2)
    total_iso += len(info['iso_ids'])

print(f'\nEntries with isoforms: {entries_with_iso}, total isoform rows: {total_iso}')

# ---- Step 5: write ----
with open(OUTPUT, 'w', encoding='utf-8', newline='') as f:
    w = csv.writer(f, delimiter='\t')
    w.writerow(['Entry', 'Isoform_ID', 'Isoform Length', 'Isoform Mass',
                'Canonical Sequence', 'Canonical Length', 'Canonical Mass', 'Sequence'])
    for entry in entries:
        info = iso_data.get(entry, {'canon': {'seq': '', 'length': '', 'mass': ''}, 'iso_ids': []})
        canon_seq = info['canon']['seq']
        canon_len = info['canon']['length']
        canon_mass = info['canon']['mass']
        for iso_id in info.get('iso_ids', []):
            iso_seq = info.get('iso_seqs', {}).get(iso_id, '')
            iso_len = str(len(iso_seq)) if iso_seq else ''
            iso_mass = calc_mass(iso_seq)
            w.writerow([entry, iso_id, iso_len, iso_mass,
                        canon_seq, canon_len, canon_mass, iso_seq])

print(f'Written: {entries_with_iso} entries, {total_iso} isoform rows -> {OUTPUT}')

# 清理缓存
if os.path.exists(CACHE_FILE):
    os.remove(CACHE_FILE)
    print('Cache removed.')
