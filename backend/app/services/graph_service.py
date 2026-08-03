from typing import List, Dict, Set, Tuple, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.models import (
    Compound, Enzyme, Reaction,
    ReactionCompound, EnzymeReactionEdge,
)
from app.schemas.graph import GraphPayload, ReactionEdge, EdgeGroup, FocusPoint
from app.schemas.compound import CompoundCard
from app.schemas.enzyme import EnzymeCard
from app.utils.compound_filters import displayable_compound_filters


DIRECTION_ALLOWS_SUBSTRATE_TO_PRODUCT = {"forward", "reversible", "unknown"}
DIRECTION_ALLOWS_PRODUCT_TO_SUBSTRATE = {"reverse", "reversible", "unknown"}


async def build_graph_payload(
    db: AsyncSession,
    center_compound_id: Optional[str] = None,
    depth: int = 2,
    limit_nodes: Optional[int] = None,
    source_types: Optional[List[str]] = None,
    review_statuses: Optional[List[str]] = None,
) -> GraphPayload:

    # 1. Select center compound
    if center_compound_id:
        result = await db.execute(
            select(Compound).where(Compound.compound_id == center_compound_id)
            .where(*displayable_compound_filters())
        )
        center = result.scalar()
    else:
        center = await _pick_default_center(db)

    if not center:
        return GraphPayload()

    # 2. BFS traversal
    compound_ids, edge_records = await _bfs_subgraph(
        db, center.compound_id, depth, source_types, review_statuses
    )

    # 3. Build compound cards
    compounds = await _fetch_compounds(db, compound_ids)
    cards = [_compound_to_card(c) for c in compounds]
    card_map = {c.compound_id: c for c in cards}

    # 4. Build edges and edge groups
    edges, edge_groups = _build_edges_and_groups(edge_records, card_map)

    # 5. Limit nodes if needed
    if limit_nodes and len(cards) > limit_nodes:
        cards = cards[:limit_nodes]

    return GraphPayload(
        nodes=cards,
        edges=edges,
        edge_groups=edge_groups,
        focus=FocusPoint(node_id=center.compound_id),
    )


async def _pick_default_center(db: AsyncSession) -> Optional[Compound]:
    result = await db.execute(
        select(ReactionCompound.compound_id, func.count().label("cnt"))
        .join(Compound, ReactionCompound.compound_id == Compound.compound_id)
        .where(*displayable_compound_filters())
        .group_by(ReactionCompound.compound_id)
        .order_by(func.count().desc())
        .limit(1)
    )
    row = result.first()
    if not row:
        return None
    result = await db.execute(
        select(Compound).where(Compound.compound_id == row[0])
    )
    return result.scalar()


async def _bfs_subgraph(
    db: AsyncSession,
    center_id: str,
    depth: int,
    source_types: Optional[List[str]],
    review_statuses: Optional[List[str]],
) -> Tuple[Set[str], List[dict]]:
    """BFS from center_id, returns (set of compound_ids, list of edge records)."""

    visited_compounds: Set[str] = {center_id}
    edge_records: List[dict] = []

    frontier: List[Tuple[str, int]] = [(center_id, 0)]

    while frontier:
        current_id, current_depth = frontier.pop(0)
        if current_depth >= depth:
            continue

        # All reaction_compound rows for this compound
        rc_query = select(ReactionCompound, Reaction).join(
            Reaction, ReactionCompound.reaction_id == Reaction.reaction_id
        ).where(ReactionCompound.compound_id == current_id)

        if source_types:
            rc_query = rc_query.where(Reaction.source_type.in_(source_types))
        if review_statuses:
            rc_query = rc_query.where(Reaction.review_status.in_(review_statuses))

        result = await db.execute(rc_query)
        rc_reaction_pairs = result.all()

        # Collect all reaction IDs to batch-query compounds and edges
        reaction_ids = list({
            rxn.reaction_id for _, rxn in rc_reaction_pairs
        })

        if not reaction_ids:
            continue

        # All compounds in these reactions (batch)
        all_rc_query = select(ReactionCompound).where(
            ReactionCompound.reaction_id.in_(reaction_ids)
        )
        rc_result = await db.execute(all_rc_query)
        all_rcs = rc_result.scalars().all()
        displayable_compound_ids = await _fetch_displayable_compound_ids(
            db, {rc.compound_id for rc in all_rcs}
        )

        # Group by reaction_id
        rxn_compounds: Dict[str, Tuple[List[str], List[str]]] = {}
        for rc in all_rcs:
            if rc.compound_id not in displayable_compound_ids:
                continue
            if rc.reaction_id not in rxn_compounds:
                rxn_compounds[rc.reaction_id] = ([], [])
            if rc.role.value == "substrate":
                rxn_compounds[rc.reaction_id][0].append(rc.compound_id)
            else:
                rxn_compounds[rc.reaction_id][1].append(rc.compound_id)

        # All edges for these reactions (batch)
        edge_query = select(EnzymeReactionEdge, Enzyme).join(
            Enzyme, EnzymeReactionEdge.enzyme_id == Enzyme.enzyme_id
        ).where(EnzymeReactionEdge.reaction_id.in_(reaction_ids))

        if source_types:
            edge_query = edge_query.where(EnzymeReactionEdge.source_type.in_(source_types))
        if review_statuses:
            edge_query = edge_query.where(EnzymeReactionEdge.review_status.in_(review_statuses))

        edge_result = await db.execute(edge_query)
        edge_rows = edge_result.all()

        # Group edges by reaction_id
        rxn_edges: Dict[str, List[Tuple[EnzymeReactionEdge, Enzyme]]] = {}
        for ere, enz in edge_rows:
            if ere.reaction_id not in rxn_edges:
                rxn_edges[ere.reaction_id] = []
            rxn_edges[ere.reaction_id].append((ere, enz))

        # Determine outgoing edges from current compound
        for rc, reaction in rc_reaction_pairs:
            substrates, products = rxn_compounds.get(reaction.reaction_id, ([], []))
            direction = reaction.direction.value if reaction.direction else "unknown"

            targets = []
            if rc.role.value == "substrate" and direction in DIRECTION_ALLOWS_SUBSTRATE_TO_PRODUCT:
                targets = products
            elif rc.role.value == "product" and direction in DIRECTION_ALLOWS_PRODUCT_TO_SUBSTRATE:
                targets = substrates

            for target_id in targets:
                if target_id not in displayable_compound_ids:
                    continue
                # Record the edge(s)
                for ere, enz in rxn_edges.get(reaction.reaction_id, []):
                    # Avoid duplicate edges for same traversal
                    edge_records.append({
                        "from_cpd": current_id,
                        "to_cpd": target_id,
                        "edge_id": ere.edge_id,
                        "enzyme_id": ere.enzyme_id,
                        "enzyme": enz,
                        "reaction_id": reaction.reaction_id,
                        "reaction": reaction,
                        "direction": direction,
                        "source_type": ere.source_type.value if ere.source_type else "swiss_prot",
                        "review_status": ere.review_status.value if ere.review_status else "official",
                    })

                # Add target to visited and frontier
                if target_id not in visited_compounds:
                    visited_compounds.add(target_id)
                    if current_depth + 1 < depth:
                        frontier.append((target_id, current_depth + 1))

    return visited_compounds, edge_records


