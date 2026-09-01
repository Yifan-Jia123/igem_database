"""
下载 UniProt 统一全列表 (单张 TSV, 取代三个分散导出 0710/0712/0716)。

数据来源: UniProt REST stream 端点, 查询 (terpene) AND reviewed:true,
19 列 = 三个导出列的并集 (与旧三导出列头完全一致)。

用法:
  python download_uniprot.py [输出路径] [--query="..."] [--fields="..."]
  默认: 输出 output_uniprot_unified.tsv, 查询 (terpene) AND reviewed:true, 19 列全选。

隔离原则: 输出写到本文件夹, 不碰 ../ 原始文件。
换新数据时: 直接重跑本脚本覆盖输出, 再把 run_all.py 指向新文件即可。
"""
import csv
import requests
import sys
import time

OUTPUT = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith('--') else 'output_uniprot_unified.tsv'
QUERY = '(terpene) AND reviewed:true'
FIELDS = ('accession,id,protein_name,organism_name,gene_primary,kinetics,cc_function,'
          'rhea,go_p,sequence,ft_var_seq,ec,cc_catalytic_activity,cc_alternative_products,'
          'lit_pubmed_id,lit_doi_id,xref_geneid,length,mass')
for a in sys.argv:
    if a.startswith('--query='):
        QUERY = a[len('--query='):]
    elif a.startswith('--fields='):
        FIELDS = a[len('--fields='):]

URL = 'https://rest.uniprot.org/uniprotkb/stream'
params = {'query': QUERY, 'format': 'tsv', 'fields': FIELDS}

print(f'Query : {QUERY}')
print(f'Fields: {len(FIELDS.split(","))} columns')
print(f'Output: {OUTPUT}')

# 确认总条数
try:
    cnt = requests.get('https://rest.uniprot.org/uniprotkb/search',
                       params={'query': QUERY, 'size': 1},
                       headers={'Accept': 'application/json'}, timeout=60)
    total = cnt.headers.get('x-total-results', '?')
    print(f'Total entries: {total}')
except Exception as e:
    print(f'WARN: count check failed ({e}), proceeding anyway')

# 流式下载 (stream 端点无分页上限)
for attempt in range(5):
    try:
        r = requests.get(URL, params=params, stream=True, timeout=600)
        if r.status_code != 200:
            print(f'HTTP {r.status_code}, retry {attempt+1}/5')
            time.sleep(5 * (attempt + 1))
            continue
        # 逐块写文件
        with open(OUTPUT, 'wb') as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                f.write(chunk)
        break
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
        print(f'{type(e).__name__}, retry {attempt+1}/5')
        time.sleep(5 * (attempt + 1))
else:
    sys.exit('Download FAILED after 5 retries')

# 校验
with open(OUTPUT, encoding='utf-8') as f:
    rows = list(csv.DictReader(f, delimiter='\t'))
print(f'\nDone: {len(rows)} rows, {len(rows[0])} columns -> {OUTPUT}')
