"""
Regenerate the rhea child table (uniprotkb_rhea.tsv).
Format: Entry, Rhea ID, Rhea Link, Equation, Direction, EC Number, Reaction SMILES, ChEBI IDs (equation order)

Data sources:
- Equation / Direction / EC / Rhea ID : parsed from 0712 raw TSV Catalytic activity column
- ChEBI equation-order (L|R)          : Rhea SPARQL sides (reaction -> side -> participant -> compound)
- Reaction SMILES                     : local chebi_data/chebi_smiles.tsv, direction-aware
                                        (substrates/products follow the Direction column, NOT the equation text)
"""
import csv
import re
import requests
import sys
import time

# 用法: python fetch_rhea.py [输入原始TSV含Catalytic activity] [chebi_smiles.tsv] [输出文件]
INPUT = sys.argv[1] if len(sys.argv) > 1 else '../uniprotkb_TERPENE_AND_reviewed_true_2026_07_12.tsv'
CHEBI_SMILES_FILE = sys.argv[2] if len(sys.argv) > 2 else 'chebi_data/chebi_smiles.tsv'
OUTPUT = sys.argv[3] if len(sys.argv) > 3 else 'output_rhea.tsv'

SPARQL_URL = 'https://sparql.rhea-db.org/sparql'
SPARQL_BATCH = 50
SPARQL_DELAY = 0.3


# ---- Step 1: parse Catalytic activity from 0712 raw TSV ----
# 每个 CATALYTIC ACTIVITY 单元: Reaction=...; Xref=Rhea:RHEA:xxxxx, ChEBI:...; EC=...; Evidence=...
#                          [PhysiologicalDirection=...; Xref=Rhea:RHEA:yyyyy; Evidence=...]
# 一个 Reaction 块 = 子表一行。Direction 取该块后紧邻的 PhysiologicalDirection(如有)，否则 'not specified'
# 重要不变量: 一个反应在 UniProt CC 里会挂多个 Rhea ID —— Reaction 行上的 RHEA:xxxxx 是通用式
#             (无向主反应)，PhysiologicalDirection 行上的 RHEA:yyyyy 是方向特异式。我们只取通用式
#             (该块第一个 Xref=Rhea:)，即 Rhea ID 列 = 通用式 ID。
def parse_catalytic_activity(cat):
    """Return list of (rhea_id, equation, direction, ec) per reaction block."""
    reactions = []
    for part in cat.split('Reaction=')[1:]:
        # 通用式 Rhea ID: 该块内第一个 Xref=Rhea: (Reaction 行上的主反应)
        m = re.search(r'Xref=Rhea:RHEA:(\d+)', part)
        if not m:
            continue
        rid = f'RHEA:{m.group(1)}'

        # Equation: Reaction= 到第一个 ';' 之间
        eq = part.split(';')[0].strip()

        # EC: EC= 到下一个 ';' 之间
        ec = ''
        m_ec = re.search(r'EC=([^;]+)', part)
        if m_ec:
            ec = m_ec.group(1).strip()

        # Direction: 紧邻本块的第一个 PhysiologicalDirection
        direction = 'not specified'
        m_dir = re.search(r'PhysiologicalDirection=([a-z-]+)', part)
        if m_dir:
            direction = m_dir.group(1)

        reactions.append((rid, eq, direction, ec))

    return reactions

def normalize_ec(ec):
    """EC 归一化：去除 Rhea/UniProt 的 n 级占位后缀（如 1.1.1.n4 -> 1.1.1.），缺失时 '--'。"""
    if not ec:
        return '--'
    return re.sub(r'n\d+$', '', ec)


print('Step 1: parsing Catalytic activity from 0712 raw TSV...')
rows = []          # (entry, rhea_id, equation, direction, ec)
all_rhea = set()
with open(INPUT, 'r', encoding='utf-8') as f:
    for row in csv.DictReader(f, delimiter='\t'):
        entry = row['Entry']
        for rid, eq, direction, ec in parse_catalytic_activity(row.get('Catalytic activity', '')):
            rows.append((entry, rid, eq, direction, ec))
            all_rhea.add(rid)

print(f'  Reactions parsed: {len(rows)}  (unique Rhea IDs: {len(all_rhea)})')


# ---- Step 2: fetch ChEBI equation-order + EC via Rhea SPARQL ----
# 每个反应返回 (side, name, chebi) 侧别 + ec（Rhea 反应关联的 EC，用于 0712 块里缺 EC 时兜底）
def batch_query_sparql(ids):
    values = ' '.join(f'rhea:{rid.split(":")[1]}' for rid in ids)
    query = f'''
    PREFIX rhea: <http://rdf.rhea-db.org/>
    SELECT ?reaction ?side ?name ?chebi ?ec WHERE {{
      VALUES ?reaction {{ {values} }}
      ?reaction rhea:side ?side .
      ?side rhea:contains ?participant .
      ?participant rhea:compound ?compound .
      ?compound rhea:accession ?chebi ;
                rhea:name ?name .
      OPTIONAL {{ ?reaction rhea:ec ?ec . }}
    }}
    '''
    r = requests.get(SPARQL_URL,
                     params={'query': query, 'format': 'json'},
                     headers={'User-Agent': 'Mozilla/5.0'},
                     timeout=60)
    r.raise_for_status()
    out = {}
    seen = {}
    for b in r.json()['results']['bindings']:
        rid = 'RHEA:' + b['reaction']['value'].rstrip('/').split('/')[-1]
        side = b['side']['value'].rstrip('/').split('/')[-1]  # e.g. 54512_L
        item = (side, b['name']['value'], b['chebi']['value'])
        # OPTIONAL ?ec 会让每行绑定重复；按 (rid, side, name, chebi) 去重
        seen.setdefault(rid, set())
        if item not in seen[rid]:
            seen[rid].add(item)
            out.setdefault(rid, {'items': [], 'ec': ''})
            out[rid]['items'].append(item)
        if 'ec' in b and not out.get(rid, {}).get('ec'):
            # rhea:ec 值如 http://purl.uniprot.org/enzyme/4.2.3.228
            out.setdefault(rid, {'items': [], 'ec': ''})
            out[rid]['ec'] = b['ec']['value'].rstrip('/').split('/')[-1]
    return out