async def _fetch_compounds(db: AsyncSession, compound_ids: Set[str]) -> List[Compound]:
    result = await db.execute(
        select(Compound).where(Compound.compound_id.in_(list(compound_ids)))
        .where(*displayable_compound_filters())
    )
    return list(result.scalars().all())


async def _fetch_displayable_compound_ids(db: AsyncSession, compound_ids: Set[str]) -> Set[str]:
    if not compound_ids:
        return set()

    result = await db.execute(
        select(Compound.compound_id)
        .where(Compound.compound_id.in_(list(compound_ids)))
        .where(*displayable_compound_filters())
    )
    return {row[0] for row in result.all()}


def _compound_to_card(c: Compound) -> CompoundCard:
    return CompoundCard(
        compound_id=c.compound_id,
        name=c.name,
        chebi_id=c.chebi_id,
        smiles=c.smiles,
        formula=c.formula,
        charge=float(c.charge) if c.charge else None,
        average_mass=float(c.average_mass) if c.average_mass else None,
        inchi=c.inchi,
        structure_image_url=c.structure_image_url,
        chebi_url=c.chebi_url,
        description=c.description,
    )


def _build_edges_and_groups(
    edge_records: List[dict],
    card_map: Dict[str, CompoundCard],
) -> Tuple[List[ReactionEdge], List[EdgeGroup]]:
    """Group edges by (source, target) to detect overlaps."""

    # Deduplicate edge records by (from_cpd, to_cpd, enzyme_id, reaction_id)
    seen = set()
    unique_records = []
    for rec in edge_records:
        key = (rec["from_cpd"], rec["to_cpd"], rec["enzyme_id"], rec["reaction_id"])
        if key not in seen:
            seen.add(key)
            unique_records.append(rec)

    # Group by (from_cpd, to_cpd)
    groups: Dict[Tuple[str, str], List[dict]] = {}
    for rec in unique_records:
        key = (rec["from_cpd"], rec["to_cpd"])
        if key not in groups:
            groups[key] = []
        groups[key].append(rec)

    edges: List[ReactionEdge] = []
    edge_groups: List[EdgeGroup] = []

    for (from_cpd, to_cpd), recs in groups.items():
        group_id = f"GROUP_{from_cpd}_{to_cpd}"

        if len(recs) == 1:
            # Single edge: put in edges[]
            rec = recs[0]
            enz = rec["enzyme"]
            label = enz.uniprot_id or enz.enzyme_id

            edges.append(ReactionEdge(
                edge_id=rec["edge_id"],
                edge_group_id=group_id,
                reaction_id=rec["reaction_id"],
                enzyme_id=rec["enzyme_id"],
                source_compound_id=from_cpd,
                target_compound_id=to_cpd,
                label=label,
                direction=rec["direction"],
                source_type=rec["source_type"],
                review_status=rec["review_status"],
                card=_make_enzyme_card(
                    rec, from_cpd, to_cpd, rec["direction"],
                    recs[0]["reaction"],
                ),
            ))
        else:
            # Multiple edges: put in edgeGroups[]
            edge_ids = [r["edge_id"] for r in recs]
            enz_count = len(set(r["enzyme_id"] for r in recs))
            label = f"{enz_count}×enzyme" if enz_count > 1 else recs[0]["enzyme"].uniprot_id or recs[0]["enzyme"].enzyme_id

            edge_groups.append(EdgeGroup(
                edge_group_id=group_id,
                source_compound_id=from_cpd,
                target_compound_id=to_cpd,
                label=label,
                count=len(recs),
                edge_ids=edge_ids,
            ))

    return edges, edge_groups


