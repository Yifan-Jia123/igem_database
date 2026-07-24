"""
Query parser for entry search.

Supports: bare words, field:value, AND, OR, NOT, and parentheses.
"""

import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple


@dataclass
class SearchCondition:
    field: Optional[str] = None
    value: str = ""

    def __repr__(self):
        if self.field:
            return f"{self.field}:{self.value}"
        return self.value


@dataclass
class SearchClause:
    conditions: List[SearchCondition] = field(default_factory=list)

    def is_empty(self) -> bool:
        return len(self.conditions) == 0


ID_PATTERNS = [
    (r"^ENZ\d+$", "enzyme_id"),
    (r"^EDGE\d+$", "edge_id"),
    (r"^RHEA:\d+$", "rhea_id"),
    (r"^CHEBI:\d+$", "compound_id"),
    (r"^\d+\.\d+\.\d+\.\d+$", "ec_number"),
    (r"^\d+\.\d+\.\d+\.-$", "ec_number"),
    (r"^[A-Z]\d[A-Z0-9]{3}\d+$", "uniprot_id"),
    (r"^\d{7,8}$", "pubmed_id"),
]


def detect_input_type(q: str) -> Optional[str]:
    """Auto-detect the likely field type of a query string."""
    for pattern, field in ID_PATTERNS:
        if re.match(pattern, q.strip(), re.IGNORECASE):
            return field
    return None


_token_pattern = re.compile(r"""(?x)
    \( | \) |
    \bAND\b | \bOR\b | \bNOT\b |
    [^\s()]+
""", re.IGNORECASE)


def tokenize(q: str) -> List[str]:
    return _token_pattern.findall(q)


def parse_query(q: str) -> List[SearchClause]:
    """
    Parse user query into OR-of-ANDs normal form.

    Returns list of SearchClause where each clause is AND-ed conditions,
    and clauses are OR-ed together.
    """
    q = q.strip()
    if not q:
        return []

    tokens = tokenize(q)
    clauses, pos = _parse_or(tokens, 0)

    if pos < len(tokens):
        remaining = " ".join(tokens[pos:])
        clauses.append(SearchClause(conditions=[SearchCondition(value=remaining)]))

    filtered = [c for c in clauses if not c.is_empty()]
    return filtered if filtered else [SearchClause(conditions=[SearchCondition(value=q)])]


def _parse_or(tokens: List[str], pos: int) -> Tuple[List[SearchClause], int]:
    clauses: List[SearchClause] = []
    clause, pos = _parse_and(tokens, pos)
    if not clause.is_empty():
        clauses.append(clause)

    while pos < len(tokens) and tokens[pos].upper() == "OR":
        pos += 1
        clause, pos = _parse_and(tokens, pos)
        if not clause.is_empty():
            clauses.append(clause)

    return clauses, pos


def _parse_and(tokens: List[str], pos: int) -> Tuple[SearchClause, int]:
    clause = SearchClause()
    cond, pos = _parse_factor(tokens, pos)
    if cond is not None:
        clause.conditions.append(cond)

    while pos < len(tokens) and tokens[pos].upper() == "AND":
        pos += 1
        cond, pos = _parse_factor(tokens, pos)
        if cond is not None:
            clause.conditions.append(cond)

    return clause, pos


def _parse_factor(tokens: List[str], pos: int) -> Tuple[Optional[SearchCondition], int]:
    if pos >= len(tokens):
        return None, pos

    token = tokens[pos]

    if token.upper() == "NOT":
        pos += 1
        cond, pos = _parse_factor(tokens, pos)
        if cond:
            return SearchCondition(field=cond.field, value=cond.value), pos
        return None, pos

    if token == "(":
        pos += 1
        inner_clauses, pos = _parse_or(tokens, pos)
        if pos < len(tokens) and tokens[pos] == ")":
            pos += 1

        if inner_clauses:
            conditions = []
            for clause in inner_clauses:
                conditions.extend(clause.conditions)
            if conditions:
                merged = SearchClause(conditions=conditions)
                inner_clauses = [merged]

        return _clause_as_condition(inner_clauses), pos

    if _is_operator(token):
        return None, pos

    pos += 1

    if ":" in token and not token.startswith(":"):
        parts = token.split(":", 1)
        field = parts[0].lower()
        value = parts[1]
        return SearchCondition(field=field, value=value), pos

    return SearchCondition(value=token), pos


def _is_operator(token: str) -> bool:
    return token.upper() in ("AND", "OR", "NOT")


def _clause_as_condition(clauses: List[SearchClause]) -> Optional[SearchCondition]:
    """Convert parsed clauses into a single condition for parent context."""
    if not clauses:
        return None
    if len(clauses) == 1 and len(clauses[0].conditions) == 1:
        c = clauses[0].conditions[0]
        return SearchCondition(field=c.field, value=c.value)
    return None
