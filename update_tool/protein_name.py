"""UniProt 'Protein names' column -> recommended name. Single implementation, shared by three callers.

## Why it must be shared

parse_names.py / build_rhea_summary.py / build_enzyme_merged.py each inlined a copy of the
splitting logic, and **the behaviour differed** (only parse_names stripped `[Includes: ...]`).
Changing one copy left the other two behind, so the same column split into different names in
different output tables -- and master lays these tables side by side, freezing the difference
into a contradiction.

## Why you cannot simply split at the first '('

Many terpene enzyme names start with a stereochemistry prefix: `(+)-`, `(-)-`, `(E)-`, `(E,E)-`, `(2E,6E)-`, `(4S)-`, `(±)-`.
Splitting at the first '(' turns these names into an empty string or a lone `+`. Measured on the
TrEMBL slice: **1.9% of 4727 rows hit this**, and TrEMBL auto-annotated names are mostly this
shape, so at full scale it blanks out large areas.

Criteria:
  1. Prefer splitting before `(EC x.x.x.x` -- this keeps the prefix of
     `(+)-delta-cadinene synthase (EC 4.2.3.13)` intact.
  2. With no EC, split before the first `(` that has **whitespace ahead of it**, skipping
     stereochemistry prefixes. The whitespace condition tells the two kinds of parentheses
     apart: the one in `... ntnL (FMN reductase)` is an alias separator; the one in
     `NAD(P)H-dependent` is part of the formula, and splitting there yields `NAD`.
  3. Fallback: if the result is still empty while the raw string is not, return the raw string.
     A name with some impurities is better than turning "has a name" into "has no name".
"""

import re

# Stereochemistry descriptors: -, +, ± / E, Z, R, S and their combinations and numbering (4S, 2E,6E, 3R:5R)
_STEREOCHEM = re.compile(r'^[\-+±]$|^\d*[ERSZ]([,:]\d*[ERSZ])*$')
# Name-separator parenthesis = a '(' with **whitespace ahead of it**. Paren group up to 20 chars.
# The whitespace condition is key: it tells the two kinds of parentheses apart:
#   'Squalene synthase (SQS)'      -> whitespace + '(' = alias separator, safe to split
#   'NAD(P)H-dependent reductase'  -> letter ahead = part of the formula, must not split
# Using only "the first '('" turns the latter into 'NAD'.
_SEP_PAREN = re.compile(r'\s\(([^()]{1,20})\)')
# '(EC 1.1.1.1' -- matches only the number, the trailing ')' is optional
_EC_SPLIT = re.compile(r'\s*\(EC\s+\d+\.\d+\.\d+')
_INCLUDES = re.compile(r'\s*\[Includes:.*')


def _first_name_separator(raw):
    """Return the start index of the first alias-separator parenthesis that is not a
    stereochemistry prefix, or -1."""
    for m in _SEP_PAREN.finditer(raw):
        # The stereochemistry prefix is part of the name itself ((+)-delta-cadinene synthase),
        # so it must not be cut as an alias separator or the prefix is lost.
        if _STEREOCHEM.match(m.group(1)):
            continue
        return m.start()
    return -1


def split_recommended_name(raw):
    """'Protein names' raw string -> recommended name. Returns '' when raw is empty."""
    if not raw:
        return ''

    # 1) Prefer splitting before (EC x.x.x.x) -- the most reliable way when an EC is present
    parts = _EC_SPLIT.split(raw, maxsplit=1)
    if len(parts) > 1:
        rec = parts[0]
    else:
        # 2) No EC: split before the first parenthesis that is truly an alias separator
        cut = _first_name_separator(raw)
        rec = raw[:cut] if cut > 0 else raw

    rec = _INCLUDES.sub('', rec).strip().rstrip(',').rstrip()

    # 3) Fallback: if it got cut to nothing, fall back to a reasonable form of the raw string.
    #    A name with some impurities is better than turning "has a name" into "has no name".
    if not rec:
        rec = _INCLUDES.sub('', raw).strip().rstrip(',').rstrip()
    return rec
