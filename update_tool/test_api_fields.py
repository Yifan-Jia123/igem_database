"""
Test UniProt REST API fields for GO and isoform reproduction.
"""
import requests
import json

test_entries = ['A0A075FBG7', 'A0A0A6ZFY4', 'P0DI77', 'A4FVP2']
acc_str = ','.join(test_entries)

# Try gene_ontology field
print('=== Field: gene_ontology ===')
url = f'https://rest.uniprot.org/uniprotkb/accessions?accessions={acc_str}&fields=accession,gene_ontology'
try:
    resp = requests.post(url, headers={'Accept': 'application/json'}, timeout=30)
    print(f'  Status: {resp.status_code}')
    if resp.status_code == 200:
        for entry in resp.json().get('results', []):
            go = entry.get('gene_ontology', '')
            print(f'  {entry["primaryAccession"]}: {go[:300]}')
except Exception as e:
    print(f'  Error: {e}')

# Full sequence object for P0DI77 (has isoforms)
print('\n=== Full sequence object for P0DI77 ===')
url2 = 'https://rest.uniprot.org/uniprotkb/P0DI77?fields=accession,sequence'
try:
    resp2 = requests.get(url2, headers={'Accept': 'application/json'}, timeout=30)
    data = resp2.json()
    print(json.dumps(data.get('sequence', {}), indent=1, ensure_ascii=False)[:1500])
except Exception as e:
    print(f'  Error: {e}')
