"""
Generate uniprotkb_terpene_pairs.tsv (每个 底物->产物 对一行, 列出催化它的所有酶).
Format: Substrate, Substrate ChEBI, Product, Product ChEBI,
        Enzyme_1..N, Rhea ID_1..N, Direction_1..N

Source: uniprotkb_terpene_only.tsv 按 (Substrate ChEBI, Product ChEBI) 分组。
  - 组内酶去重: 同一酶催化同一对的多反应, Rhea ID 升序 '; ' 连接, Direction 取该酶第一个反应的值
  - 组内酶排序 = Entry 字符串升序
  - 组间排序 = (Substrate ChEBI 字符串, Product ChEBI 字符串) 升序 (与旧表一致)
  - Substrate/Product 名称: 去前导计量前缀 'N ' (如 '3 isopentenyl diphosphate' -> 'isopentenyl diphosphate'),
    立体描述符括号保留 ((2E)-farnesyl ...)
"""
import csv
import re
import sys
from collections import OrderedDict

# 用法: python build_terpene_pairs.py [terpene_only] [输出]
INPUT = sys.argv[1] if len(sys.argv) > 1 else 'output_terpene_only.tsv'
OUTPUT = sys.argv[2] if len(sys.argv) > 2 else 'output_terpene_pairs.tsv'


def rhea_key(rid):
    return int(rid.split(':')[1])


def strip_stoich(name):
    """去前导计量前缀 'N ' (立体描述符括号保留)。"""
    return re.sub(r'^\d+ ', '', name)


# ---- 单次遍历: (sub, prod) -> {entry: [(rid, direction), ...]}, 记录首个名称 ----
pairs = OrderedDict()  # key -> {'sub': name, 'prod': name, 'enzymes': {entry: [(rid,dir)]}}
with open(INPUT, 'r', encoding='utf-8') as f:
    for row in csv.DictReader(f, delimiter='\t'):
        key = (row['Substrate ChEBI'], row['Product ChEBI'])
        if key not in pairs:
            pairs[key] = {'sub': row['Substrate'], 'prod': row['Product'],
                          'enzymes': {}}
        pairs[key]['enzymes'].setdefault(row['Entry'], []).append(
            (row['Rhea ID'], row['Direction']))

max_n = max((len(v['enzymes']) for v in pairs.values()), default=0)
print(f'Unique substrate->product pairs: {len(pairs)}, max enzymes per pair: {max_n}')

# ---- 组装 ----
fields = ['Substrate', 'Substrate ChEBI', 'Product', 'Product ChEBI']
for i in range(1, max_n + 1):
    fields += [f'Enzyme_{i}', f'Rhea ID_{i}', f'Direction_{i}']

out_rows = []
for (sub_chebi, prod_chebi) in sorted(pairs.keys()):  # 组间字符串升序
    meta = pairs[(sub_chebi, prod_chebi)]
    enzymes = sorted(meta['enzymes'].keys())  # 组内酶 Entry 字符串升序
    row = OrderedDict()
    row['Substrate'] = strip_stoich(meta['sub'])
    row['Substrate ChEBI'] = sub_chebi
    row['Product'] = strip_stoich(meta['prod'])
    row['Product ChEBI'] = prod_chebi
    for i in range(1, max_n + 1):
        if i <= len(enzymes):
            entry = enzymes[i - 1]
            rxns = meta['enzymes'][entry]
            row[f'Enzyme_{i}'] = entry
            row[f'Rhea ID_{i}'] = '; '.join(sorted({r for r, _ in rxns}, key=rhea_key))
            # Direction: 仅当该酶所有反应都是 'not specified' 才记为 'not specified', 否则 '' (与旧表一致)
            row[f'Direction_{i}'] = ('not specified' if all(d == 'not specified' for _, d in rxns) else '')
        else:
            for c in ['Enzyme', 'Rhea ID', 'Direction']:
                row[f'{c}_{i}'] = ''
    out_rows.append(row)

with open(OUTPUT, 'w', encoding='utf-8', newline='') as f:
    w = csv.DictWriter(f, delimiter='\t', fieldnames=fields)
    w.writeheader()
    w.writerows(out_rows)

print(f'Done! {len(out_rows)} rows, {len(fields)} cols -> {OUTPUT}')
