"""
Generate the names_split child table (uniprotkb_names_split.tsv).
Format: Entry, UniProt Link, Entry Name, Organism, Recommended name, Alternative names, Gene Names

Source: parse_names.py's output uniprotkb_terpene_parsed.tsv
  - Recommended name / Alternative names use the same logic as rebuild_master.py: recommended name + first EC,
    alternative names paired with ECs in order (the EC comes from the 'EC numbers' field split out by parse_names)
  - Entry Name / Organism / Gene Names are taken straight from parsed
"""
import csv
import sys

# Usage: python build_names_split.py [parsed.tsv] [output file]
INPUT = sys.argv[1] if len(sys.argv) > 1 else 'uniprotkb_terpene_parsed.tsv'
OUTPUT = sys.argv[2] if len(sys.argv) > 2 else 'output_names_split.tsv'


def build_name_fields(rec_name, alt_names_str, ec_str):
    """Recommended name / alternative name + EC combination logic, identical to rebuild_master.py."""
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
            # Source is passed through from RAW by parse_names.py (empty if the upstream column is missing).
            'Source': (row.get('Source') or '').strip(),
        })

# The old table is ordered by ascending Entry
rows.sort(key=lambda r: r['Entry'])

# 'Source' goes in the last column. etl_enzymes' base enzyme set is taken from this table, so source attribution must be available here.
fields = ['Entry', 'UniProt Link', 'Entry Name', 'Organism',
          'Recommended name', 'Alternative names', 'Gene Names', 'Source']

with open(OUTPUT, 'w', encoding='utf-8', newline='') as f:
    w = csv.DictWriter(f, delimiter='\t', fieldnames=fields)
    w.writeheader()
    w.writerows(rows)

print(f'Done! {len(rows)} rows -> {OUTPUT}')
