# Mensa Project - Architecture Overview

## System Purpose
The Mensa Project is a lottery prediction system that:
- Ingests historical lottery data from NY State APIs
- Stores data in ChromaDB (vector database) for semantic search
- Trains ML models on historical patterns
- Generates predictions for various lottery games
- Provides AI-powered insights via RAG (Retrieval-Augmented Generation)

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         Docker Compose                          │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐         │
│  │   Frontend   │  │   Backend    │  │   ChromaDB   │         │
│  │  (React)     │  │  (FastAPI)   │  │  (Vector DB) │         │
│  │  Port: 3000  │  │  Port: 5000  │  │  Port: 8000  │         │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘         │
└─────────┼─────────────────┼─────────────────┼─────────────────┘
          │                 │                 │
          │ HTTP/REST       │ HTTP/REST       │ HTTP/REST
          │                 │                 │
          ▼                 ▼                 ▼
┌─────────────────────────────────────────────────────────────────┐
│                        External Services                         │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐         │
│  │  NY.gov API  │  │   Gemini     │  │  ChatGPT     │         │
│  │  (Lottery    │  │   (Google)   │  │  (OpenAI)    │         │
│  │   Data)      │  │   LLM)       │  │  LLM)        │         │
│  └──────────────┘  └──────────────┘  └──────────────┘         │
└─────────────────────────────────────────────────────────────────┘
```

## Service Dependencies

```
ChromaDB (starts first)
    ↓
Backend (waits for ChromaDB)
    ↓
Frontend (waits for Backend)
```

## Backend Architecture

### Core Services

```
┌─────────────────────────────────────────────────────────────────┐
│                      FastAPI Application                         │
│                         (main.py)                                │
└─────────────────────────────────────────────────────────────────┘
                              │
        ┌─────────────────────┼─────────────────────┐
        │                     │                     │
        ▼                     ▼                     ▼
┌──────────────┐    ┌──────────────┐    ┌──────────────┐
│   Ingest     │    │  Predictor   │    │   Trainer    │
│   Service    │    │   Service    │    │   Service    │
└──────────────┘    └──────────────┘    └──────────────┘
        │                     │                     │
        │                     │                     │
        ▼                     ▼                     ▼
┌──────────────┐    ┌──────────────┐    ┌──────────────┐
│  ChromaDB    │    │  ML Models   │    │  ChromaDB    │
│  (Storage)   │    │  (sklearn)   │    │  (Training   │
└──────────────┘    └──────────────┘    │   Data)      │
                                        └──────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                        AI Services                               │
└─────────────────────────────────────────────────────────────────┘
                              │
        ┌─────────────────────┼─────────────────────┐
        │                     │                     │
        ▼                     ▼                     ▼
┌──────────────┐    ┌──────────────┐    ┌──────────────┐
│  RAG Service │    │  LM Router   │    │  ChromaDB    │
│  (AI Chat)   │    │  (Provider   │    │  (Context)   │
└──────────────┘    │   Selection) │    └──────────────┘
                    └──────────────┘
                            │
            ┌───────────────┼───────────────┐
            │               │               │
            ▼               ▼               ▼
    ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
    │   Gemini     │ │  ChatGPT     │ │    Grok      │
    │   Client     │ │   Client     │ │   Client     │
    └──────────────┘ └──────────────┘ └──────────────┘
```

## Data Flow Diagrams

### 1. Data Ingestion Flow

```
User triggers ingestion (or auto-start on boot)
         ↓
┌─────────────────────────────────────────────────────────────────┐
│  Ingest Service (ingest.py)                                     │
│  - Fetches data from NY.gov APIs                                │
│  - Normalizes lottery results                                   │
│  - Creates embeddings for semantic search                       │
└─────────────────────────────────────────────────────────────────┘
         ↓
┌─────────────────────────────────────────────────────────────────┐
│  ChromaDB (chroma_client.py)                                     │
│  - Stores vectors with metadata (game, date, numbers)           │
│  - One collection per lottery game                              │
│  - Enables semantic similarity search                            │
└─────────────────────────────────────────────────────────────────┘
         ↓
┌─────────────────────────────────────────────────────────────────┐
│  Background Progress Tracking                                   │
│  - Updates startup_state dictionary                             │
│  - Frontend polls /api/startup_status                           │
│  - Shows real-time progress to user                              │
└─────────────────────────────────────────────────────────────────┘
```

### 2. ML Training Flow

```
User triggers training for a game
         ↓
┌─────────────────────────────────────────────────────────────────┐
│  Trainer Service (trainer.py)                                    │
│  - Fetches historical data from ChromaDB                         │
│  - Parses winning numbers and patterns                           │
│  - Extracts features (frequency, gaps, trends)                  │
└─────────────────────────────────────────────────────────────────┘
         ↓
┌─────────────────────────────────────────────────────────────────┐
│  Model Training (sklearn RandomForest)                           │
│  - Trains regressor on historical patterns                      │
│  - Validates accuracy (target: 95%)                              │
│  - Saves model to /data/models                                   │
└─────────────────────────────────────────────────────────────────┘
         ↓
