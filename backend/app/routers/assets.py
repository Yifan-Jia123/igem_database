from fastapi import APIRouter

from app.schemas.common import ApiResponse

router = APIRouter()


@router.get("/assets/reactions/{rhea_id}/atom-map.svg")
async def get_atom_map(rhea_id: str):
    """Proxy Rhea atom map SVG (stub)."""
    return ApiResponse(data={"url": f"https://www.rhea-db.org/rhea/{rhea_id.split(':')[-1]}/reaction.svg"})


@router.get("/assets/compounds/{chebi_id}/structure.svg")
async def get_compound_structure(chebi_id: str):
    """Proxy ChEBI structure SVG (stub)."""
    return ApiResponse(data={"url": f"https://www.ebi.ac.uk/chebi/displayImage.do?defaultImage=true&chebiId={chebi_id.split(':')[-1]}"})
