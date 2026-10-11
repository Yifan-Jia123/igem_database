"""Multi-value field in a TSV cell -> list of values. One implementation, shared by
both readers.

## Contract

[build_terpene_pairs.py:65](../update_tool/build_terpene_pairs.py#L65) joins several
Rhea IDs of the same enzyme into one cell:

    row[f'Rhea ID_{i}'] = '; '.join(sorted({r for r, _ in rxns}, key=rhea_key))

So `Rhea ID_{i}` is a **multi-value cell**, not one ID. Every reader must split it,
otherwise:

  - [etl_edges.py](etl_edges.py)        -- looking up the whole string in reaction_map
                                    always misses -> **edges silently dropped**
  - [etl_search_index.py](etl_search_index.py) -- the whole string is used as entity_id,
                                    so the index gains a fake entity that can never
                                    match, while none of the enzyme's real reactions
                                    reach the index

Measured on the current table `for_graph/uniprotkb_terpene_pairs.tsv`: **42** such
cells, spanning 8 rows / 36 enzymes.

## Why this lives here

The repo's existing separator spelling for this kind of column is `;` with no space
([etl_enzymes.py:39](etl_enzymes.py#L39),
[etl_reactions.py:25](etl_reactions.py#L25)), while the pairs table uses `'; '`. If the
separator ever changes, both readers must change together -- hard-coding it in two
places drifts. One copy is enough.
"""


def split_multi_value(value):
    """Split a `'; '`-joined multi-value cell. Empty / NaN returns [].

    Callers can pass DataFrame raw values directly: NaN is caught here along with
    everything else (it is `!=` itself, so no pandas import is needed -- turning it
    into a string like 'nan' would miss the lookup, which is exactly the silent loss
    this module exists to eliminate).
    """
    if value is None:
        return []
    if value != value:  # NaN
        return []
    text = str(value).strip()
    if not text:
        return []
    return [part.strip() for part in text.split(';') if part.strip()]
