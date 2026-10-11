"""The single registry for source segments.

A segment boundary is "whether a row corresponds to one UniProt entry": entry-level steps are produced per source segment,
while cross-entry aggregation steps (compounds / substrate-product pairs) are produced merged. When adding a source, **change only this file** --
download_uniprot.py and run_all.py both read from here.

Why there must be only one copy: if two places each wrote their own, missing an update in one when adding a source would make
run_all.py --merge's safety valve think "all sources are present" and emit a merged table missing that source,
overwriting the previously complete version -- the only remaining hole in the whole segmentation design through which data can be silently lost.

Order is priority: when the same accession appears in several sources at once, the one listed earlier wins
(SwissProt first, see the plan's "accession promotion/demotion").
"""

SOURCES = ('swiss_prot', 'trembl')

# There is **only one** query term, covering the whole query set. Segmentation happens locally by the `Reviewed` column, not by splitting the query term on reviewed.
# --query= can override it (used to slice small pieces during a pilot).
#
# Why no longer "one term per source" (`(terpene) AND reviewed:true` / `reviewed:false` fetched separately each time):
# that is **two time windows**. A promotion/demotion across the windows lets the same accession land in both segments, or be missed by both,
# while this approach's premise is that "segments must be re-produced as a group" (see the plan). One query term = one snapshot,
# so that premise becomes enforced by the script, no longer relying on the discipline of "remember to change both terms together".
ALL_QUERY = '(terpene)'
