import React, { useState, useEffect } from 'react';
import axios from 'axios';
import { formatApiError } from '../utils/errorUtils';
import {
  buildTrainRequestBody,
  formatTrainingErrorMessage,
  formatTrainingSuccessMessage,
  isTrainSuccessStatus,
} from '../utils/trainingUtils';
import { runTrainingJob } from '../utils/runTrainJob';

const DEFAULT_TRAIN_PARAMS = {
  testSize: 0.25,
  randomState: 42,
  nEstimators: 250,
  maxDepth: 18,
  maxIterations: 40,
  targetAccuracy: 0.90,
  windowSize: 3,
  autoTune: true,
  blendStep: 0.05,
};
import getApiBase from '../utils/apiBase';
import PredictionPanel from './PredictionPanel';
import ChatPanel from './ChatPanel';
import WorkflowSummary from './WorkflowSummary';
import ExperimentsPanel from './ExperimentsPanel';
import GameSummaryPanel from './GameSummaryPanel';
import ChromaStatusPanel from './ChromaStatusPanel';
import chromaStateManager from '../utils/chromaStateManager';
import '../styles/dashboard.css';

export default function Dashboard() {
  const API_BASE = getApiBase();
  
  // Game and UI State
  const [games, setGames] = useState([]);
  const [selectedGame, setSelectedGame] = useState('');
  const [selectedForIngest, setSelectedForIngest] = useState('');
  
  // Status tracking
  const [ingestStatus, setIngestStatus] = useState('idle');
  const [trainStatus, setTrainStatus] = useState('idle');
  const [trainProgress, setTrainProgress] = useState(0);
  const [experiments, setExperiments] = useState([]);
  
  // Real-time collection updates
  const [collectionUpdates, setCollectionUpdates] = useState({});
  const [lastUpdate, setLastUpdate] = useState(null);

  // Fetch available games on mount
  useEffect(() => {
    async function fetchGames() {
      try {
        const r = await axios.get(`${API_BASE}/api/games`);
        const gameList = r.data.games || [];
        setGames(gameList);
        if (gameList.length > 0 && !selectedGame) {
          setSelectedGame(gameList[0]);
          setSelectedForIngest(gameList[0]);
        }
      } catch (e) {
        console.error('Error fetching games:', e);
      }
    }
    
    if (API_BASE) {
      fetchGames();
    }
  }, [API_BASE, selectedGame]);

  // Subscribe to ChromaDB state changes
  useEffect(() => {
    const unsubscribe = chromaStateManager.subscribe((update) => {
      setCollectionUpdates(prev => ({
        ...prev,
        [update.game]: {
          rowsAdded: update.rowsAdded,
          totalRows: update.totalRows,
          timestamp: update.timestamp
        }
      }));
      setLastUpdate(update);
    });

    return () => unsubscribe();
  }, []);

  // Polling for experiments
  useEffect(() => {
    async function fetchExperiments() {
      try {
        const r = await axios.get(`${API_BASE}/api/experiments`);
        setExperiments(r.data.experiments || []);
      } catch (e) {
        console.error('Error fetching experiments:', e);
      }
    }

    if (API_BASE) {
      fetchExperiments();
      const experimentPollInterval = setInterval(fetchExperiments, 5000);
      return () => clearInterval(experimentPollInterval);
    }
  }, [API_BASE]);

  const startIngest = async () => {
    if (!selectedForIngest) {
      alert('Please select a game');
      return;
    }
    
    setIngestStatus('in progress');

    try {
      const response = await axios.post(`${API_BASE}/api/ingest`, { 
        game: selectedForIngest 
      });
      
      if (response.data.status === 'success') {
        setIngestStatus('completed');
        // Trigger collection update notification
        chromaStateManager.notifyCollectionUpdate(
          selectedForIngest,
          response.data.added,
          response.data.total
        );
      } else {
        setIngestStatus('error');
        alert(`Ingestion failed: ${response.data.message}`);
      }
    } catch (e) {
      console.error("Error starting ingestion:", e);
      setIngestStatus('error');
      alert(`Ingestion failed: ${formatApiError(e)}`);
    }
  };

  const startTrain = async () => {
    if (!selectedGame) {
      alert('Please select a game');
      return;
    }
    
    setTrainStatus('in progress');
    setTrainProgress(0);
    
    const interval = setInterval(() => {
      setTrainProgress(p => Math.min(p + 5, 95));
    }, 2000);

    try {
      const response = { data: await runTrainingJob(
        axios,
        API_BASE,
        buildTrainRequestBody(selectedGame, DEFAULT_TRAIN_PARAMS),
      ) };
      
      clearInterval(interval);
      setTrainProgress(100);
      
      if (isTrainSuccessStatus(response.data.status)) {
        setTrainStatus('completed');
        alert(formatTrainingSuccessMessage(selectedGame, response.data));
      } else {
        setTrainStatus('error');
        alert(`Training failed: ${response.data.message || response.data.error}`);
      }
    } catch (e) {
      clearInterval(interval);
      console.error("Error starting training:", e);
      setTrainStatus('error');
      alert(`Training failed: ${formatTrainingErrorMessage(e, formatApiError)}`);
    }
  };

  return (
    <div className="dashboard">
      {/* Header with Game Selector */}
      <div className="dashboard-header">
        <h1>Lottery Data Pipeline</h1>
        <div className="game-selector-wrapper">
          <label htmlFor="game-select" className="label">Select Game:</label>
          <select
            id="game-select"
            value={selectedGame}
            onChange={(e) => setSelectedGame(e.target.value)}
            className="game-select"
          >
            {games.map(game => (
              <option key={game} value={game}>{game.toUpperCase()}</option>
            ))}
          </select>
        </div>
      </div>

      {/* Real-time Collection Status */}
      {lastUpdate && (
        <div className="collection-update-banner">
          <div className="update-content">
            <span className="update-icon">✓</span>
            <div className="update-text">
              <strong>{lastUpdate.game.toUpperCase()}</strong>: {lastUpdate.rowsAdded} rows added
              {lastUpdate.totalRows && ` (Total: ${lastUpdate.totalRows})`}
            </div>
            <span className="update-time">{new Date(lastUpdate.timestamp).toLocaleTimeString()}</span>
          </div>
        </div>
      )}

      {/* Main Content Grid */}
      <div className={`dashboard-grid ${selectedGame ? 'grid-cols-2' : 'grid-cols-1'}`}>
        {/* Control Panel */}
        <div className="card">
          <div className="card-header">
            <h3>Data Pipeline Control</h3>
            <span className={`badge badge-${ingestStatus === 'completed' ? 'success' : ingestStatus === 'error' ? 'error' : 'info'}`}>
              {ingestStatus.charAt(0).toUpperCase() + ingestStatus.slice(1)}
            </span>
          </div>
          <div className="card-body">
            <div className="form-group">
              <label htmlFor="ingest-game" className="label">Game for Ingestion:</label>
              <select
                id="ingest-game"
                value={selectedForIngest}
                onChange={(e) => setSelectedForIngest(e.target.value)}
                className="form-input"
              >
                {games.map(game => (
                  <option key={game} value={game}>{game.toUpperCase()}</option>
                ))}
              </select>
            </div>

            <div className="form-group">
              <button
                onClick={startIngest}
                disabled={!selectedForIngest || ingestStatus === 'in progress'}
                className={`btn ${ingestStatus === 'in progress' ? 'btn-secondary' : 'btn-primary'}`}
              >
                {ingestStatus === 'in progress' ? (
                  <>
                    <span className="spinner"></span>
                    Ingesting...
                  </>
                ) : (
                  'Start Ingestion'
                )}
              </button>
            </div>

            <div className="form-group">
              <button
                onClick={startTrain}
                disabled={!selectedGame || trainStatus === 'in progress'}
                className={`btn ${trainStatus === 'in progress' ? 'btn-secondary' : 'btn-primary'}`}
              >
                {trainStatus === 'in progress' ? (
                  <>
                    <span className="spinner"></span>
                    Training...
                  </>
                ) : (
                  `Train Model for ${selectedGame.toUpperCase()}`
                )}
              </button>
            </div>

            {trainStatus === 'in progress' && (
              <div className="form-group">
                <div className="dashboard-modern-progress-wrap">
                  <progress className="dashboard-modern-progress" value={trainProgress} max="100" />
                </div>
                <small>{trainProgress}%</small>
              </div>
            )}
          </div>
        </div>

        {/* Selected Game Details */}
        {selectedGame && (
          <div className="card">
            <div className="card-header">
              <h3>{selectedGame.toUpperCase()} Details</h3>
              {collectionUpdates[selectedGame] && (
                <span className="badge badge-success">
                  {collectionUpdates[selectedGame].totalRows} rows
                </span>
              )}
            </div>
            <div className="card-body">
              <GameSummaryPanel game={selectedGame} />
            </div>
          </div>
        )}
      </div>

      {/* Secondary Panels */}
      <div className="dashboard-grid grid-cols-1">
        <div className="card">
          <div className="card-header">
            <h3>ChromaDB Status</h3>
          </div>
          <div className="card-body">
            <ChromaStatusPanel />
          </div>
        </div>
      </div>

      {/* Additional Features */}
      <div className={`dashboard-grid ${selectedGame ? 'grid-cols-2' : 'grid-cols-1'}`}>
        <div className="card">
          <div className="card-header">
            <h3>Suggestions</h3>
          </div>
          <div className="card-body">
            <PredictionPanel game={selectedGame} />
          </div>
        </div>

        {selectedGame && (
          <div className="card">
            <div className="card-header">
              <h3>Chat Assistant</h3>
            </div>
            <div className="card-body">
              <ChatPanel />
            </div>
          </div>
        )}
      </div>

      {/* Experiments and Workflow */}
      <div className="dashboard-grid grid-cols-1">
        <div className="card">
          <div className="card-header">
            <h3>Experiments</h3>
          </div>
          <div className="card-body">
            <ExperimentsPanel experiments={experiments} />
          </div>
        </div>
      </div>

      <div className="dashboard-grid grid-cols-1">
        <div className="card">
          <div className="card-header">
            <h3>Workflow Summary</h3>
          </div>
          <div className="card-body">
            <WorkflowSummary 
              selectedGame={selectedGame}
              ingestStatus={ingestStatus}
              trainStatus={trainStatus}
            />
          </div>
        </div>
      </div>
    </div>
  );
}
