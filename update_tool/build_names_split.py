"""
Generate the names_split child table (uniprotkb_names_split.tsv).
Format: Entry, UniProt Link, Entry Name, Organism, Recommended name, Alternative names, Gene Names

Source: parse_names.py 的产物 uniprotkb_terpene_parsed.tsv
  - Recommended name / Alternative names 与 rebuild_master.py 同一逻辑：推荐名+首个 EC，
    替代名依次配 EC（EC 来自 parse_names 拆出的 'EC numbers' 字段）
  - Entry Name / Organism / Gene Names 直接取自 parsed
"""
import csv
import sys

# 用法: python build_names_split.py [parsed.tsv] [输出文件]
INPUT = sys.argv[1] if len(sys.argv) > 1 else 'uniprotkb_terpene_parsed.tsv'
OUTPUT = sys.argv[2] if len(sys.argv) > 2 else 'output_names_split.tsv'


def build_name_fields(rec_name, alt_names_str, ec_str):
    """与 rebuild_master.py 完全一致的推荐名/替代名 + EC 组合逻辑。"""
    alt_names_list = [n.strip() for n in alt_names_str.split(';') if n.strip()]
    ecs = [ec.strip().replace('EC ', '') for ec in ec_str.split(';') if ec.strip()]

    if ecs:
        rec_full = f'{rec_name} (EC {ecs[0]})'
    else:
        rec_full = rec_name

    alt_parts = []
    for i, alt in enumerate(alt_names_list):
        ec_idx = min(i + 1, len(ecs) - 1) if len(ecs) > 1 else 0
        if ecs:
            alt_parts.append(f'{alt} (EC {ecs[ec_idx]})')
        else:
            alt_parts.append(alt)

    return rec_full, '; '.join(alt_parts)


rows = []
with open(INPUT, 'r', encoding='utf-8') as f:
    for row in csv.DictReader(f, delimiter='\t'):
        entry = row['Entry']
        rec_full, alt_full = build_name_fields(
            row.get('Recommended name', ''),
            row.get('Alternative names', ''),
            row.get('EC numbers', ''),
        )
        rows.append({
            'Entry': entry,
            'UniProt Link': f'https://www.uniprot.org/uniprotkb/{entry}',
            'Entry Name': row.get('Entry Name', ''),
            'Organism': row.get('Organism', ''),
            'Recommended name': rec_full,
            'Alternative names': alt_full,
            'Gene Names': row.get('Gene Names (primary)', ''),
        })

# 旧表按 Entry 升序排列
rows.sort(key=lambda r: r['Entry'])

fields = ['Entry', 'UniProt Link', 'Entry Name', 'Organism',
          'Recommended name', 'Alternative names', 'Gene Names']

with open(OUTPUT, 'w', encoding='utf-8', newline='') as f:
    w = csv.DictWriter(f, delimiter='\t', fieldnames=fields)
    w.writeheader()
    w.writerows(rows)

print(f'Done! {len(rows)} rows -> {OUTPUT}')
