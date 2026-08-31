from typing import List, Dict, Any, Optional, Set
from chromadb.utils import embedding_functions
from chromadb.api.models.Collection import Collection
from services.chroma_client import chroma_client

class ChromaRepository:
    """
    A repository class for abstracting ChromaDB interactions.
    This centralizes all direct database access logic, making it easier to manage,
    test, and modify the underlying data store without affecting business logic.
    """

    def __init__(self):
        # Lazy: DefaultEmbeddingFunction loads ONNX (~80MB) and can hang the
        # Hypercorn worker at import time on low-RAM Docker Desktop hosts.
        self._default_ef = None

    @property
    def default_ef(self):
        if self._default_ef is None:
            self._default_ef = embedding_functions.DefaultEmbeddingFunction()
        return self._default_ef

    def get_or_create_collection(self, name: str) -> Collection:
        """Gets or creates a ChromaDB collection."""
        return chroma_client.client.get_or_create_collection(
            name=name,
            embedding_function=self.default_ef
        )

    def get_collection(self, name: str) -> Optional[Collection]:
        """Gets a ChromaDB collection by name."""
        try:
            return chroma_client.client.get_collection(
                name=name,
                embedding_function=self.default_ef,
            )
        except TypeError:
            # Older chromadb get_collection may not accept embedding_function
            try:
                return chroma_client.client.get_collection(name=name)
            except Exception:
                return None
        except Exception:  # ChromaDB raises if collection not found
            return None

    def list_collections(self) -> List[Collection]:
        """Lists all ChromaDB collections."""
        return chroma_client.client.list_collections()

    def delete_collection(self, name: str):
        """Deletes a ChromaDB collection by name."""
        chroma_client.client.delete_collection(name=name)

    def add_documents(
        self,
        collection_name: str,
        documents: List[str],
        metadatas: List[Dict[str, Any]],
        ids: List[str]
    ):
        """Adds documents to a specified collection."""
        collection = self.get_or_create_collection(collection_name)
        collection.add(documents=documents, metadatas=metadatas, ids=ids)

    def upsert_documents(
        self,
        collection_name: str,
        documents: List[str],
        metadatas: List[Dict[str, Any]],
        ids: List[str]
    ):
        """Upserts documents to a specified collection."""
        collection = self.get_or_create_collection(collection_name)
        collection.upsert(documents=documents, metadatas=metadatas, ids=ids)

    def get_documents(
        self,
        collection_name: str,
        ids: Optional[List[str]] = None,
        where: Optional[Dict[str, Any]] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        include: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """Retrieves documents from a specified collection."""
        collection = self.get_collection(collection_name)
        if not collection:
            return {"ids": [], "embeddings": [], "documents": [], "metadatas": [], "uris": []}
        return collection.get(ids=ids, where=where, limit=limit, offset=offset, include=include)

    def count_documents(self, collection_name: str) -> int:
        """Counts documents in a specified collection."""
        collection = self.get_collection(collection_name)
        if not collection:
            return 0
        return collection.count()

    def heartbeat(self) -> int:
        """Checks the ChromaDB server's health."""
        return chroma_client.client.heartbeat()

chroma_repository = ChromaRepository()