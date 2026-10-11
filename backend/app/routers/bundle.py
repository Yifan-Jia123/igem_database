"""Aggregation endpoint: one call returns both the entity fields and the
structure-image URLs of its compounds.

Assembly logic lives entirely in `app.services.entity_service` -- this file only
does parameter checks and envelope wrapping.
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db
from app.schemas.bundle import BUNDLE_KINDS, BundleBatchData, BundleBatchRequest
from app.schemas.common import ApiResponse, ErrorDetail
from app.services.entity_service import (
    build_entity_bundle,
    build_entity_bundles,
    find_compound_id_by_inchikey,
)
from app.utils.chemistry import smiles_to_inchikey

router = APIRouter()


@router.get("/bundle/{kind}/{entity_id}")
async def get_entity_bundle(
    kind: str,
    entity_id: str,
    source_types: Optional[List[str]] = Query(
        None, description="仅 pathway 用：重建链时透传的来源筛选"
    ),
    review_statuses: Optional[List[str]] = Query(
        None, description="仅 pathway 用：重建链时透传的审核状态筛选"
    ),
    db: AsyncSession = Depends(get_db),
):
    """kind ∈ enzyme | compound | reaction | pathway.

    A `pathway`'s entityId is `PATH_A_B_C` (see `pathway_service.parse_pathway_id`).
    It is not a row in a table but **computed on the fly** from that chain; the id
    itself carries no filter conditions, so if it was searched with a filtered
    `POST /search/pathways`, pass the same filters again here.
    """
    # kind is deliberately not typed Literal: otherwise FastAPI returns a
    # non-enveloped 422, while this repo's convention is HTTP 200 + success:false
    # (see the metadata / search routers).
    if kind not in BUNDLE_KINDS:
        return ApiResponse(success=False, error={"code": "BAD_REQUEST", "message": f"Unknown kind {kind}"})

    bundle, error = await build_entity_bundle(
        db, kind, entity_id,
        source_types=source_types,
        review_statuses=review_statuses,
    )
    if bundle is None:
        return ApiResponse(success=False, error=error.model_dump(by_alias=True))
    return ApiResponse(data=bundle.model_dump(by_alias=True))


@router.post("/bundle/batch")
async def post_entity_bundles(
    payload: BundleBatchRequest,
    db: AsyncSession = Depends(get_db),
):
    """Return one result per input, in input order; one miss does not fail the
    whole request.

    Each item carries only `{kind, entityId}`, so pathway is always rebuilt
    **unfiltered** here.
    """
    results = await build_entity_bundles(db, [(i.kind, i.entity_id) for i in payload.items])
    found = sum(1 for r in results if r.found)
    data = BundleBatchData(items=results)
    return ApiResponse(
        data=data.model_dump(by_alias=True),
        meta={"total": len(results), "found": found, "missing": len(results) - found},
    )


@router.get("/bundle/compound")
async def get_compound_bundle_by_smiles(
    # Deliberately Optional rather than Query(...): a missing required parameter
    # makes FastAPI return a **non-enveloped 422**, while this repo's convention is
    # HTTP 200 + success:false -- the same reason as the kind case below.
    smiles: Optional[str] = Query(None, description="SMILES; resolved to a compound server-side with RDKit"),
    db: AsyncSession = Depends(get_db),
):
    """Fetch a compound bundle by SMILES -- the entry point for "I don't know the
    compoundId yet".

    A separate route rather than adding a parameter to `/bundle/{kind}/{entity_id}`:
    SMILES contains `/ \\ # + @ [ ]`, and `/` -- even encoded as `%2F` -- is decoded
    before route matching and cuts the path, so it cannot ride in a path segment.
    This one has only two segments and does not collide with the three-segment one.

    The response is **exactly the same** as `GET /bundle/compound/{id}` -- SMILES is
    just another way to address "how to find this compound", not a change to "what
    is returned once found". The caller reads the resolution result from
    `data.entityId`.
    """
    raw = (smiles or "").strip()
    if not raw:
        return ApiResponse(success=False, error={"code": "BAD_REQUEST", "message": "SMILES is required"})

    # **Must not upper()**: SMILES is case-sensitive; upper-casing aromatic c/n/o and
    # the chirality marker @ yields a different molecule -- `c1ccccc1O` (phenol)
    # silently becomes `C1CCCCC1O` (cyclohexanol), and RDKit still parses it fine
    # with no error.
    key = smiles_to_inchikey(raw)
    if key is None:
        return ApiResponse(
            success=False,
            error=ErrorDetail(
                code="INVALID_SMILES",
                message="SMILES could not be parsed",
                details={"smiles": raw},
            ).model_dump(by_alias=True),
        )
    if not key:
        # Kept separate from the previous one: the SMILES is not wrong, it describes
        # an R-group/Markush generic with an *, which has no InChIKey to begin with.
        # The caller's action differs -- supply another SMILES, not fix the syntax.
        return ApiResponse(
            success=False,
            error=ErrorDetail(
                code="INCHIKEY_UNAVAILABLE",
                message="SMILES parsed but has no InChIKey (generic / R-group structure)",
                details={"smiles": raw},
            ).model_dump(by_alias=True),
        )

    compound_id = await find_compound_id_by_inchikey(db, key)
    if compound_id is None:
        return ApiResponse(
            success=False,
            error=ErrorDetail(
                code="NOT_FOUND",
                message=f"No compound with InChIKey {key}",
                details={"smiles": raw, "inchikey": key},
            ).model_dump(by_alias=True),
        )

    bundle, error = await build_entity_bundle(db, "compound", compound_id)
    if bundle is None:
        return ApiResponse(success=False, error=error.model_dump(by_alias=True))
    return ApiResponse(data=bundle.model_dump(by_alias=True))
