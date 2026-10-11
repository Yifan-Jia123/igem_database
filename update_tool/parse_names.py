import csv
import re
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from protein_name import split_recommended_name  # noqa: E402

# Usage: python parse_names.py [raw TSV] [output parsed.tsv]
INPUT = sys.argv[1] if len(sys.argv) > 1 else '../uniprotkb_terpene_AND_reviewed_true_2026_07_10.tsv'
OUTPUT = sys.argv[2] if len(sys.argv) > 2 else 'output_parsed.tsv'

rows = []
with open(INPUT, 'r', encoding='utf-8') as f:
    reader = csv.DictReader(f, delimiter='\t')
    fieldnames = reader.fieldnames
    for row in reader:
        raw = row.get('Protein names', '')

        # ---- 1. extract the recommended name ----
        # The split logic is extracted to protein_name.py, shared with build_rhea_summary /
        # build_enzyme_merged: three call sites used to inline their own versions, with even
        # divergent behavior (only this one stripped [Includes:]); changing one left the other two behind.
        rec_name = split_recommended_name(raw)

        # ---- 2. extract everything inside top-level parentheses (handles nesting like ((2E,6E)-farnesyl...) ----
        paren_items = []
        depth = 0
        start = -1
        for i, ch in enumerate(raw):
            if ch == '(':
                if depth == 0:
                    start = i
                depth += 1
            elif ch == ')':
                depth -= 1
                if depth == 0 and start >= 0:
                    paren_items.append(raw[start+1:i])
                    start = -1

        # ---- 3. classify: EC number vs alias ----
        # Stereochemistry prefixes such as (-), (+), (E), (4S), (E,E), (2Z,6E) are caught by the
        # depth parser as parenthesis content; they are not enzyme names and must be filtered out.
        def _is_stereochem(s):
            s = s.strip()
            if not s:
                return True
            # pure optical-rotation markers: -, +, ±
            if re.match(r'^[\-+±]$', s):
                return True
            # pure numbers: position numbers in chemical names, e.g. the 14 in 2,10(14)-diene
            if re.match(r'^\d{1,3}$', s):
                return True
            # stereo descriptors: E, Z, R, S, possibly numbered like 4S, 2E,6E, 3R:5R
            if re.match(r'^\d*[ERSZ]([,:]\d*[ERSZ])*$', s):
                return True
            return False

        ec_list = []
        alt_names = []
        for item in paren_items:
            if re.match(r'EC\s+[\d\-n]+\.[\d\-n]+\.[\d\-n]+\.[\d\-n]+', item):
                ec_list.append(item)
            elif not _is_stereochem(item):
                alt_names.append(item)

        row['Recommended name'] = rec_name
        row['Alternative names'] = '; '.join(alt_names)
        row['EC numbers'] = '; '.join(ec_list)
        rows.append(row)

# Re-order the columns
# 'Source' is written into the RAW unified table by download_uniprot.py and passed through here
# (extrasaction='ignore' means unlisted columns are dropped -- omitting it would leave every downstream script without the source).
new_fields = ['Entry', 'Recommended name', 'Alternative names', 'EC numbers',
              'Entry Name', 'Organism', 'Gene Names (primary)',
              'Kinetics', 'Function [CC]', 'Rhea ID', 'Gene Ontology (biological process)',
              'Source']

with open(OUTPUT, 'w', encoding='utf-8', newline='') as f:
    writer = csv.DictWriter(f, fieldnames=new_fields, delimiter='\t', extrasaction='ignore')
    writer.writeheader()
    writer.writerows(rows)

print(f'Done! {len(rows)} rows written to {OUTPUT}')
print()
print('Sample:')
print('-' * 80)
for row in rows[:3]:
    print(f"Entry:     {row['Entry']}")
    print(f"Recommended: {row['Recommended name']}")
    print(f"Alt names:   {row['Alternative names']}")
    print(f"EC numbers:  {row['EC numbers']}")
    print('-' * 40)
