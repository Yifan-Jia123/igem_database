import time
from fastapi import APIRouter
from fastapi.responses import Response
import httpx

router = APIRouter()

_rhea_cache: dict[str, tuple[bytes, float]] = {}
_chebi_cache: dict[str, tuple[bytes, float, str]] = {}
CACHE_TTL = 3600  # 1 hour
REQUEST_TIMEOUT = 15


@router.get("/assets/reactions/{rhea_id}/atom-map.svg")
async def get_atom_map(rhea_id: str):
    number = rhea_id.split(":")[-1] if ":" in rhea_id else rhea_id

    now = time.time()
    cached = _rhea_cache.get(number)
    if cached and cached[1] > now:
        return Response(content=cached[0], media_type="image/svg+xml")

    url = f"https://www.rhea-db.org/rhea/{number}/reaction.svg"
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            svg_bytes = resp.content
            _rhea_cache[number] = (svg_bytes, now + CACHE_TTL)
            return Response(content=svg_bytes, media_type="image/svg+xml")
    except Exception:
        outdated = cached[0] if cached else None
        if outdated:
            return Response(content=outdated, media_type="image/svg+xml")
        return Response(content="", status_code=502)


@router.get("/assets/compounds/{chebi_id}/structure.svg")
async def get_compound_structure(chebi_id: str):
    number = chebi_id.split(":")[-1] if ":" in chebi_id else chebi_id

    now = time.time()
    cached = _chebi_cache.get(number)
    if cached and cached[1] > now:
        return Response(content=cached[0], media_type=cached[2])

    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, follow_redirects=True) as client:
            compound_resp = await client.get(f"https://www.ebi.ac.uk/chebi/backend/api/public/compound/{number}")
            compound_resp.raise_for_status()
            structure_id = (compound_resp.json().get("default_structure") or {}).get("id")
            if not structure_id:
                return Response(content="", status_code=404)

            image_resp = await client.get(f"https://www.ebi.ac.uk/chebi/backend/api/public/structure/{structure_id}/")
            image_resp.raise_for_status()
            media_type = image_resp.headers.get("content-type", "image/svg+xml").split(";", 1)[0]
            image_bytes = image_resp.content
            if not media_type.startswith("image/") or image_bytes.lstrip().lower().startswith(b"<!doctype"):
                raise ValueError("ChEBI structure endpoint did not return an image")

            _chebi_cache[number] = (image_bytes, now + CACHE_TTL, media_type)
            return Response(content=image_bytes, media_type=media_type)
    except Exception:
        if cached:
            return Response(content=cached[0], media_type=cached[2])
        return Response(content="", status_code=502)