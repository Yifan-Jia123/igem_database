import time
from fastapi import APIRouter
from fastapi.responses import Response
import httpx

router = APIRouter()

_rhea_cache: dict[str, tuple[bytes, float]] = {}
_chebi_cache: dict[str, tuple[bytes, float, str]] = {}
CACHE_TTL = 3600  # 1 hour
REQUEST_TIMEOUT = 15

# Rhea asks API clients to identify themselves (https://www.rhea-db.org/help/rest-api).
# The default httpx user-agent also trips the Cloudflare rule in front of the site.
RHEA_HEADERS = {
    "User-Agent": "StaraseAtlas/1.0 (terpene pathway database; mailto:anshuaicheng200692@163.com)",
    "Accept": "image/svg+xml,*/*",
}

# Rhea stores one drawing per reaction, reachable under any of its four ids: the
# master plus the three direction-resolved forms. Verified against the whole of
# rhea-directions.tsv (18611 rows, 0 exceptions): left-to-right = master+1,
# right-to-left = +2, bidirectional = +3. They return byte-identical images, so
# the offsets are only useful as a fallback — polymer reactions have no drawing
# at all, and occasionally the master id is the one that 404s.
RHEA_ID_OFFSETS = (0, 1, 2, 3)


@router.get("/assets/reactions/{rhea_id}/atom-map.svg")
async def get_atom_map(rhea_id: str):
    number = rhea_id.split(":")[-1] if ":" in rhea_id else rhea_id
    try:
        base = int(number)
    except ValueError:
        return Response(content="", status_code=404)

    now = time.time()
    cached = _rhea_cache.get(number)
    if cached and cached[1] > now:
        return Response(content=cached[0], media_type="image/svg+xml")

    try:
        async with httpx.AsyncClient(
            timeout=REQUEST_TIMEOUT, follow_redirects=True, headers=RHEA_HEADERS
        ) as client:
            for offset in RHEA_ID_OFFSETS:
                resp = await client.get(f"https://www.rhea-db.org/rhea/{base + offset}/svg")
                if resp.status_code == 404:
                    continue
                resp.raise_for_status()
                svg_bytes = resp.content
                if not svg_bytes.lstrip()[:200].lower().startswith(b"<?xml") and b"<svg" not in svg_bytes[:800]:
                    raise ValueError("Rhea did not return an SVG drawing")
                _rhea_cache[number] = (svg_bytes, now + CACHE_TTL)
                return Response(content=svg_bytes, media_type="image/svg+xml")
            # No id in the family has a drawing (polymer reactions) — a definite
            # "none exists", distinct from the 502 "upstream unreachable" below.
            return Response(content="", status_code=404)
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