┌─────────────────────────────────────────────────────────────────┐
│  Model Storage                                                   │
│  - Persists trained models as joblib files                        │
│  - Tracks training metadata (accuracy, timestamp)                │
└─────────────────────────────────────────────────────────────────┘
```

### 3. Prediction Flow

```
User requests prediction for a game
         ↓
┌─────────────────────────────────────────────────────────────────┐
│  Predictor Service (predictor.py)                                 │
│  - Loads trained ML model for the game                            │
│  - Applies game-specific rules (ranges, uniqueness)              │
│  - Generates candidate numbers                                    │
└─────────────────────────────────────────────────────────────────┘
         ↓
┌─────────────────────────────────────────────────────────────────┐
│  Rule Validation                                                 │
│  - Ensures numbers are within valid ranges                       │
│  - Enforces uniqueness requirements                              │
│  - Applies game-specific constraints                              │
└─────────────────────────────────────────────────────────────────┘
         ↓
┌─────────────────────────────────────────────────────────────────┐
│  Response                                                        │
│  - Returns formatted prediction                                  │
│  - Includes confidence metrics                                   │
│  - Shows next draw schedule                                      │
└─────────────────────────────────────────────────────────────────┘
```

### 4. RAG (AI Chat) Flow

```
User asks a question in the chat interface
         ↓
┌─────────────────────────────────────────────────────────────────┐
│  RAG Service (rag_service.py)                                    │
│  - Receives user query                                           │
│  - Identifies relevant game context                              │
└─────────────────────────────────────────────────────────────────┘
         ↓
┌─────────────────────────────────────────────────────────────────┐
│  Context Retrieval (ChromaDB)                                    │
│  - Performs semantic search on lottery data                      │
│  - Retrieves top-k relevant documents                            │
│  - Formats context for LLM                                       │
└─────────────────────────────────────────────────────────────────┘
         ↓
┌─────────────────────────────────────────────────────────────────┐
│  LM Router (lm_router.py)                                        │
│  - Audits available LLM providers                                │
│  - Selects fastest available provider                            │
│  - Falls back if provider unavailable                            │
└─────────────────────────────────────────────────────────────────┘
         ↓
┌─────────────────────────────────────────────────────────────────┐
│  LLM Generation                                                   │
│  - Sends augmented prompt (query + context)                      │
│  - Generates AI response                                         │
│  - Returns response with sources                                 │
└─────────────────────────────────────────────────────────────────┘
         ↓
┌─────────────────────────────────────────────────────────────────┐
│  Frontend Display                                                │
│  - Shows AI response with citations                              │
│  - Highlights relevant data sources                              │
└─────────────────────────────────────────────────────────────────┘
```

## Supported Lottery Games

| Game | Primary Numbers | Bonus | Draw Schedule |
|------|----------------|-------|---------------|
| Take 5 | 5 (1-39) | 1 (1-39) | 2x daily |
| Pick 3 | 3 (0-9) | None | 2x daily |
| Powerball | 5 (1-69) | 1 (1-26) | Mon, Wed, Sat |
| Mega Millions | 5 (1-70) | 1 (1-25) | Tue, Fri |
| Pick 10 | 20 (1-80) | None | Daily |
| Cash4Life | 5 (1-60) | 1 (1-4) | Daily |
| Quick Draw | 20 (1-80) | None | 360x daily |
| NY Lotto | 6 (1-59) | 1 (1-59) | Wed, Sat |

## Key API Endpoints

### Backend (FastAPI)
- `GET /api` - Health check
- `GET /api/startup_status` - Ingestion progress
- `GET /api/games` - List available games
- `GET /api/predict/{game}` - Get prediction
- `POST /api/ingest/{game}` - Trigger ingestion
- `POST /api/train/{game}` - Train model
- `GET /api/chat` - RAG chat endpoint
- `GET /api/chroma_status` - ChromaDB status

### Frontend (React)
- `/` - Main dashboard
- Real-time SSE: `/api/ingest_stream` - Ingestion progress updates

## Configuration

### Environment Variables
- `GEMINI_API_KEY` - Google Gemini API key
- `CHAT_GPT_API_KEY` - OpenAI API key
- `OPENAI_API_KEY` - OpenAI API key
- `GROK_API_KEY` - xAI Grok API key
- `CHROMA_HOST` - ChromaDB host (default: mensa_chroma)
- `CHROMA_PORT` - ChromaDB port (default: 8000)
- `DATA_DIR` - Data directory (default: /data)

### Game Configuration
All game rules are defined in `backend/config.py`:
- Number ranges
- Uniqueness requirements
- Bonus ball rules
- Draw schedules
- API endpoints for data ingestion

## Deployment

### Local Development
```bash
./start.sh --build
```

### Production
- Docker Compose for container orchestration
- Prebuilt GHCR images (`docker-compose.ghcr.yml`, see `DEPLOY.md`)
- Health checks for service reliability
- Background ingestion for fast startup

## Technology Stack

### Backend
- Python 3.11
- FastAPI (web framework)
- ChromaDB (vector database)
- scikit-learn (ML models)
- joblib (model persistence)

### Frontend
- React
- Axios (HTTP client)
- Bootstrap (styling)

### Infrastructure
- Docker & Docker Compose
- Nginx (reverse proxy for frontend)
