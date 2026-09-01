"""
Generate uniprotkb_terpene_only.tsv (每个反应一行: 底物/产物各取首个主化合物).
Format: Entry, Rhea ID, Direction, Substrate, Substrate ChEBI, Product, Product ChEBI

构造逻辑 (与旧表 0 差异验证):
  - 底物/产物 ChEBI : rhea child 的 'ChEBI IDs (equation order)' 列(方向感知 底物|产物),
                      每侧取第一个非 CHEBI:33019(二磷酸) 的化合物
  - 底物/产物名称   : 从 Equation 文本的方向感知侧取段, 按干净名(Rhea rhea:name)匹配对应段,
                      保留方程中的计量前缀与区室后缀 (如 '3 isopentenyl diphosphate', 'abscisate(out)')
                      right-to-left 时底物取自方程右侧、产物取自方程左侧
  - Direction 列     : 旧表格式 —— 仅保留 'not specified', 其余留空
行序 = rhea child 行序。
"""
import csv
import re
import sys
import time

import requests

# 用法: python build_terpene_only.py [rhea子表] [输出]
RHEA_INPUT = sys.argv[1] if len(sys.argv) > 1 else '../for_enzyme_detail/child_tables/uniprotkb_rhea.tsv'
OUTPUT = sys.argv[2] if len(sys.argv) > 2 else 'output_terpene_only.tsv'

SPARQL_URL = 'https://sparql.rhea-db.org/sparql'
SPARQL_BATCH = 100
SPARQL_DELAY = 0.3
MAX_ATTEMPTS = 8
BACKOFF = [2, 5, 10, 20, 30, 30, 30, 30]

# 辅因子排除: 二磷酸 (旧表观察到的唯一被跳过化合物)
EXCLUDE_COFACTOR = {'CHEBI:33019'}


def clean_name(seg):
    """方程段 -> 干净化合物名: 去计量前缀 '3 ', 去尾部区室 '(out)'。"""
    seg = re.sub(r'^\d+ ', '', seg)
    seg = re.sub(r'\([^()]*\)$', '', seg).strip()
    return seg


def side_first(comps):
    """取侧内第一个非辅因子化合物。"""
    for c in comps:
        if c not in EXCLUDE_COFACTOR:
            return c
    return comps[0] if comps else ''


# ---- Step 1: 解析 rhea child ----
print('Step 1: parsing rhea child...')
rows = []          # (entry, rhea_id, direction, sub_chebi, prod_chebi, sub_segs, prod_segs)
all_chebi = set()
with open(RHEA_INPUT, 'r', encoding='utf-8') as f:
    for row in csv.DictReader(f, delimiter='\t'):
        direction = row['Direction']
        out_dir = 'not specified' if direction == 'not specified' else ''

        left, _, right = row['ChEBI IDs (equation order)'].partition(' | ')
        sub_comps = [c.strip() for c in left.split(';') if c.strip()]
        prod_comps = [c.strip() for c in right.split(';') if c.strip()]
        sub_chebi = side_first(sub_comps)
        prod_chebi = side_first(prod_comps)
        all_chebi.update(c for c in (sub_chebi, prod_chebi) if c)

        # 方程段: 方向感知对应侧 (right-to-left 时底物=方程右侧, 产物=方程左侧)
        eq_left, _, eq_right = row['Equation'].partition(' = ')
        ls = [s.strip() for s in eq_left.split(' + ')]
        rs = [s.strip() for s in eq_right.split(' + ')]
        if direction == 'right-to-left':
            sub_segs, prod_segs = rs, ls
        else:
            sub_segs, prod_segs = ls, rs

        rows.append((row['Entry'], row['Rhea ID'], out_dir,
                     sub_chebi, prod_chebi, sub_segs, prod_segs))

print(f'  Reactions: {len(rows)}, unique compounds: {len(all_chebi)}')


# ---- Step 2: Rhea SPARQL 取 rhea:name (Rhea 短名, 同 fetch_rhea.py) ----
def batch_query_names(ids):
    quoted = ', '.join(f'"{i}"' for i in ids)
    query = f'''
    PREFIX rhea: <http://rdf.rhea-db.org/>
    SELECT ?acc ?name WHERE {{
      ?compound rhea:accession ?acc ;
                rhea:name ?name .
      FILTER(?acc IN ({quoted}))
    }}
    '''
    r = requests.get(SPARQL_URL,
                     params={'query': query, 'format': 'json'},
                     headers={'User-Agent': 'Mozilla/5.0'},
                     timeout=60)
    r.raise_for_status()
    out = {}
    for b in r.json()['results']['bindings']:
        acc = b['acc']['value']
        if acc not in out:
            out[acc] = b['name']['value']
    return out


print('Step 2: fetching names from Rhea SPARQL...')
name_map = {}
chebi_list = sorted(all_chebi)
for i in range(0, len(chebi_list), SPARQL_BATCH):
    batch = chebi_list[i:i + SPARQL_BATCH]
    for attempt in range(MAX_ATTEMPTS):
        try:
            name_map.update(batch_query_names(batch))
            break
        except Exception as e:
            if attempt < MAX_ATTEMPTS - 1:
                time.sleep(BACKOFF[attempt])
            else:
                print(f'  Batch {i} FAILED: {e}')
    print(f'  Progress: {min(i + SPARQL_BATCH, len(chebi_list))}/{len(chebi_list)}')
    if i + SPARQL_BATCH < len(chebi_list):
        time.sleep(SPARQL_DELAY)

missing = all_chebi - set(name_map)
print(f'  Compounds with names: {len(name_map)}, missing: {len(missing)}')
for m in list(missing)[:10]:
    print(f'    MISSING NAME: {m}')


def display_name(segments, rhea_name):
    """在方程段中匹配干净名 ==/包含 Rhea 短名的段, 返回原样段(保留计量/区室)。"""
    if not rhea_name:
        return ''
    for s in segments:
        cn = clean_name(s)
        if cn == rhea_name or rhea_name in cn or cn in rhea_name:
            return s
    return ''


# ---- Step 3: 组装输出 ----
print('Step 3: writing output...')
fields = ['Entry', 'Rhea ID', 'Direction', 'Substrate', 'Substrate ChEBI',
          'Product', 'Product ChEBI']
with open(OUTPUT, 'w', encoding='utf-8', newline='') as f:
    w = csv.writer(f, delimiter='\t')
    w.writerow(fields)
    for entry, rid, direction, sub_chebi, prod_chebi, sub_segs, prod_segs in rows:
        w.writerow([entry, rid, direction,
                    display_name(sub_segs, name_map.get(sub_chebi, '')), sub_chebi,
                    display_name(prod_segs, name_map.get(prod_chebi, '')), prod_chebi])

print(f'Done! {len(rows)} rows -> {OUTPUT}')
