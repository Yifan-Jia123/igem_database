"""Entity field assembly + structure-image URL resolution.

Shared by the detail endpoints (`/enzymes/{id}`, `/compounds/{id}/card`,
`/reactions/{id}`) and the `/bundle/*` aggregation endpoints; the router layer only
wraps the envelope.

**Why a separate service**: `CompoundCard` used to be assembled in 4 separate
places in the backend (`routers/enzymes.py`, `routers/compounds.py`,
`routers/reactions.py`, `services/graph_service.py`), and the first two silently
omitted `formula` / `charge` / `inchi` / `description` -- pydantic takes `None` for
missing fields without erroring, so the compound cards on the enzyme and reaction
detail pages were always missing those four fields and nobody noticed. After
collapsing into `compound_to_card` this kind of drift has one place to fix.

**The cost of batching**: a single `load_enzyme_detail` fires `~8 + 2·R` queries
(R = number of reactions) -- enzyme, gene, 4 table-existence probes,
sequence-links, evidence, edges, then one reaction + one reaction-compound per
edge. `build_entity_bundles` only lifts the 4 probes out of the loop and does
**no full prefetch**: that would mean rewriting the enzyme loader into one big JOIN
with grouping in Python, defeating the "reuse the single-row loader" goal. The
cost is capped by `schemas.bundle.MAX_BATCH_ITEMS`; the trade-off is deliberate,
not an oversight.
"""
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple
from urllib.parse import quote

from sqlalchemy import or_, select, text
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Compound,
    Enzyme,
    EnzymeGoTerm,
    EnzymeIsoform,
    EnzymeReactionEdge,
    EnzymeSolubilityScore,
    Evidence,
    Gene,
    GeneSequenceLink,
    Reaction,
    ReactionCompound,
)
from app.schemas.bundle import (
    BUNDLE_KINDS,
    BundleBatchResult,
    BundleData,
    CompoundBundle,
    CompoundImage,
    EnzymeBundle,
    PathwayBundle,
    ReactionBundle,
)
from app.schemas.common import ErrorDetail
from app.schemas.compound import CompoundCard
from app.schemas.enzyme import EnzymeDetail, EnzymeReactionItem, ExternalLink, GoTerm, IsoformSequence
from app.schemas.evidence import EvidenceItem
from app.schemas.gene import GeneSummary, SequenceLink
from app.schemas.reaction import ReactionDetail
from app.services.pathway_service import (
    PATHWAY_BAD_ID,
    PATHWAY_NOT_REACHABLE,
    PATHWAY_UNKNOWN_COMPOUND,
    load_pathway_by_chain,
    parse_pathway_id,
)
from app.utils.compound_card import compound_to_card as build_compound_card
from app.utils.compound_filters import displayable_compound_filters

# This cache-buster string lives in exactly one place. It is **owned by the
# frontend** -- if the frontend bumps to v=5 while this still sends v=4, both must
# change together (three frontend spots: api.ts, graphExperience.tsx,
# components/StructureSearchDrawer.tsx).
STRUCTURE_SUFFIX = "/structure.svg?v=4"
ASSET_PREFIX = "/api/v1/assets/compounds"


# --------------------------------------------------------------------------- assembly

def compound_to_card(c: Compound) -> CompoundCard:
    """See `app.utils.compound_card` -- this is just a re-export for old call sites."""
    return build_compound_card(c)


def _reaction_compound_cards(rows: Iterable[Tuple[ReactionCompound, Compound]]) -> Tuple[List[CompoundCard], List[CompoundCard]]:
    substrates: List[CompoundCard] = []
    products: List[CompoundCard] = []
    for rc, cpd in rows:
        card = compound_to_card(cpd)
        if rc.role.value == "substrate":
            substrates.append(card)
        else:
            products.append(card)
    return substrates, products


async def _fetch_reaction_compound_cards(
    db: AsyncSession, reaction_id: str
) -> Tuple[List[CompoundCard], List[CompoundCard]]:
    result = await db.execute(
        select(ReactionCompound, Compound)
        .join(Compound, ReactionCompound.compound_id == Compound.compound_id)
        .where(ReactionCompound.reaction_id == reaction_id)
        .where(*displayable_compound_filters())
    )
    return _reaction_compound_cards(result.all())


@dataclass(frozen=True)
class EnzymeTableFlags:
    """Whether the four optional tables exist. Probed once per batch, reused in the loop."""

    gene_sequence_link: bool
    enzyme_go: bool
    enzyme_solubility_score: bool
    enzyme_isoform: bool


