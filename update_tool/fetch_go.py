"""
Regenerate the GO child table (uniprotkb_go.tsv) from the unified raw TSV.
Format: Entry, GO ID, GO Term, GO Link

Data source: the unified raw TSV's 'Gene Ontology (biological process)' column (the go_p field).
The old uniprotkb_go.tsv stored only biological-process (BP) GO terms, and that column is exactly BP-only:
  - real rows: the go_p parse matches the old table's 2698 real rows cell for cell (0 diff)
  - empty placeholder rows: a row is added for enzymes with no BP GO (GO ID/Term/Link all empty), guaranteeing at least one row per enzyme, same structure as the old table
  - Note: a few no-GO enzymes had 2-7 empty placeholder rows in the old table (the historical build script emitted empty rows per the API's GO-reference count at the time;
    the source data has changed, now unified to one empty placeholder per enzyme). rebuild_master filters out rows with empty GO ID when reading the child table.

No longer hits the UniProt API: GO cross-references are already in the exported go_p column, matching the "API only fills what the export lacks" policy,
and offline parsing is more deterministic.

Usage: python fetch_go.py [input raw TSV] [output file]
"""
import csv
import re
import sys

# Usage: python fetch_go.py [input raw TSV] [output file]
INPUT = sys.argv[1] if len(sys.argv) > 1 else 'output_uniprot_unified.tsv'
OUTPUT = sys.argv[2] if len(sys.argv) > 2 else 'output_go.tsv'

# Fixed header name (the display name of the go_p field in UniProt TSV)
GO_COL = 'Gene Ontology (biological process)'

# ---- collect entries and their BP GO terms from the raw TSV ----
go_by_entry = {}
# Entry -> Source. 'Source' is written in the last column of the RAW unified table by download_uniprot.py,
# and carried along with the entry here; otherwise downstream cannot tell which source segment this GO belongs to.
source_by_entry = {}
with open(INPUT, 'r', encoding='utf-8') as f:
    for row in csv.DictReader(f, delimiter='\t'):
        e = row['Entry']
        source_by_entry[e] = (row.get('Source') or '').strip()
        gos = []
        col = row.get(GO_COL, '') or ''
        for t in col.split(';'):
            t = t.strip()
            if not t:
                continue
            m = re.search(r'\[(GO:\d+)\]$', t)
            term = t[:m.start()].strip() if m else t
            goid = m.group(1) if m else ''
            if goid:
                gos.append({
                    'go_id': goid,
                    'term': term,
                    'link': f'https://www.ebi.ac.uk/QuickGO/term/{goid}',
                })
        go_by_entry[e] = gos

# ---- write: real GO rows + at least one row per enzyme (a no-GO enzyme gets one empty placeholder, same structure as the old table) ----
with open(OUTPUT, 'w', encoding='utf-8', newline='') as f:
    w = csv.writer(f, delimiter='\t')
    w.writerow(['Entry', 'GO ID', 'GO Term', 'GO Link', 'Source'])
    for entry in go_by_entry:
        gos = go_by_entry[entry]
        src = source_by_entry.get(entry, '')
        if gos:
            for g in gos:
                w.writerow([entry, g['go_id'], g['term'], g['link'], src])
        else:
            # Empty placeholder: a no-BP-GO enzyme (row order same as the raw table). Source must still be written, or the placeholder row
            # cannot be attributed when replacing by source, and would be swept away by another source's refresh.
            w.writerow([entry, '', '', '', src])

# ---- stats ----
total_go = sum(len(v) for v in go_by_entry.values())
with_go = sum(1 for v in go_by_entry.values() if v)
empty = len(go_by_entry) - with_go
print(f'\nDone: {len(go_by_entry)} entries, {total_go} real GO terms (BP-only), '
      f'{with_go} entries with GO, {empty} empty placeholder rows -> {OUTPUT}')
