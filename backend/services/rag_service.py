"""
RAG (Retrieval-Augmented Generation) Service
Combines ChromaDB retrieval with Gemini LLM for context-aware responses
"""
import asyncio

from services.chroma_client import chroma_client
from services.chroma_client import chroma_client # Import the chroma_client instance
from services.gemini_client import gemini_client
from config import settings

class RAGService:
    """
    Retrieval-Augmented Generation service that:
    1. Retrieves relevant documents from ChromaDB
    2. Augments LLM prompt with retrieved context
    3. Generates informed responses using Gemini
    """

    def __init__(self, chroma_client, gemini_client, top_k=5):
        self.chroma_client = chroma_client
        self.gemini_client = gemini_client
        self.top_k = top_k

    async def query_with_rag(
        self, 
        user_query: str, 
        game: str = None,
        use_all_games: bool = False,
        lm_client=None,
        lm_provider: str = "auto",
    ) -> dict:
        """
        Query using RAG pattern:
        1. Search ChromaDB for relevant documents
        2. Build context from retrieved documents
        3. Augment prompt with context
        4. Generate response using Gemini
        
        Args:
            user_query: User's question
            game: Specific game to search (optional)
            use_all_games: Search across all games if True
            
        Returns:
            dict with response, sources, and context info
        """
        
        # Step 1: Retrieve relevant documents from ChromaDB
        # Run synchronous retrieval in an executor to avoid blocking the event loop
        loop = asyncio.get_running_loop()
        retrieved_docs = await loop.run_in_executor(
            None,
            self._retrieve_context,
            user_query,
            game,
            use_all_games,
            None # top_k
        )
        
        # Step 2: Format retrieved context
        context_text = self._format_context(retrieved_docs)
        
        # Step 3: Build augmented prompt
        augmented_prompt = self._build_augmented_prompt(user_query, context_text)

        # Step 4: Generate response with configured LM client
        active_lm_client = lm_client or self.gemini_client
        selected_provider = None
        if hasattr(active_lm_client, "generate_with_provider"):
            lm_result = await active_lm_client.generate_with_provider(
                augmented_prompt,
                preferred_provider=lm_provider,
            )
            response_text = lm_result.get("response", "")
            selected_provider = lm_result.get("provider")
        else:
            response_text = await active_lm_client.generate_text(augmented_prompt)
        
        return {
            "response": response_text,
            "sources": retrieved_docs,
            "context_count": len(retrieved_docs),
            "game": game,
            "lm_provider": selected_provider,
        }

    def _build_local_fallback_response(self, user_query: str, documents: list) -> str:
        if not documents:
            return (
                "Gemini is currently unavailable, but the app is running. "
                "I could not find relevant context records for your query in ChromaDB yet. "
                "Try a more specific request (for example: 'show latest take5 winning numbers')."
            )

        unique_games = sorted({doc.get("game", "unknown") for doc in documents if doc.get("game")})
        sample_lines = []
        for doc in documents[:3]:
            game = str(doc.get("game", "unknown")).upper()
            metadata = doc.get("metadata") or {}
            winning = metadata.get("winning_numbers")
            draw_date = metadata.get("draw_date")
            content = str(doc.get("content", "")).strip()

            if winning:
                line = f"- {game}: winning numbers {winning}"
                if draw_date:
                    line += f" (draw date: {draw_date})"
            elif content:
                line = f"- {game}: {content[:180]}"
            else:
                line = f"- {game}: context record available"

            sample_lines.append(line)

        context_preview = "\n".join(sample_lines)
        games_text = ", ".join(game.upper() for game in unique_games) if unique_games else "the available games"

        return (
            "Gemini is currently unavailable, so this answer is generated from local ChromaDB context only.\n\n"
            f"Query: {user_query}\n"
            f"Relevant games: {games_text}\n"
            f"Top context records:\n{context_preview}\n\n"
            "You can continue using ingestion, training, suggestions, and diagnostics while Gemini reconnects."
        )

    def retrieve_context(
        self,
        query: str,
        game: str = None,
        use_all_games: bool = False,
        top_k: int = None,
    ) -> list:
        """Public API for retrieving relevant ChromaDB documents for a query."""
        return self._retrieve_context(
            query,
            game=game,
            use_all_games=use_all_games,
            top_k=top_k,
        )

    def _retrieve_context(
        self, 
        query: str, 
        game: str = None,
        use_all_games: bool = False,
        top_k: int = None
    ) -> list:
        """
        Retrieve relevant documents from ChromaDB
        """
        if top_k is None:
            top_k = self.top_k
        
        try:
            # If specific game requested, search that collection
            if game and not use_all_games:
                return self._search_game_collection(query, game, top_k)
            
            # Search across all game collections
            if use_all_games:
                return self._search_all_collections(query, top_k)
            
            # Default: search all available collections
            return self._search_all_collections(query, top_k)
            
        except Exception as e:
            print(f"Error retrieving context: {e}")
            return []

    def _search_game_collection(self, query: str, game: str, top_k: int) -> list:
        """Search a specific game's ChromaDB collection"""
        try:
            collection = self.chroma_client.get_or_create_collection(game)
            results = collection.query(
                query_texts=[query],
                n_results=top_k
            )
            
            # Format results
            documents = []
            if results and results['documents'] and len(results['documents']) > 0:
                for i, doc in enumerate(results['documents'][0]):
                    documents.append({
                        "game": game,
                        "content": doc,
                        "distance": results['distances'][0][i] if results.get('distances') else 0,
                        "metadata": results['metadatas'][0][i] if results.get('metadatas') and i < len(results['metadatas'][0]) else {}
                    })
            
            return documents
        except Exception as e:
            print(f"Error searching {game} collection: {e}")
            return self._fallback_get_recent_collection_docs(game, top_k)

    def _search_all_collections(self, query: str, top_k: int) -> list:
        """Search across all game collections"""
        all_documents = []
        
        try:
            collections = self.chroma_client.client.list_collections()
            
            for collection_obj in collections:
                game_name = collection_obj.name
                try:
                    collection = self.chroma_client.get_or_create_collection(game_name)
                    results = collection.query(
                        query_texts=[query],
                        n_results=top_k // len(collections) + 1  # Distribute top_k across collections
                    )
                    
                    if results and results['documents'] and len(results['documents']) > 0:
                        for i, doc in enumerate(results['documents'][0]):
                            all_documents.append({
                                "game": game_name,
                                "content": doc,
                                "distance": results['distances'][0][i] if results.get('distances') else 0,
                                "metadata": results['metadatas'][0][i] if results.get('metadatas') and i < len(results['metadatas'][0]) else {}
                            })
                except Exception as e:
                    print(f"Error searching {game_name} collection: {e}")
                    all_documents.extend(self._fallback_get_recent_collection_docs(game_name, max(1, top_k // max(len(collections), 1))))
                    continue
            
            # Sort by distance and return top_k
            all_documents.sort(key=lambda x: x.get('distance', float('inf')))
            return all_documents[:top_k]
            
        except Exception as e:
            print(f"Error listing collections: {e}")
            return []

    def _fallback_get_recent_collection_docs(self, game: str, limit: int) -> list:
        try:
            collection = self.chroma_client.get_or_create_collection(game)
            data = collection.get(limit=limit, include=["documents", "metadatas"])
            docs = data.get("documents") or []
            metas = data.get("metadatas") or []

            documents = []
            for i, content in enumerate(docs[:limit]):
                metadata = metas[i] if i < len(metas) and isinstance(metas[i], dict) else {}
                documents.append({
                    "game": game,
                    "content": content,
                    "distance": 1.0,
                    "metadata": metadata,
                })

            return documents
        except Exception as e:
            print(f"Fallback get() failed for {game}: {e}")
            return []

    def _format_context(self, documents: list) -> str:
        """Format retrieved documents into context string for prompt"""
        if not documents:
            return "No relevant context found in the database."
        
        context_parts = []
        
        for i, doc in enumerate(documents, 1):
            game = doc.get('game', 'Unknown')
            content = doc.get('content', '')
            metadata = doc.get('metadata', {})
            distance = doc.get('distance', 0)
            
            # Build document context
            doc_context = f"[Source {i} - {game.upper()}]\n{content}"
            
            if metadata and metadata.get('winning_numbers'):
                doc_context += f"\nWinning Numbers: {metadata.get('winning_numbers')}"
            if metadata and metadata.get('draw_date'):
                doc_context += f"\nDraw Date: {metadata.get('draw_date')}"
            
            context_parts.append(doc_context)
        
        return "\n\n".join(context_parts)

    def _build_augmented_prompt(self, user_query: str, context: str) -> str:
        """Build the augmented prompt with context for Gemini"""
        augmented_prompt = f"""You are an expert lottery data analyst. Use the following context from our lottery database to answer the user's question accurately and helpfully.

CONTEXT FROM LOTTERY DATABASE:
{context}

---

USER QUESTION: {user_query}

Please provide a helpful, accurate response based on the context above. If the context doesn't contain relevant information, say so clearly and provide general knowledge if helpful."""
        
        return augmented_prompt

    async def generate_summary(self, game: str) -> str:
        """Generate a summary of a game's lottery data"""
        query = f"Summary of {game} lottery draws, patterns, and frequency statistics"
        result = await self.query_with_rag(
            user_query=query,
            game=game,
            use_all_games=False
        )
        return result['response']

    async def generate_game_comparison(self) -> str:
        """Generate comparison across all games"""
        query = "Compare the lottery games across draws, odds, and patterns"
        result = await self.query_with_rag(
            user_query=query,
            use_all_games=True
        )
        return result['response']


# Initialize RAG service
rag_service = RAGService(chroma_client, gemini_client, top_k=5)