async def _table_exists(db: AsyncSession, table_name: str) -> bool:
    try:
        result = await db.execute(
            text(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_schema = DATABASE() AND table_name = :table_name"
            ),
            {"table_name": table_name},
        )
        return bool(result.scalar())
    except (ProgrammingError, OperationalError):
        # NOTE: this resets the transaction mid-batch. The batch is all reads, so
        # a rollback loses nothing, but do not reuse this module after a write.
        await db.rollback()
        return False


async def probe_enzyme_tables(db: AsyncSession) -> EnzymeTableFlags:
    return EnzymeTableFlags(
        gene_sequence_link=await _table_exists(db, "gene_sequence_link"),
        enzyme_go=await _table_exists(db, "enzyme_go"),
        enzyme_solubility_score=await _table_exists(db, "enzyme_solubility_score"),
        enzyme_isoform=await _table_exists(db, "enzyme_isoform"),
    )


async def _load_enzyme_detail_with_flags(
    db: AsyncSession, enzyme_id: str, flags: EnzymeTableFlags
) -> Optional[EnzymeDetail]:
    result = await db.execute(select(Enzyme).where(Enzyme.enzyme_id == enzyme_id))
    enz = result.scalar()
    if not enz:
        return None

    enzyme_values = {
        "enzyme_id": enz.enzyme_id,
        "primary_name": enz.primary_name,
        "secondary_names": enz.secondary_names or [],
        "uniprot_id": enz.uniprot_id,
        "organism_name": enz.organism_name,
        "sequence": enz.sequence,
        "length": enz.length,
        "mass": float(enz.mass) if enz.mass is not None else None,
        # The source/review status of the enzyme **itself**. Not the same as the
        # source on each item in reaction_items below: a reaction is a shared
        # objective entity, so etl_reactions stamps every reaction row swiss_prot
        # (see schemas.EnzymeDetail). Takes .value because it is an ENUM on the model.
        "source_type": enz.source_type.value if enz.source_type else None,
        "review_status": enz.review_status.value if enz.review_status else None,
    }

    # Gene
    gene_result = await db.execute(select(Gene).where(Gene.enzyme_id == enzyme_values["enzyme_id"]))
    gene = gene_result.scalar()
    gene_summary = None
    gene_ncbi_url = None
    if gene:
        gene_ncbi_url = gene.ncbi_url
        gene_summary = GeneSummary(
            gene_name=gene.gene_name,
            gene_record_id=str(gene.gene_id),
            genbank_id=gene.genbank_id,
            ncbi_url=gene.ncbi_url,
            ena_accession=gene.ena_accession,
            protein_accession=gene.protein_accession,
        )

    # Sequence links
    sequence_links = []
    if flags.gene_sequence_link:
        seq_link_result = await db.execute(
            select(GeneSequenceLink)
            .where(GeneSequenceLink.enzyme_id == enzyme_values["enzyme_id"])
            .order_by(GeneSequenceLink.link_category, GeneSequenceLink.sequence_link_id)
        )
        sequence_links = [
            SequenceLink(
                category=link.link_category,
                accession=link.accession,
                url=link.url,
                related_accession=link.related_accession,
                related_url=link.related_url,
            )
            for link in seq_link_result.scalars()
        ]

    # Evidence
    ev_result = await db.execute(select(Evidence).where(Evidence.enzyme_id == enzyme_values["enzyme_id"]))
    evidences = [
        EvidenceItem(
            doi=e.doi,
            pubmed_id=e.pubmed_id,
            title=e.title,
            authors=e.authors,
            journal=e.journal,
            volume=e.volume,
            pages=e.pages,
            publication_year=e.publication_year,
            reference_type=e.reference_type,
            positions=e.positions,
            url=e.url,
            source_description=e.source_description,
            review_status=e.review_status.value,
        ) for e in ev_result.scalars()
    ]

    go_terms = []
    if flags.enzyme_go:
        go_result = await db.execute(
            select(EnzymeGoTerm)
            .where(EnzymeGoTerm.enzyme_id == enzyme_values["enzyme_id"])
            .order_by(EnzymeGoTerm.go_record_id)
        )
        go_terms = [
            GoTerm(go_id=go.go_id, go_term=go.go_term, go_url=go.go_url)
            for go in go_result.scalars()
        ]

    # Model reference score + membrane annotation. One query covers both the
    # enzyme-level (canonical) row and the variant rows -- the row with
    # `isoform_id IS NULL` is the canonical sequence's score.
    # Only 30 true variants have a score in the whole database, so
    # score_by_isoform is empty the vast majority of the time.
    enzyme_score = None
    enzyme_membrane = None
    score_by_isoform: dict = {}
    if flags.enzyme_solubility_score:
        sol_result = await db.execute(
            select(EnzymeSolubilityScore)
            .where(EnzymeSolubilityScore.enzyme_id == enzyme_values["enzyme_id"])
            .order_by(EnzymeSolubilityScore.solubility_record_id)
        )
        for sol in sol_result.scalars():
            score = float(sol.deep_solnet_score) if sol.deep_solnet_score is not None else None
            if sol.isoform_id is None:
                enzyme_score = score
                enzyme_membrane = sol.membrane
            else:
                score_by_isoform[sol.isoform_id] = score

    isoforms = []
    if flags.enzyme_isoform:
        isoform_result = await db.execute(
            select(EnzymeIsoform)
            .where(EnzymeIsoform.enzyme_id == enzyme_values["enzyme_id"])
            .order_by(EnzymeIsoform.isoform_record_id)
        )
        isoforms = [
            IsoformSequence(
                isoform_id=iso.isoform_id,
                isoform_length=iso.isoform_length,
                isoform_mass=iso.isoform_mass,
                canonical_sequence=iso.canonical_sequence,
                canonical_length=iso.canonical_length,
                canonical_mass=iso.canonical_mass,
                sequence=iso.sequence,
                # Assemble by isoform_id directly, so the frontend never id-matches.
                deep_solnet_score=score_by_isoform.get(iso.isoform_id),
            )
            for iso in isoform_result.scalars()
        ]

    # Reactions
    edge_result = await db.execute(
        select(EnzymeReactionEdge).where(EnzymeReactionEdge.enzyme_id == enzyme_values["enzyme_id"])
    )
    edges = edge_result.scalars().all()
    reaction_items = []
    for edge in edges:
        rxn_result = await db.execute(
            select(Reaction).where(Reaction.reaction_id == edge.reaction_id)
        )
        rxn = rxn_result.scalar()
        if not rxn:
            continue

        substrates, products = await _fetch_reaction_compound_cards(db, rxn.reaction_id)

        reaction_items.append(EnzymeReactionItem(
            reaction_id=rxn.reaction_id,
            rhea_id=rxn.rhea_id,
            rhea_url=rxn.rhea_url,
            equation=rxn.equation,
            direction=rxn.direction.value,
            ec_number=rxn.ec_number,
            smiles=rxn.smiles,
            atom_map_image_url=rxn.atom_map_image_url,
            substrates=substrates,
            products=products,
            source_type=rxn.source_type.value if rxn.source_type else "swiss_prot",
            review_status=rxn.review_status.value if rxn.review_status else "official",
        ))

    # External links
    links = []
    if enzyme_values["uniprot_id"]:
        links.append(ExternalLink(label="UniProt", url=f"https://www.uniprot.org/uniprotkb/{enzyme_values['uniprot_id']}"))
    if gene_ncbi_url:
        links.append(ExternalLink(label="NCBI", url=gene_ncbi_url))

    return EnzymeDetail(
        enzyme_id=enzyme_values["enzyme_id"],
        database_code=enzyme_values["enzyme_id"],
        primary_name=enzyme_values["primary_name"],
        secondary_names=enzyme_values["secondary_names"],
        uniprot_id=enzyme_values["uniprot_id"],
        uniprot_url=f"https://www.uniprot.org/uniprotkb/{enzyme_values['uniprot_id']}" if enzyme_values["uniprot_id"] else None,
        organism_name=enzyme_values["organism_name"],
        sequence=enzyme_values["sequence"],
        length=enzyme_values["length"],
        mass=enzyme_values["mass"],
        source_type=enzyme_values["source_type"],
        review_status=enzyme_values["review_status"],
        deep_solnet_score=enzyme_score,
        membrane=enzyme_membrane,
        gene=gene_summary,
        sequence_links=sequence_links,
        go_terms=go_terms,
        isoforms=isoforms,
        reactions=reaction_items,
        evidence=evidences,
        links=links,
    )


