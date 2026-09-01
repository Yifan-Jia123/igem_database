"""
Fetch full reference/citation data from UniProt REST API using the
lit_pubmed_id field. Returns all references (journal articles, submissions,
books, etc.) with title, authors, journal, volume, pages, date, PMID, DOI, URL.
"""
import csv
import requests
import time
import json
import os
import sys

# 用法: python fetch_references.py [原始TSV(取Entry列)] [输出] [缓存文件]
# 注意: Entry 列表取自原始 TSV 而非 master, 打破循环依赖 (master 由本表拼装)。
INPUT = sys.argv[1] if len(sys.argv) > 1 else '../uniprotkb_terpene_AND_reviewed_true_2026_07_10.tsv'
OUTPUT = sys.argv[2] if len(sys.argv) > 2 else 'output_references.tsv'
CACHE_FILE = sys.argv[3] if len(sys.argv) > 3 else '_ref_cache.json'
BATCH_SIZE = 50

# ---- Step 1: collect all entries ----
entries = []
with open(INPUT, 'r', encoding='utf-8') as f:
    for row in csv.DictReader(f, delimiter='\t'):
        entries.append(row['Entry'])
entries = sorted(entries)  # 旧表按 Entry 升序
print(f'Total entries: {len(entries)}')

# ---- Step 2: load cache ----
all_refs = {}  # entry -> [ref_dict, ...]
if os.path.exists(CACHE_FILE):
    with open(CACHE_FILE, 'r', encoding='utf-8') as f:
        all_refs = json.load(f)
    print(f'Loaded {len(all_refs)} cached entries')

# ---- Step 3: batch fetch from UniProt ----
MAX_RETRIES = 5
BACKOFF = [2, 5, 10, 20, 40]

