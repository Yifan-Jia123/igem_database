from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import (
    metadata,
    graph,
    search,
    enzymes,
    compounds,
    reactions,
    homology,
    download,
    assets,
)

app = FastAPI(
    title="IGEM Metabolic Pathway Database",
    description="Terpene Atlas — enzyme-reaction-compound graph database",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

API_PREFIX = "/api/v1"

app.include_router(metadata.router, prefix=API_PREFIX, tags=["Metadata"])
app.include_router(graph.router, prefix=API_PREFIX, tags=["Graph"])
app.include_router(search.router, prefix=API_PREFIX, tags=["Search"])
app.include_router(enzymes.router, prefix=API_PREFIX, tags=["Enzymes"])
app.include_router(compounds.router, prefix=API_PREFIX, tags=["Compounds"])
app.include_router(reactions.router, prefix=API_PREFIX, tags=["Reactions"])
app.include_router(homology.router, prefix=API_PREFIX, tags=["Homology"])
app.include_router(download.router, prefix=API_PREFIX, tags=["Download"])
app.include_router(assets.router, prefix=API_PREFIX, tags=["Assets"])


@app.get("/")
async def root():
    return {"service": "IGEM Metabolic Pathway Database", "version": "0.1.0"}