async def load_enzyme_detail(
    db: AsyncSession,
    enzyme_id: str,
    flags: Optional[EnzymeTableFlags] = None,
) -> Optional[EnzymeDetail]:
    """None means the enzyme does not exist -- the router turns it into NOT_FOUND."""
    if flags is None:
        flags = await probe_enzyme_tables(db)
    return await _load_enzyme_detail_with_flags(db, enzyme_id, flags)


async def load_compound_card(db: AsyncSession, compound_id: str) -> Optional[CompoundCard]:
    result = await db.execute(select(Compound).where(Compound.compound_id == compound_id))
    cpd = result.scalar()
    if not cpd:
        return None
    return compound_to_card(cpd)


async def load_reaction_detail(db: AsyncSession, reaction_id: str) -> Optional[ReactionDetail]:
    result = await db.execute(select(Reaction).where(Reaction.reaction_id == reaction_id))
    rxn = result.scalar()
    if not rxn:
        return None

    substrates, products = await _fetch_reaction_compound_cards(db, rxn.reaction_id)

    return ReactionDetail(
        reaction_id=rxn.reaction_id,
        rhea_id=rxn.rhea_id,
        rhea_url=rxn.rhea_url,
        equation=rxn.equation,
        direction=rxn.direction.value,
        ec_number=rxn.ec_number,
        smiles=rxn.smiles,
        atom_map_image_url=rxn.atom_map_image_url,
        substrates=substrates,
        products=products,
        source_type=rxn.source_type.value if rxn.source_type else "swiss_prot",
        review_status=rxn.review_status.value if rxn.review_status else "official",
    )