for i in range(0, len(entries), BATCH_SIZE):
    batch = entries[i:i + BATCH_SIZE]
    missing = [e for e in batch if e not in all_refs]
    if not missing:
        progress = min(i + BATCH_SIZE, len(entries))
        print(f'  Skipped {progress}/{len(entries)} (cached)')
        continue

    accessions_str = ','.join(missing)
    url = f'https://rest.uniprot.org/uniprotkb/accessions?accessions={accessions_str}&fields=accession,lit_pubmed_id'

    success = False
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.post(url, headers={'Accept': 'application/json'}, timeout=120)
            if resp.status_code == 200:
                results = resp.json().get('results', [])
                for entry_data in results:
                    acc = entry_data['primaryAccession']
                    refs = []
                    for ref in entry_data.get('references', []):
                        cit = ref['citation']
                        xrefs = {x['database']: x['id'] for x in cit.get('citationCrossReferences', [])}
                        pmid = xrefs.get('PubMed', '')
                        doi = xrefs.get('DOI', '')
                        refs.append({
                            'pmid': pmid,
                            'doi': doi,
                            'title': cit.get('title', ''),
                            'authors': '; '.join(cit.get('authors', [])),
                            'journal': cit.get('journal', ''),
                            'volume': cit.get('volume', ''),
                            'pages': f'{cit.get("firstPage", "")}-{cit.get("lastPage", "")}'.strip('-'),
                            'year': cit.get('publicationDate', ''),
                            'type': cit.get('citationType', ''),
                            'positions': '; '.join(ref.get('referencePositions', [])),
                            'url': f'https://pubmed.ncbi.nlm.nih.gov/{pmid}/' if pmid else f'https://doi.org/{doi}' if doi else '',
                        })
                    all_refs[acc] = refs
                # Empty for missing entries
                for e in missing:
                    if e not in all_refs:
                        all_refs[e] = []
                success = True
                break
            else:
                print(f'  Batch {i//BATCH_SIZE+1}: HTTP {resp.status_code}, attempt {attempt+1}')
                if attempt < MAX_RETRIES - 1:
                    time.sleep(BACKOFF[attempt])
        except Exception as ex:
            print(f'  Batch {i//BATCH_SIZE+1}: {type(ex).__name__}, attempt {attempt+1}')
            if attempt < MAX_RETRIES - 1:
                time.sleep(BACKOFF[attempt])

    if not success:
        with open(CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(all_refs, f, ensure_ascii=False)
        print(f'  FAILED. Cache saved ({len(all_refs)} entries). Re-run.')
        exit(1)

    progress = min(i + BATCH_SIZE, len(entries))
    loaded = sum(1 for v in all_refs.values() if v)
    print(f'  Fetched {progress}/{len(entries)} ({loaded} with refs)')

    if (i // BATCH_SIZE + 1) % 5 == 0:
        with open(CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(all_refs, f, ensure_ascii=False)
    if i + BATCH_SIZE < len(entries):
        time.sleep(0.5)

# ---- Step 4: stats ----
max_refs = max((len(v) for v in all_refs.values()), default=0)
total_refs = sum(len(v) for v in all_refs.values())
with_refs = sum(1 for v in all_refs.values() if v)
without_refs = sum(1 for v in all_refs.values() if not v)
print(f'\nMax refs per entry: {max_refs}')
print(f'Total references: {total_refs}')
print(f'Entries with refs: {with_refs}, without refs: {without_refs}')

# Count refs without PMID (submissions etc.)
no_pmid = sum(1 for v in all_refs.values() for r in v if not r['pmid'])
print(f'References without PubMed ID: {no_pmid}')

# ---- Step 5: build columns ----
fields = ['Entry']
for n in range(1, max_refs + 1):
    fields += [
        f'PMID_{n}', f'DOI_{n}',
        f'Title_{n}', f'Authors_{n}',
        f'Journal_{n}', f'Volume_{n}', f'Pages_{n}', f'Year_{n}',
        f'Type_{n}', f'Positions_{n}', f'URL_{n}',
    ]

# ---- Step 6: write ----
with open(OUTPUT, 'w', encoding='utf-8', newline='') as f:
    writer = csv.DictWriter(f, fieldnames=fields, delimiter='\t', extrasaction='ignore')
    writer.writeheader()
    for entry in entries:
        refs = all_refs.get(entry, [])
        row = {'Entry': entry}
        for idx, ref in enumerate(refs):
            n = idx + 1
            row[f'PMID_{n}'] = ref['pmid']
            row[f'DOI_{n}'] = ref['doi']
            row[f'Title_{n}'] = ref['title']
            row[f'Authors_{n}'] = ref['authors']
            row[f'Journal_{n}'] = ref['journal']
            row[f'Volume_{n}'] = ref['volume']
            row[f'Pages_{n}'] = ref['pages']
            row[f'Year_{n}'] = ref['year']
            row[f'Type_{n}'] = ref['type']
            row[f'Positions_{n}'] = ref['positions']
            row[f'URL_{n}'] = ref['url']
        writer.writerow(row)

print(f'Written: {len(entries)} rows, {len(fields)} cols -> {OUTPUT}')

# ---- Step 7: sample ----
print('\nSample:')
print('-' * 80)
with open(OUTPUT, 'r', encoding='utf-8') as f:
    for row in csv.DictReader(f, delimiter='\t'):
        if row.get('Title_1'):
            print(f"Entry: {row['Entry']}")
            for i in range(1, min(4, max_refs + 1)):
                ti = row.get(f'Title_{i}', '')
                if ti:
                    au = row.get(f'Authors_{i}', '')
                    jo = row.get(f'Journal_{i}', '')
                    vo = row.get(f'Volume_{i}', '')
                    pa = row.get(f'Pages_{i}', '')
                    ye = row.get(f'Year_{i}', '')
                    ty = row.get(f'Type_{i}', '')
                    pm = row.get(f'PMID_{i}', '')
                    do = row.get(f'DOI_{i}', '')
                    po = row.get(f'Positions_{i}', '')
                    print(f'  #{i}: [{ty}] {ti[:100]}')
                    print(f'       Authors: {au[:100]}')
                    print(f'       {jo} {vo}:{pa} ({ye})')
                    print(f'       PMID: {pm}  DOI: {do}')
                    if po:
                        print(f'       Evidence: {po[:120]}')
            break

# Clean up
if os.path.exists(CACHE_FILE):
    os.remove(CACHE_FILE)
    print('\nCache removed.')