def _make_enzyme_card(
    rec: dict,
    source_compound_id: str,
    target_compound_id: str,
    direction: str,
    reaction,
) -> EnzymeCard:
    enz = rec["enzyme"]
    return EnzymeCard(
        edge_id=rec["edge_id"],
        enzyme_id=enz.enzyme_id,
        primary_name=enz.primary_name,
        uniprot_id=enz.uniprot_id,
        database_code=enz.enzyme_id,
        organism_name=enz.organism_name,
        ec_number=reaction.ec_number,
        reaction_id=rec["reaction_id"],
        reaction_equation=reaction.equation,
        reaction_direction=direction,
        source_type=rec["source_type"],
        review_status=rec["review_status"],
    )


async def expand_edge_group(
    db: AsyncSession,
    edge_group_id: str,
) -> List[ReactionEdge]:
    """Expand an edge group: return individual ReactionEdge cards from the edge IDs."""

    parts = edge_group_id.split("_", 2)
    if len(parts) < 3 or parts[0] != "GROUP":
        return []

    from_cpd = parts[1]
    to_cpd = parts[2]
    if {from_cpd, to_cpd} != await _fetch_displayable_compound_ids(db, {from_cpd, to_cpd}):
        return []

    # Find all edges between these two compounds
    # Query: enzyme_reaction_edge where source compound is from_cpd and target is to_cpd
    # (This is the reverse of BFS; we query by compound pair)

    # Get reactions involving from_cpd as substrate and to_cpd as product (or vice versa)
    # We need to find reactions where both from_cpd and to_cpd participate

    query = select(ReactionCompound.reaction_id).where(
        ReactionCompound.compound_id.in_([from_cpd, to_cpd])
    ).group_by(ReactionCompound.reaction_id).having(
        func.count(func.distinct(ReactionCompound.compound_id)) == 2
    )
    result = await db.execute(query)
    reaction_ids = [r[0] for r in result.all()]

    if not reaction_ids:
        return []

    # Get reaction details (with direction)
    rxn_query = select(Reaction).where(Reaction.reaction_id.in_(reaction_ids))
    rxn_result = await db.execute(rxn_query)
    reaction_map = {r.reaction_id: r for r in rxn_result.scalars().all()}

    # Get edges
    edge_query = select(EnzymeReactionEdge, Enzyme).join(
        Enzyme, EnzymeReactionEdge.enzyme_id == Enzyme.enzyme_id
    ).where(EnzymeReactionEdge.reaction_id.in_(reaction_ids))

    edge_result = await db.execute(edge_query)
    edge_rows = edge_result.all()

    # Get reaction_compound data to determine direction
    rc_query = select(ReactionCompound).where(
        ReactionCompound.reaction_id.in_(reaction_ids)
    )
    rc_result = await db.execute(rc_query)
    all_rcs = rc_result.scalars().all()

    # Build compound role lookup per reaction
    rxn_roles: Dict[str, Dict[str, str]] = {}
    for rc in all_rcs:
        if rc.reaction_id not in rxn_roles:
            rxn_roles[rc.reaction_id] = {}
        rxn_roles[rc.reaction_id][rc.compound_id] = rc.role.value

    edges = []
    for ere, enz in edge_rows:
        reaction = reaction_map.get(ere.reaction_id)
        if not reaction:
            continue

        direction = reaction.direction.value if reaction.direction else "unknown"
        label = enz.uniprot_id or enz.enzyme_id

        edges.append(ReactionEdge(
            edge_id=ere.edge_id,
            edge_group_id=edge_group_id,
            reaction_id=ere.reaction_id,
            enzyme_id=ere.enzyme_id,
            source_compound_id=from_cpd,
            target_compound_id=to_cpd,
            label=label,
            direction=direction,
            source_type=ere.source_type.value if ere.source_type else "swiss_prot",
            review_status=ere.review_status.value if ere.review_status else "official",
            card=_make_enzyme_card(
                {
                    "edge_id": ere.edge_id,
                    "enzyme_id": ere.enzyme_id,
                    "enzyme": enz,
                    "reaction_id": ere.reaction_id,
                    "reaction": reaction,
                    "direction": direction,
                    "source_type": ere.source_type.value if ere.source_type else "swiss_prot",
                    "review_status": ere.review_status.value if ere.review_status else "official",
                },
                from_cpd, to_cpd, direction, reaction,
            ),
        ))

    return edges