async def load_entity(db: AsyncSession, kind: str, entity_id: str):
    if kind == "enzyme":
        return await load_enzyme_detail(db, entity_id)
    if kind == "compound":
        return await load_compound_card(db, entity_id)
    if kind == "reaction":
        return await load_reaction_detail(db, entity_id)
    return None


async def find_compound_id_by_inchikey(db: AsyncSession, key: str) -> Optional[str]:
    """Look up a compound_id by one InChIKey; None when not found.

    Match **across two columns**, for the same reason as in
    `routers/structure_search.py`: `inchi_key` is the official ChEBI value and
    `inchi_key_derived` is RDKit's computed from this row's smiles; the two differ
    in only 2 of 710 comparable records (`CHEBI:231826` / `CHEBI:53643`, where
    ChEBI's own record contradicts itself). Recognizing one column only would make
    a caller that "computed the other key with another tool" find nothing.

    Today **one key hits at most one compound** (measured on 710 keyed compounds:
    0 within-column duplicates, 0 cross-column collisions), so taking the first is
    fine; still ordered by compound_id so the result stays stable if duplicates
    ever do appear.
    """
    result = await db.execute(
        select(Compound.compound_id)
        .where(or_(Compound.inchi_key == key, Compound.inchi_key_derived == key))
        .order_by(Compound.compound_id)
        .limit(1)
    )
    return result.scalar_one_or_none()


# --------------------------------------------------------------------------- image URL

def compound_image_for_card(c: CompoundCard) -> CompoundImage:
    """Verbatim replica of the frontend's three `compoundImageUrl` spots.

    `quote(key, safe="")` encodes `:` as `%3A`, matching `encodeURIComponent`'s
    result. The two differ only on `!*'()`, and those characters never appear in a
    ChEBI id.
    """
    key = c.chebi_id or c.compound_id
    if key and key.startswith("CHEBI:"):
        return CompoundImage(
            compound_id=c.compound_id,
            name=c.name,
            chebi_id=c.chebi_id,
            image_url=f"{ASSET_PREFIX}/{quote(key, safe='')}{STRUCTURE_SUFFIX}",
            source="chebi_proxy",
        )
    return CompoundImage(
        compound_id=c.compound_id,
        name=c.name,
        chebi_id=c.chebi_id,
        image_url=c.structure_image_url,
        source="database" if c.structure_image_url else "none",
    )


def _cards_of(kind: str, entity) -> List[CompoundCard]:
    if kind == "compound":
        return [entity]
    if kind == "reaction":
        return list(entity.substrates) + list(entity.products)
    cards: List[CompoundCard] = []
    for item in entity.reactions:
        cards.extend(item.substrates)
        cards.extend(item.products)
    return cards


def collect_images(kind: str, entity) -> List[CompoundImage]:
    """De-duplicate by compoundId, keeping first-seen order."""
    seen: Dict[str, CompoundImage] = {}
    for card in _cards_of(kind, entity):
        if card.compound_id not in seen:
            seen[card.compound_id] = compound_image_for_card(card)
    return list(seen.values())


async def _load_cards_for_ids(
    db: AsyncSession, compound_ids: Iterable[str]
) -> List[CompoundCard]:
    """Fetch compound cards in the **given order**; misses are skipped.

    A pathway card carries only `compoundIds`, no embedded CompoundCard, so it
    needs one round-trip to the table.
    """
    ids = list(compound_ids)
    if not ids:
        return []
    result = await db.execute(select(Compound).where(Compound.compound_id.in_(ids)))
    by_id = {c.compound_id: compound_to_card(c) for c in result.scalars()}
    return [by_id[cid] for cid in ids if cid in by_id]