print('\nStep 2: fetching ChEBI order + EC from Rhea SPARQL...')
rhea_side = {}  # rhea_id -> {'L': [(name, chebi)...], 'R': [...], 'ec': str}
rhea_list = sorted(all_rhea)
for batch_start in range(0, len(rhea_list), SPARQL_BATCH):
    batch = rhea_list[batch_start:batch_start + SPARQL_BATCH]
    for attempt in range(3):
        try:
            result = batch_query_sparql(batch)
            for rid, info in result.items():
                sides = {'L': [], 'R': [], 'ec': info['ec']}
                for side, name, chebi in info['items']:
                    key = 'L' if side.endswith('_L') else ('R' if side.endswith('_R') else None)
                    if key:
                        sides[key].append((name, chebi))
                rhea_side[rid] = sides
            break
        except Exception as e:
            if attempt < 2:
                time.sleep(3 * (attempt + 1))
            else:
                print(f'  Batch {batch_start} FAILED: {e}')
    print(f'  Progress: {min(batch_start + SPARQL_BATCH, len(rhea_list))}/{len(rhea_list)}')
    if batch_start + SPARQL_BATCH < len(rhea_list):
        time.sleep(SPARQL_DELAY)


def get_sides(rid):
    """Return (left_list, right_list) of ChEBI IDs in SPARQL return order (side内保持返回顺序)."""
    sides = rhea_side.get(rid)
    if not sides:
        return [], []
    return [c for n, c in sides['L']], [c for n, c in sides['R']]


# ---- Step 3: load ChEBI -> SMILES ----
print('\nStep 3: loading ChEBI -> SMILES...')
chebi_smiles = {}
with open(CHEBI_SMILES_FILE, 'r', encoding='utf-8') as f:
    for line in f:
        if line.startswith('CHEBI\t'):
            continue
        parts = line.strip().split('\t', 1)
        if len(parts) == 2:
            chebi_smiles[parts[0]] = parts[1]
print(f'  Loaded {len(chebi_smiles)} ChEBI SMILES')


# ---- Step 4: assemble ChEBI order + reaction SMILES (direction-aware) ----
# ChEBI order 列 = 底物;... | 产物;...（跟随 Direction 列，而非方程式文本）。
# 侧内顺序 = Rhea SPARQL 侧别返回顺序（含 GENERIC 大分子化合物）。
print('Step 4: assembling ChEBI order + reaction SMILES...')
smiles_hit = 0
chebi_hit = 0
for i, (entry, rid, equation, direction, ec) in enumerate(rows):
    left_c, right_c = get_sides(rid)

    # SMILES 底物/产物跟随 Direction 列
    if direction == 'right-to-left':
        sub_c, prod_c = right_c, left_c
    else:  # left-to-right / not specified
        sub_c, prod_c = left_c, right_c

    chebi_order = '; '.join(sub_c) + ' | ' + '; '.join(prod_c) if (sub_c or prod_c) else ''
    if chebi_order:
        chebi_hit += 1

    sub_list = [chebi_smiles.get(c, '') for c in sub_c]
    prod_list = [chebi_smiles.get(c, '') for c in prod_c]
    sub_str = '.'.join(s for s in sub_list if s)
    prod_str = '.'.join(s for s in prod_list if s)
    rxn_smiles = f'{sub_str}>>{prod_str}' if sub_str and prod_str else ''
    if rxn_smiles:
        smiles_hit += 1

    # EC: 0712 块的 EC= 优先，缺失时用 Rhea 反应的 EC，仍缺失则 '--'；统一去 n 后缀
    if not ec:
        ec = rhea_side.get(rid, {}).get('ec', '')
    ec = normalize_ec(ec)

    rid_num = rid.split(':')[1]
    rows[i] = (entry, rid, f'https://www.rhea-db.org/rhea/{rid_num}',
               equation, direction, ec, rxn_smiles, chebi_order)

print(f'  Rows with reaction SMILES: {smiles_hit}/{len(rows)}')
print(f'  Rows with ChEBI order: {chebi_hit}/{len(rows)}')


# ---- Step 5: write ----
print('\nStep 5: writing output...')
with open(OUTPUT, 'w', encoding='utf-8', newline='') as f:
    w = csv.writer(f, delimiter='\t')
    w.writerow(['Entry', 'Rhea ID', 'Rhea Link', 'Equation', 'Direction',
                'EC Number', 'Reaction SMILES', 'ChEBI IDs (equation order)'])
    for entry, rid, link, eq, direction, ec, rxn, chebi in rows:
        w.writerow([entry, rid, link, eq, direction, ec, rxn, chebi])

print(f'Done! {len(rows)} rows -> {OUTPUT}')
