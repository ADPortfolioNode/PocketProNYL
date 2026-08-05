from fastapi import APIRouter, Depends, HTTPException
from typing import List, Dict, Any, Optional
from pydantic import BaseModel

from repositories.chroma_repository import ChromaRepository, chroma_repository

router = APIRouter()

# Pydantic models for request bodies
class DocumentAddRequest(BaseModel):
    documents: List[str]
    metadatas: List[Dict[str, Any]]
    ids: List[str]

class DocumentGetRequest(BaseModel):
    ids: Optional[List[str]] = None
    where: Optional[Dict[str, Any]] = None
    limit: Optional[int] = None
    offset: Optional[int] = None
    include: Optional[List[str]] = None

@router.get("/heartbeat")
async def chroma_heartbeat(repo: ChromaRepository = Depends(lambda: chroma_repository)):
    """Checks the ChromaDB server's health."""
    try:
        heartbeat_ms = repo.heartbeat()
        return {"status": "ok", "heartbeat_ms": heartbeat_ms}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"ChromaDB heartbeat failed: {e}")

@router.get("/collections")
async def list_chroma_collections(repo: ChromaRepository = Depends(lambda: chroma_repository)):
    """Lists all ChromaDB collections."""
    try:
        collections = repo.list_collections()
        return {"collections": [{"name": c.name, "id": c.id, "count": c.count()} for c in collections]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list collections: {e}")

@router.get("/collection/{name}")
async def get_chroma_collection(name: str, repo: ChromaRepository = Depends(lambda: chroma_repository)):
    """Gets details for a specific ChromaDB collection."""
    collection = repo.get_collection(name)
    if not collection:
        raise HTTPException(status_code=404, detail=f"Collection '{name}' not found")
    return {"name": collection.name, "id": collection.id, "count": collection.count()}

@router.post("/collection/{name}/add")
async def add_documents_to_collection(
    name: str,
    request: DocumentAddRequest,
    repo: ChromaRepository = Depends(lambda: chroma_repository)
):
    """Adds documents to a specified ChromaDB collection."""
    try:
        repo.add_documents(name, request.documents, request.metadatas, request.ids)
        return {"status": "success", "message": f"Added {len(request.ids)} documents to collection '{name}'"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to add documents: {e}")

@router.post("/collection/{name}/upsert")
async def upsert_documents_to_collection(
    name: str,
    request: DocumentAddRequest,
    repo: ChromaRepository = Depends(lambda: chroma_repository)
):
    """Upserts documents to a specified ChromaDB collection."""
    try:
        repo.upsert_documents(name, request.documents, request.metadatas, request.ids)
        return {"status": "success", "message": f"Upserted {len(request.ids)} documents to collection '{name}'"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to upsert documents: {e}")

@router.post("/collection/{name}/get")
async def get_documents_from_collection(
    name: str,
    request: DocumentGetRequest,
    repo: ChromaRepository = Depends(lambda: chroma_repository)
):
    """Retrieves documents from a specified ChromaDB collection."""
    try:
        result = repo.get_documents(
            name,
            ids=request.ids,
            where=request.where,
            limit=request.limit,
            offset=request.offset,
            include=request.include
        )
        return {"status": "success", "data": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get documents: {e}")

@router.get("/collection/{name}/count")
async def count_documents_in_collection(name: str, repo: ChromaRepository = Depends(lambda: chroma_repository)):
    """Counts documents in a specified ChromaDB collection."""
    try:
        count = repo.count_documents(name)
        return {"status": "success", "count": count}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to count documents: {e}")

@router.delete("/collection/{name}")
async def delete_chroma_collection(name: str, repo: ChromaRepository = Depends(lambda: chroma_repository)):
    """Deletes a ChromaDB collection by name."""
    try:
        repo.delete_collection(name)
        return {"status": "success", "message": f"Collection '{name}' deleted"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete collection: {e}")