async def _images_for(db: AsyncSession, kind: str, entity) -> List[CompoundImage]:
    if kind == "pathway":
        # Chain order = reaction order, which is meaningful, so use its chain order
        # directly instead of collect_images.
        cards = await _load_cards_for_ids(db, entity.compound_ids)
        return [compound_image_for_card(c) for c in cards]
    return collect_images(kind, entity)


# --------------------------------------------------------------------------- bundle

def _wrap(kind: str, entity_id: str, entity, images: List[CompoundImage]) -> BundleData:
    if kind == "enzyme":
        return EnzymeBundle(entity_id=entity_id, entity=entity, images=images)
    if kind == "compound":
        return CompoundBundle(entity_id=entity_id, entity=entity, images=images)
    if kind == "pathway":
        return PathwayBundle(entity_id=entity_id, entity=entity, images=images)
    return ReactionBundle(entity_id=entity_id, entity=entity, images=images)


_KIND_LABEL = {
    "enzyme": "Enzyme",
    "compound": "Compound",
    "reaction": "Reaction",
    "pathway": "Pathway",
}

_PATHWAY_FAILURES = {
    PATHWAY_BAD_ID: ("BAD_REQUEST", "malformed pathway id"),
    PATHWAY_UNKNOWN_COMPOUND: ("NOT_FOUND", "pathway references an unknown compound"),
    PATHWAY_NOT_REACHABLE: (
        "NOT_FOUND",
        "pathway is not reachable under the current filters",
    ),
}


async def build_entity_bundle(
    db: AsyncSession,
    kind: str,
    entity_id: str,
    flags: Optional[EnzymeTableFlags] = None,
    source_types: Optional[List[str]] = None,
    review_statuses: Optional[List[str]] = None,
) -> Tuple[Optional[BundleData], Optional[ErrorDetail]]:
    """Return `(bundle, error)`, exactly one of which is non-None.

    An invalid kind -> BAD_REQUEST. pathway uses a `PATH_` id: the id encodes only
    the compound chain and **not the filter conditions**, so forward
    `source_types`/`review_statuses` into `load_pathway_by_chain`, or the segments
    returned may not be the set the caller originally saw.
    """
    if kind not in BUNDLE_KINDS:
        return None, ErrorDetail(code="BAD_REQUEST", message=f"Unknown kind {kind}")

    if kind == "enzyme":
        entity = await load_enzyme_detail(db, entity_id, flags=flags)
    elif kind == "pathway":
        chain = parse_pathway_id(entity_id)
        if chain is None:
            return None, _pathway_error(PATHWAY_BAD_ID, entity_id)
        entity, failure = await load_pathway_by_chain(
            db, chain, source_types, review_statuses
        )
        if entity is None:
            return None, _pathway_error(failure, entity_id)
    else:
        entity = await load_entity(db, kind, entity_id)

    if entity is None:
        return None, ErrorDetail(
            code="NOT_FOUND",
            message=f"{_KIND_LABEL[kind]} {entity_id} not found",
        )
    return _wrap(kind, entity_id, entity, await _images_for(db, kind, entity)), None


def _pathway_error(failure: Optional[str], entity_id: str) -> ErrorDetail:
    code, message = _PATHWAY_FAILURES.get(failure or PATHWAY_NOT_REACHABLE, ("NOT_FOUND", "pathway unavailable"))
    return ErrorDetail(code=code, message=f"{message}: {entity_id}")


async def build_entity_bundles(
    db: AsyncSession,
    requests: List[Tuple[str, str]],
) -> List[BundleBatchResult]:
    """Return one result per input, in input order; a repeated id reuses the last
    result but still emits one entry each.

    `requests` is `[(kind, entity_id), ...]`; its length is already capped by
    `BundleBatchRequest.items`'s `max_length`.
    """
    flags = await probe_enzyme_tables(db)
    cache: Dict[Tuple[str, str], BundleBatchResult] = {}
    results: List[BundleBatchResult] = []

    for kind, entity_id in requests:
        key = (kind, entity_id)
        if key in cache:
            results.append(cache[key])
            continue

        # A batch item carries only {kind, entityId}, no room for filter conditions
        # -- pathway is always rebuilt unfiltered here; use the single endpoint when
        # filters are needed.
        bundle, error = await build_entity_bundle(db, kind, entity_id, flags=flags)
        if bundle is None:
            result = BundleBatchResult(
                kind=kind,
                entity_id=entity_id,
                found=False,
                error=error or ErrorDetail(code="NOT_FOUND", message=f"{entity_id} not found"),
            )
        else:
            result = BundleBatchResult(kind=kind, entity_id=entity_id, found=True, bundle=bundle)

        cache[key] = result
        results.append(result)

    return results
