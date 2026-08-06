import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import axios from 'axios';
import getApiBase from '../utils/apiBase';
import PredictionPanel from './PredictionPanel';
import ChatPanelRAG from './ChatPanelRAG';
import WorkflowSummary from './WorkflowSummary';
import ExperimentsPanel from './ExperimentsPanel';
import GameSummaryPanel from './GameSummaryPanel';
import ChromaStatusPanel from './ChromaStatusPanel';
import GameSelector from './GameSelector';
import IngestionProgressPanel from './IngestionProgressPanel';
import ExpandableCard from './ExpandableCard';
import ProgressiveProgressBar from './ProgressiveProgressBar';
import chromaStateManager from '../utils/chromaStateManager';
import { startPolling } from '../utils/polling';
import { formatApiError } from '../utils/errorUtils';
import { formatExperimentTimestamp, parseExperimentTimestampMs } from '../utils/timestampUtils';
import {
  BASE_MODEL_TYPE,
  buildTrainRequestBody,
  formatModelTypeLabel,
  formatTrainSizePercent,
  normalizeTrainSizeFraction,
  normalizeNEstimators,
  normalizeMaxDepth,
  normalizeMaxIterations,
  formatTrainingSuccessMessage,
  isTrainSuccessStatus,
  formatTrainingErrorMessage,
  isCompletedTrainingExperiment,
  pickHighestAccuracyExperiment,
  resolveBestTrainParams,
  experimentToTrainParams,
  trainingDataBadgeClass,
  trainingDataStatusLabel,
  gameNeedsTraining,
} from '../utils/trainingUtils';

const ALL_GAMES_VALUE = '__all_games__';
const GAME_COLOR_SCHEMES = ['primary', 'success', 'warning', 'info', 'danger', 'secondary', 'dark'];

export default function Dashboard({ startupStatus = { status: 'unknown', progress: 0, total: 0, elapsed_s: 0 }, startupErrorMessage = '' }) {
  const API_BASE = getApiBase();
  // Use runtime-computed API base
  // eslint-disable-next-line no-console
  console.debug('API base:', API_BASE || '(same-origin via nginx proxy)');
  const [ingestStatus, setIngestStatus] = useState('idle');
  const [ingestErrorMessage, setIngestErrorMessage] = useState('');

  // A ref to ensure the auto-ingestion check runs only once per component mount
  const hasCheckedForAutoIngestion = useRef(false);

  // Function to fetch startup status directly from the backend for the auto-ingestion check
  const getBackendStartupStatus = useCallback(async () => {
    try {
      const response = await axios.get(`${API_BASE}/api/startup_status`);
      return response.data;
    } catch (err) {
      console.error("Failed to fetch backend startup status for auto-ingestion:", err);
      return null;
    }
  }, [API_BASE]);
  const [ingestingGame, setIngestingGame] = useState(null);
  const [ingestStartTime, setIngestStartTime] = useState(null);
  const [trainStatus, setTrainStatus] = useState('idle');
  const [trainErrorMessage, setTrainErrorMessage] = useState('');
  const [trainProgress, setTrainProgress] = useState(0);
  const [trainStartTime, setTrainStartTime] = useState(null);
  const [games, setGames] = useState([]);
  const [gameContents, setGameContents] = useState({});
  const [gameContentsErrorMessage, setGameContentsErrorMessage] = useState('');
  const [experiments, setExperiments] = useState([]);
  const [experimentsErrorMessage, setExperimentsErrorMessage] = useState('');
  const [selectedGame, setSelectedGame] = useState('');
  const [forceReingest, setForceReingest] = useState(false);
  const [selectedTrainGame, setSelectedTrainGame] = useState('');
  const [selectedExperimentReadyGame, setSelectedExperimentReadyGame] = useState('');
  const [selectedTrainingExperimentId, setSelectedTrainingExperimentId] = useState('');
  const [expandedCard, setExpandedCard] = useState(null);
  const [chatIsActive, setChatIsActive] = useState(false);

  // Function to trigger backend startup initialization
  const triggerBackendStartupInit = useCallback(async () => {
    try {
      console.log("Attempting to trigger backend startup initialization via /api/startup_init...");
      await axios.post(`${API_BASE}/api/startup_init`, {});
      console.log("Backend startup initialization triggered successfully.");
      // The parent component should eventually update the startupStatus prop via its own polling
    } catch (err) {
      console.error("Failed to trigger backend startup initialization:", err);
      // Optionally, set a local error state or show a notification
    }
  }, [API_BASE]);
  const [summaryRefreshKey, setSummaryRefreshKey] = useState(0);
  const [allGamesProgress, setAllGamesProgress] = useState({});


  useEffect(() => {
    if (ingestStatus !== 'in progress' || selectedGame !== ALL_GAMES_VALUE) {
      return;
    }

    let eventSource;
    let cancelled = false;

    const mapIngestStatus = (backendStatus) => (
      backendStatus === 'completed'
        ? 'completed'
        : backendStatus === 'error'
          ? 'error'
          : backendStatus === 'ingesting'
            ? 'active'
            : 'pending'
    );

    const applyGameProgress = (gameName, progressData) => {
      const rowsFetched = Number(progressData.rows_fetched || 0);
      const totalRows = Number(progressData.total_rows || 0);
      const mappedStatus = mapIngestStatus(String(progressData.status || '').toLowerCase());
      setAllGamesProgress((prev) => ({
        ...prev,
        [gameName]: {
          ...(prev[gameName] || {}),
          status: mappedStatus,
          rowsFetched,
          totalRows,
        },
      }));
    };

    const handleStreamPayload = (payload) => {
      if (!payload || typeof payload !== 'object') return;
      for (const [gameName, gameState] of Object.entries(payload)) {
        if (gameState && typeof gameState === 'object') {
          applyGameProgress(gameName, gameState);
        }
      }
    };

    const pollAllGamesProgress = async () => {
      if (cancelled || !games.length) return;
      await Promise.all(games.map(async (gameName) => {
        try {
          const response = await axios.get(`${API_BASE}/api/ingest_progress?game=${gameName}`);
          if (!cancelled && response?.data) {
            applyGameProgress(gameName, response.data);
          }
        } catch {
          // transient failures ignored
        }
      }));
    };

    const tryStartSSE = () => {
      try {
        const url = `${API_BASE}/api/ingest_stream`;
        eventSource = new EventSource(url);
        eventSource.onmessage = (e) => {
          try {
            handleStreamPayload(JSON.parse(e.data) || {});
          } catch {
            // ignore parse errors
          }
        };
        eventSource.onerror = () => {
          try { eventSource.close(); } catch (e) {}
          eventSource = null;
        };
      } catch {
        eventSource = null;
      }
    };

    tryStartSSE();
    pollAllGamesProgress();

    const stopPolling = startPolling({
      intervalMs: 5000,
      tick: async () => {
        if (!eventSource) {
          await pollAllGamesProgress();
        }
      },
    });

    return () => {
      cancelled = true;
      try { if (eventSource) eventSource.close(); } catch (e) {}
      stopPolling();
    };
  }, [ingestStatus, selectedGame, ingestingGame, API_BASE, games]);

  // Training parameters
  const [trainParams, setTrainParams] = useState({
    testSize: 0.25,
    randomState: 42,
    nEstimators: 250,
    maxDepth: 18,
    maxIterations: 40,
    targetAccuracy: 0.90,
    windowSize: 3,
    autoTune: true,
    blendStep: 0.05,
  });
  const [trainDefaultsForGame, setTrainDefaultsForGame] = useState(null);
  const [trainIncremental, setTrainIncremental] = useState(null);
  const [trainDataByGame, setTrainDataByGame] = useState({});
  const trainParamsAutoAppliedForGame = useRef(null);
  const trainParamsExperimentSource = useRef(null);

  useEffect(() => {
    if (!selectedTrainGame) return;
    let cancelled = false;

    const mapDefaults = (defaults, prev) => ({
      testSize: normalizeTrainSizeFraction(
        defaults.train_size ?? prev?.testSize ?? 0.25,
        0.25,
      ),
      randomState: defaults.random_state ?? prev?.randomState ?? 42,
      nEstimators: normalizeNEstimators(defaults.n_estimators ?? prev?.nEstimators ?? 250),
      maxDepth: normalizeMaxDepth(defaults.max_depth ?? prev?.maxDepth ?? 18),
      maxIterations: normalizeMaxIterations(defaults.max_iterations ?? prev?.maxIterations ?? 40),
      targetAccuracy: defaults.target_accuracy ?? prev?.targetAccuracy ?? 0.90,
      windowSize: defaults.window_size ?? prev?.windowSize ?? 3,
      autoTune: defaults.auto_tune ?? prev?.autoTune ?? true,
      blendStep: defaults.blend_step ?? prev?.blendStep ?? 0.05,
    });

    const shallowEqual = (a, b, keys) => {
      if (!a || !b) return false;
      return keys.every((key) => (a[key] ?? null) === (b[key] ?? null));
    };

    const fetchSettings = async () => {
      try {
        const response = await axios.get(`${API_BASE}/api/train_settings?game=${selectedTrainGame}`, { timeout: 15000 });
        if (cancelled || !response?.data) return;
        const recreateDefaults = response.data.recreate_defaults
          || response.data.incremental?.recreate_defaults
          || response.data.incremental?.best_training_params
          || {};
        const mapped = mapDefaults(
          { ...(response.data.defaults || {}), ...recreateDefaults },
          trainParams,
        );
        const keys = ['testSize', 'randomState', 'nEstimators', 'maxDepth', 'maxIterations', 'targetAccuracy', 'windowSize', 'autoTune', 'blendStep'];
        const shouldOverwrite = trainDefaultsForGame === null || shallowEqual(trainParams, trainDefaultsForGame, keys);
        if (shouldOverwrite) {
          setTrainParams((prev) => ({ ...prev, ...mapped }));
        }
        setTrainDefaultsForGame(mapped);
        setTrainIncremental(response.data.incremental || null);
        if (response.data.training_data) {
          setTrainDataByGame((prev) => ({
            ...prev,
            [selectedTrainGame]: response.data.training_data,
          }));
        }
      } catch (_) {
        // Keep current params when settings endpoint is unavailable.
      }
    };

    fetchSettings();
    return () => { cancelled = true; };
  }, [selectedTrainGame, API_BASE]);

  useEffect(() => {
    let cancelled = false;

    const refreshTrainingDataStatus = async () => {
      try {
        const response = await axios.get(`${API_BASE}/api/train_settings`, { timeout: 30000 });
        if (cancelled || !response?.data?.per_game) return;
        const next = {};
        for (const [gameKey, payload] of Object.entries(response.data.per_game)) {
          if (payload?.training_data) {
            next[gameKey] = payload.training_data;
          }
        }
        setTrainDataByGame(next);
      } catch (_) {
        // Non-fatal: training screen still works without freshness badges.
      }
    };

    refreshTrainingDataStatus();
    return () => { cancelled = true; };
  }, [API_BASE, summaryRefreshKey, ingestStatus, trainStatus, experiments.length, gameContents]);

  useEffect(() => {
    async function fetchGamesAndContents() {
      try {
        const r = await axios.get(`${API_BASE}/api/games`, { timeout: 15000 });
        setGameContentsErrorMessage('');
        const gameList = r.data.games || [];
        setGames(gameList);
        setSelectedTrainGame((prev) => prev || gameList[0] || '');
        const contents = {};
        try {
          const summaryRes = await axios.get(`${API_BASE}/api/games/summaries`, { timeout: 20000 });
          const summaries = summaryRes.data?.summaries || {};
          for (const game of gameList) {
            contents[game] = Number(summaries[game]?.draw_count || 0);
          }
        } catch {
          for (const game of gameList) {
            contents[game] = 0;
          }
        }
        setGameContents(contents);
      } catch (e) {
        setGameContentsErrorMessage(e?.response?.data?.detail || e.message || 'Failed to fetch game contents.');
      }
    }
    fetchGamesAndContents();
  }, [API_BASE]);

  useEffect(() => {
    if (trainStatus === 'in progress' || ingestStatus === 'in progress') {
      return undefined;
    }

    return startPolling({
      intervalMs: 30000,
      maxBackoffMs: 120000,
      tick: async () => {
        try {
          const r = await axios.get(`${API_BASE}/api/experiments?limit=100`, { timeout: 30000 });
          const experimentsPayload = Array.isArray(r.data)
            ? r.data
            : (Array.isArray(r.data?.experiments) ? r.data.experiments : []);
          setExperiments(experimentsPayload);
          setExperimentsErrorMessage('');
        } catch (e) {
          if (e?.response) {
            setExperimentsErrorMessage(e?.response?.data?.detail || e.message || 'Failed to fetch experiments.');
            return;
          }
          // Backend restarting or busy — backoff silently without UI error spam
          throw e;
        }
      },
    });
  }, [API_BASE, trainStatus, ingestStatus]);

  // Effect to check for auto-ingestion conditions and trigger if necessary on initial load
  useEffect(() => {
    // Only run if API_BASE is available and we haven't checked yet for this component instance
    if (!API_BASE || hasCheckedForAutoIngestion.current) {
      return;
    }

    const performAutoIngestionCheck = async () => {
      hasCheckedForAutoIngestion.current = true; // Mark as checked

      // First, check the overall startup status from the backend
      const currentBackendStatus = await getBackendStartupStatus();

      if (!currentBackendStatus) {
        console.warn("Could not get backend startup status. Skipping auto-ingestion check.");
        return;
      }

      // Condition for auto-ingestion: overall system status is 'ready' AND all games are 'not_started'
      const allGamesNotStarted = Object.values(currentBackendStatus.games || {}).every(
        (gameStatus) => gameStatus.status === 'not_started'
      );

      if (currentBackendStatus.status === 'ready' && allGamesNotStarted) {
        console.log("Detected all games require ingestion. Auto-triggering backend startup initialization.");
        triggerBackendStartupInit();
      }
    };
    performAutoIngestionCheck();
  }, [API_BASE, getBackendStartupStatus, triggerBackendStartupInit]); // Dependencies

  const startIngest = useCallback(async () => {
    if (!selectedGame) return;
    setIngestStatus('in progress');
    setIngestErrorMessage('');
    setIngestStartTime(Date.now());
    setExpandedCard('ingest');

    try {
      if (selectedGame === ALL_GAMES_VALUE) {
        setAllGamesProgress(
          games.reduce((acc, game) => {
            acc[game] = { status: 'pending', totalRows: 0, rowsFetched: 0 };
            return acc;
          }, {})
        );
        setIngestingGame(ALL_GAMES_VALUE);

        const gameTasks = games.map((game) => (async () => {
          setAllGamesProgress((prev) => ({
            ...prev,
            [game]: {
              ...(prev[game] || {}),
              status: 'active',
            },
          }));

          let pollCancelled = false;
          const pollCurrentGameProgress = async () => {
            try {
              const progressRes = await axios.get(`${API_BASE}/api/ingest_progress?game=${game}`);
              const progressData = progressRes?.data || {};
              if (pollCancelled) return;

              const rowsFetched = Number(progressData.rows_fetched || 0);
              const totalRows = Number(progressData.total_rows || 0);
              const backendStatus = String(progressData.status || '').toLowerCase();
              const mappedStatus = backendStatus === 'completed'
                ? 'completed'
                : backendStatus === 'error'
                  ? 'error'
                  : backendStatus === 'ingesting'
                    ? 'active'
                    : 'pending';

              setAllGamesProgress((prev) => ({
                ...prev,
                [game]: {
                  ...(prev[game] || {}),
                  status: mappedStatus,
                  rowsFetched,
                  totalRows,
                },
              }));
            } catch {
              // transient progress polling failures should not break ingestion
            }
          };

          const stopPoll = startPolling({
            intervalMs: 5000,
            tick: pollCurrentGameProgress,
          });
          await pollCurrentGameProgress();

          try {
            const response = await axios.post(`${API_BASE}/api/ingest`, { game, force: forceReingest });
            const normalizedIngestStatus = String(response?.data?.status || '').toLowerCase();
            
            // Handle queued status - ingestion is processed in background, wait for progress polling
            if (normalizedIngestStatus === 'queued') {
              // Don't mark as error, let progress polling handle the final status
              // Keep status as 'active' since progress polling will update it
              return {
                game,
                status: 'pending',
                message: response?.data?.message || 'Ingestion queued.',
              };
            }

            const isIngestSuccess = normalizedIngestStatus === 'completed' || normalizedIngestStatus === 'success';

            if (!isIngestSuccess) {
              setAllGamesProgress((prev) => ({
                ...prev,
                [game]: {
                  ...(prev[game] || {}),
                  status: 'error',
                },
              }));

              return {
                game,
                status: 'error',
                message: response?.data?.message || 'Ingestion failed.',
              };
            }

            setAllGamesProgress((prev) => ({
              ...prev,
              [game]: {
                ...(prev[game] || {}),
                status: 'completed',
                rowsFetched: response.data.total || prev[game]?.rowsFetched || 0,
                totalRows: response.data.total || prev[game]?.totalRows || 0,
              },
            }));

            return {
              game,
              status: 'completed',
              message: '',
            };
          } catch (err) {
            console.error(`Error ingesting ${game}:`, err);

            setAllGamesProgress((prev) => ({
              ...prev,
              [game]: {
                ...(prev[game] || {}),
                status: 'error',
              },
            }));

            return {
              game,
              status: 'error',
              message: err?.response?.data?.message || err.message || 'Ingestion request failed.',
            };
          } finally {
            pollCancelled = true;
            stopPoll();

            try {
              const res = await axios.get(`${API_BASE}/api/games/${game}/summary`);
              const drawCount = res.data.draw_count;
              setGameContents((prev) => ({
                ...prev,
                [game]: drawCount,
              }));
              chromaStateManager.notifyCollectionUpdate(game, 0, drawCount);
              setSummaryRefreshKey((prev) => prev + 1);
              setAllGamesProgress((prev) => ({
                ...prev,
                [game]: {
                  ...(prev[game] || {}),
                  rowsFetched: drawCount,
                  totalRows: drawCount,
                },
              }));
            } catch {
              // ignore per-game summary refresh failures
            }
          }
        })());

        // Run ingestions sequentially to avoid excessive concurrent requests
        const resolvedResults = [];
        for (const promise of gameTasks) {
          try {
            const val = await promise;
            resolvedResults.push(val);
          } catch (err) {
            resolvedResults.push({ game: undefined, status: 'error', message: err?.message || 'Ingestion task failed.' });
          }
        }

        const failures = resolvedResults.filter((item) => item.status === 'error');
        setIngestingGame(null);
        setIngestStatus(failures.length > 0 ? 'error' : 'completed');

        if (failures.length > 0) {
          const failureMessage = failures
            .map((item) => `${item.game.toUpperCase()}: ${item.message}`)
            .join(' | ');
          setIngestErrorMessage(failureMessage);
        } else {
          setIngestErrorMessage('');
          setExpandedCard(null);
        }
      } else {
        setAllGamesProgress({});
        setIngestingGame(selectedGame);
        const response = await axios.post(`${API_BASE}/api/ingest`, { game: selectedGame, force: forceReingest });
        const normalizedIngestStatus = String(response?.data?.status || '').toLowerCase();
        const isIngestSuccess = normalizedIngestStatus === 'completed' || normalizedIngestStatus === 'success';

        if (isIngestSuccess) {
          setIngestStatus('completed');
          setIngestingGame(null);
          setIngestErrorMessage('');
          const res = await axios.get(`${API_BASE}/api/games/${selectedGame}/summary`);
          const drawCount = res.data.draw_count;
          setGameContents(prev => ({
            ...prev,
            [selectedGame]: drawCount
          }));
          chromaStateManager.notifyCollectionUpdate(selectedGame, response.data.added || 0, drawCount);
          setSummaryRefreshKey((prev) => prev + 1);
          setExpandedCard(null);
        } else {
          setIngestStatus('error');
          setIngestErrorMessage(response?.data?.message || `Ingestion failed for ${selectedGame.toUpperCase()}.`);
          setIngestingGame(null);
        }
      }
    } catch (e) {
      console.error("Error starting ingestion:", e);
      setIngestStatus('error');
      setIngestErrorMessage(e?.response?.data?.message || e.message || 'Failed to start ingestion.');
      setIngestingGame(null);
    }
  }, [selectedGame, games, API_BASE, forceReingest]);

  const handleIngestionComplete = useCallback((progress) => {
    if (progress.status === 'completed') {
      setIngestStatus('completed');
      setIngestErrorMessage('');
      setTimeout(() => setExpandedCard(null), 2000); // Auto-collapse after 2s
    } else if (progress.status === 'error') {
      setIngestStatus('error');
      setIngestErrorMessage(progress?.error || `Ingestion failed for ${selectedGame?.toUpperCase() || 'selected game'}.`);
    }
    setIngestingGame(null);
    
    // Refresh game contents
    if (selectedGame) {
      axios.get(`${API_BASE}/api/games/${selectedGame}/summary`)
        .then(res => {
          const drawCount = res.data.draw_count;
          setGameContents(prev => ({
            ...prev,
            [selectedGame]: drawCount
          }));
          chromaStateManager.notifyCollectionUpdate(selectedGame, progress.added || 0, drawCount);
          setSummaryRefreshKey((prev) => prev + 1);
        })
        .catch(e => console.error("Error refreshing game content:", e));
    }
  }, [selectedGame, API_BASE]);

  const totalStoredDraws = useMemo(
    () => Object.values(gameContents).reduce((sum, count) => sum + Number(count || 0), 0),
    [gameContents]
  );

  const selectedGameStoredDraws = useMemo(() => {
    if (!selectedGame || selectedGame === ALL_GAMES_VALUE) {
      return totalStoredDraws;
    }
    return Number(gameContents[selectedGame] || 0);
  }, [selectedGame, gameContents, totalStoredDraws]);

  const hasStoredDataForSelection = selectedGameStoredDraws > 0;

  const completedTrainingExperiments = useMemo(() => {
    return (experiments || [])
      .filter((exp) => isCompletedTrainingExperiment(exp))
      .sort((a, b) => (parseExperimentTimestampMs(b) ?? 0) - (parseExperimentTimestampMs(a) ?? 0));
  }, [experiments]);

  const trainingReadyGames = useMemo(() => {
    return [...new Set(completedTrainingExperiments.map((exp) => exp.game).filter(Boolean))];
  }, [completedTrainingExperiments]);

  const experimentsForReadyGame = useMemo(() => {
    if (!selectedExperimentReadyGame) return [];
    return completedTrainingExperiments
      .filter((exp) => exp.game === selectedExperimentReadyGame)
      .sort((a, b) => {
        const scoreA = Number(a.score ?? a.final_accuracy ?? a.accuracy ?? 0);
        const scoreB = Number(b.score ?? b.final_accuracy ?? b.accuracy ?? 0);
        if (scoreB !== scoreA) return scoreB - scoreA;
        return (parseExperimentTimestampMs(b) ?? 0) - (parseExperimentTimestampMs(a) ?? 0);
      });
  }, [completedTrainingExperiments, selectedExperimentReadyGame]);

  const selectedTrainingExperiment = useMemo(() => {
    if (!selectedTrainingExperimentId) return null;
    return experimentsForReadyGame.find((exp) => exp.experiment_id === selectedTrainingExperimentId) || null;
  }, [experimentsForReadyGame, selectedTrainingExperimentId]);

  useEffect(() => {
    if (!trainingReadyGames.length) {
      setSelectedExperimentReadyGame('');
      return;
    }

    if (!selectedExperimentReadyGame || !trainingReadyGames.includes(selectedExperimentReadyGame)) {
      setSelectedExperimentReadyGame(trainingReadyGames[0]);
    }
  }, [trainingReadyGames, selectedExperimentReadyGame]);

  useEffect(() => {
    if (!experimentsForReadyGame.length) {
      setSelectedTrainingExperimentId('');
      return;
    }

    const selectedStillExists = experimentsForReadyGame.some((exp) => exp.experiment_id === selectedTrainingExperimentId);
    if (!selectedStillExists) {
      const bestForGame = pickHighestAccuracyExperiment(experimentsForReadyGame, selectedExperimentReadyGame);
      setSelectedTrainingExperimentId(bestForGame?.experiment_id || experimentsForReadyGame[0].experiment_id);
    }
  }, [experimentsForReadyGame, selectedTrainingExperimentId, selectedExperimentReadyGame]);

  useEffect(() => {
    if (!selectedTrainGame) return;
    let cancelled = false;

    const applyHighestAccuracySettings = async () => {
      const shouldAutoApply = trainParamsAutoAppliedForGame.current !== selectedTrainGame;
      const bestExperiment = pickHighestAccuracyExperiment(completedTrainingExperiments, selectedTrainGame);
      const shouldMergeExperiment = Boolean(
        bestExperiment
        && trainParamsExperimentSource.current !== bestExperiment.experiment_id,
      );
      try {
        const response = await axios.get(
          `${API_BASE}/api/train_settings?game=${selectedTrainGame}`,
          { timeout: 15000 },
        );
        if (cancelled || !response?.data) return;

        const mapped = resolveBestTrainParams({
          defaults: response.data.defaults || {},
          recreateDefaults: response.data.recreate_defaults
            || response.data.incremental?.recreate_defaults
            || {},
          bestTrainingParams: response.data.best_training_params
            || response.data.incremental?.best_training_params
            || {},
          experiment: bestExperiment,
        });

        if (shouldAutoApply || shouldMergeExperiment || trainParamsAutoAppliedForGame.current === null) {
          setTrainParams(mapped);
          trainParamsAutoAppliedForGame.current = selectedTrainGame;
          if (bestExperiment) {
            trainParamsExperimentSource.current = bestExperiment.experiment_id;
          }
        }
        setTrainDefaultsForGame(mapped);
        setTrainIncremental(response.data.incremental || null);

        if (bestExperiment) {
          setSelectedExperimentReadyGame(selectedTrainGame);
          setSelectedTrainingExperimentId(bestExperiment.experiment_id);
        }
      } catch (_) {
        // Keep current params when settings endpoint is unavailable.
      }
    };

    applyHighestAccuracySettings();
    return () => { cancelled = true; };
  }, [selectedTrainGame, API_BASE, completedTrainingExperiments]);

  const selectedTrainGameStoredDraws = useMemo(
    () => Number(gameContents[selectedTrainGame] || 0),
    [gameContents, selectedTrainGame]
  );

  const effectiveIngestStatus = useMemo(() => {
    if (ingestStatus === 'in progress') {
      return ingestStatus;
    }

    if (hasStoredDataForSelection) {
      return 'completed';
    }

    if (ingestStatus === 'error' || ingestStatus === 'completed') {
      return ingestStatus;
    }

    return 'idle';
  }, [ingestStatus, hasStoredDataForSelection]);

  const effectiveTrainingTarget = useMemo(() => {
    const requested = Number(trainParams.targetAccuracy ?? 0.9);
    const prior = trainIncremental?.highest_accuracy ?? trainIncremental?.baseline_accuracy;
    const safeRequested = Number.isFinite(requested) ? Math.min(0.99, Math.max(0.5, requested)) : 0.9;
    if (prior == null || !Number.isFinite(Number(prior))) return safeRequested;
    return Math.max(safeRequested, Number(prior));
  }, [trainParams.targetAccuracy, trainIncremental]);

  const startTrain = useCallback(async () => {
    if (effectiveIngestStatus !== 'completed' || !selectedTrainGame) {
      alert('Please complete data ingestion first and select a game.');
      return;
    }
    if (selectedTrainGameStoredDraws <= 0) {
      alert('Selected training game has no draws. Please run ingestion for that game first.');
      return;
    }
    setTrainStatus('in progress');
    setTrainErrorMessage('');
    setTrainProgress(0);
    setTrainStartTime(Date.now());
    setExpandedCard('train');
    
    const interval = setInterval(() => setTrainProgress(p => Math.min(p + 5, 95)), 2000);
    try {
      const response = await axios.post(
        `${API_BASE}/api/train`,
        buildTrainRequestBody(selectedTrainGame, {
          ...trainParams,
          targetAccuracy: effectiveTrainingTarget,
        }),
        { timeout: 600000 },
      );
      clearInterval(interval); // Clear interval regardless of outcome
      setTrainProgress(100);
      if (isTrainSuccessStatus(response?.data?.status)) {
        setTrainStatus('completed');
        setTrainErrorMessage('');
        setSummaryRefreshKey((prev) => prev + 1);
        // Refresh experiments
        const r = await axios.get(`${API_BASE}/api/experiments`);
        const experimentsPayload = Array.isArray(r.data)
          ? r.data
          : (Array.isArray(r.data?.experiments) ? r.data.experiments : []);
        setExperiments(experimentsPayload);
        setExperimentsErrorMessage('');
        try {
          const settingsRes = await axios.get(`${API_BASE}/api/train_settings?game=${selectedTrainGame}`, { timeout: 15000 });
          const bestExperiment = pickHighestAccuracyExperiment(experimentsPayload, selectedTrainGame);
          const mapped = resolveBestTrainParams({
            defaults: settingsRes.data?.defaults || {},
            recreateDefaults: settingsRes.data?.recreate_defaults
              || settingsRes.data?.incremental?.recreate_defaults
              || {},
            bestTrainingParams: settingsRes.data?.best_training_params
              || settingsRes.data?.incremental?.best_training_params
              || {},
            experiment: bestExperiment,
          });
          setTrainParams(mapped);
          setTrainDefaultsForGame(mapped);
          trainParamsAutoAppliedForGame.current = selectedTrainGame;
          if (bestExperiment) {
            trainParamsExperimentSource.current = bestExperiment.experiment_id;
            setSelectedExperimentReadyGame(selectedTrainGame);
            setSelectedTrainingExperimentId(bestExperiment.experiment_id);
          }
          setTrainIncremental(settingsRes.data?.incremental || null);
        } catch (_) {
          // ignore refresh errors
        }
        alert(formatTrainingSuccessMessage(selectedTrainGame, response.data));
        setTimeout(() => setExpandedCard(null), 2000);
      } else {
        setTrainStatus('error');
        setTrainErrorMessage(response.data.error || response.data.message || 'Training failed.');
        alert(`Training failed: ${response.data.error || response.data.message}`);
      }
    } catch (e) {
      clearInterval(interval);
      setTrainStatus('error');
      setTrainProgress(0);
      const errText = formatTrainingErrorMessage(e, formatApiError);
      setTrainErrorMessage(errText);
      alert(`Training failed due to an error: ${errText}`);
    }
  }, [effectiveIngestStatus, selectedTrainGame, selectedTrainGameStoredDraws, trainParams, effectiveTrainingTarget, API_BASE]);

  const trainingModelTypeLabel = useMemo(() => {
    const strategy =
      selectedTrainingExperiment?.model_strategy
      ?? trainIncremental?.model_strategy
      ?? null;
    const blendWeight =
      selectedTrainingExperiment?.blend_weight
      ?? trainIncremental?.blend_weight
      ?? null;
    return formatModelTypeLabel({ strategy, blendWeight }) || BASE_MODEL_TYPE;
  }, [selectedTrainingExperiment, trainIncremental]);

  const isTrained = completedTrainingExperiments.length > 0;

  const selectedTrainData = useMemo(
    () => (selectedTrainGame ? trainDataByGame[selectedTrainGame] : null),
    [selectedTrainGame, trainDataByGame],
  );

  const gamesWithUntrainedData = useMemo(
    () => games.filter((game) => gameNeedsTraining(trainDataByGame[game])),
    [games, trainDataByGame],
  );

  const formatTrainGameOptionLabel = useCallback((game) => {
    const data = trainDataByGame[game];
    const base = game.toUpperCase();
    if (!data) return base;
    if (data.status === 'never_trained') return `${base} — untrained data`;
    if (data.status === 'stale') {
      const n = Number(data.new_draws || 0);
      return n > 0 ? `${base} — +${n.toLocaleString()} new draws` : `${base} — new data`;
    }
    if (data.status === 'current') return `${base} — up to date`;
    return base;
  }, [trainDataByGame]);

  const getGameColorScheme = (gameName) => {
    const idx = games.findIndex((name) => name === gameName);
    if (idx < 0) return 'primary';
    return GAME_COLOR_SCHEMES[idx % GAME_COLOR_SCHEMES.length];
  };

  const getGameLabelColor = (scheme) => {
    switch (scheme) {
      case 'primary': return '#0d6efd';
      case 'success': return '#198754';
      case 'warning': return '#996500';
      case 'info': return '#0dcaf0';
      case 'danger': return '#dc3545';
      case 'secondary': return '#6c757d';
      case 'dark': return '#212529';
      default: return '#0d6efd';
    }
  };

  const getGameProgressFraction = useCallback((progressItem) => {
    if (!progressItem) return 0;

    if (progressItem.status === 'completed') {
      return 1;
    }

    const totalRows = Number(progressItem.totalRows || 0);
    const rowsFetched = Number(progressItem.rowsFetched || 0);

    if (totalRows > 0) {
      return Math.max(0, Math.min(rowsFetched / totalRows, 1));
    }

    if (progressItem.status === 'active') {
      return 0.5;
    }

    return 0;
  }, []);

  const allGamesOverallProgressCurrent = useMemo(() => {
    if (!games.length) return 0;
    return games.reduce((acc, game) => acc + getGameProgressFraction(allGamesProgress[game]), 0);
  }, [games, allGamesProgress, getGameProgressFraction]);

  const initializationDisplay = useMemo(() => {
    const status = startupStatus.status;
    if (status === 'completed') return 'Completed';
    if (status === 'ready') return 'Ready';
    if (status === 'ingesting') return 'In Progress';
    if (status === 'failed') return 'Failed';
    if (status === 'pending') return 'Pending';
    return 'Unknown';
  }, [startupStatus.status]);

  const handleCardFocus = (cardKey) => (expanded) => {
    setExpandedCard(expanded ? cardKey : null);
  };

  const getFocusColClass = (cardKey) => (
    `focus-cards-col${expandedCard === cardKey ? ' focus-cards-col-maximized' : ''}`
  );

  const renderCardErrorAlert = (title, message, className = 'mt-3 mb-0') => {
    if (!message) return null;
    return (
      <div className={`alert alert-danger ${className}`} role="alert">
        <strong>{title}:</strong> {message}
      </div>
    );
  };
  
  return (
    <div className="container-fluid">
      <div className="card mb-4">
        <div className="card-body">
          <h4 className="text-neon mb-2">Lottery Data Initialization</h4>
          <p className="mb-0 text-muted">
            Status: {initializationDisplay}
            {startupStatus.total > 0 ? ` (${startupStatus.progress} of ${startupStatus.total} games)` : ''}
          </p>
          {startupErrorMessage && (
            <p className="mb-0 mt-2 text-danger">Error: {startupErrorMessage}</p>
          )}
        </div>
      </div>
      <WorkflowSummary />
      <div className="mb-4">
        <h4 className="text-neon">Workflow Status</h4>
        <div className="mb-2">
          <ProgressiveProgressBar
            current={effectiveIngestStatus === 'completed' ? 1 : effectiveIngestStatus === 'in progress' ? 0.5 : 0}
            total={1}
            status={effectiveIngestStatus === 'completed' ? 'completed' : effectiveIngestStatus === 'in progress' ? 'active' : effectiveIngestStatus === 'error' ? 'error' : 'idle'}
            label="1. Ingest Data"
            showMetadata={false}
          />
        </div>
        <div className="mb-2">
          <ProgressiveProgressBar
            current={trainProgress}
            total={100}
            status={trainStatus === 'completed' ? 'completed' : trainStatus === 'in progress' ? 'active' : 'idle'}
            label="2. Train Model"
            showMetadata={trainStatus === 'in progress'}
            startTime={trainStartTime}
          />
        </div>
        <div className="mb-2">
          <ProgressiveProgressBar
            current={isTrained ? 1 : 0}
            total={1}
            status={isTrained ? 'completed' : 'idle'}
            label="3. Make Suggestions"
            showMetadata={false}
          />
        </div>
      </div>

      <div className="focus-cards-row">
        {/* Mensa Concierge */}
        <div className={getFocusColClass('chat')}>
          <ExpandableCard
            title="Mensa Concierge"
            cardKey="chat"
            focusedCard={expandedCard}
            neonBorder={true}
            isActive={chatIsActive}
            metadata={{
              'Game Context': selectedGame || 'All games',
              'Status': chatIsActive ? 'Responding' : 'Ready',
            }}
            onToggle={handleCardFocus('chat')}
          >
            <p className="mb-3 text-muted">
              Friendly support for Python, React, ChromaDB, and RAG workflows. Panel expands while chat is active.
            </p>
            <ChatPanelRAG
              game={selectedGame}
              isExpanded={expandedCard === 'chat' || chatIsActive}
              onActivityChange={setChatIsActive}
            />
          </ExpandableCard>
        </div>

        {/* Data Ingestion Card */}
        <div className={getFocusColClass('ingest')}>
          <ExpandableCard
            title="Data Ingestion"
            cardKey="ingest"
            focusedCard={expandedCard}
            neonBorder={true}
            isActive={effectiveIngestStatus === 'in progress'}
            metadata={selectedGame ? {
              'Selected Game': selectedGame.toUpperCase(),
              'Current Draws': `${selectedGame === ALL_GAMES_VALUE ? totalStoredDraws : Number(gameContents[selectedGame] || 0)} draws`,
              'Status': effectiveIngestStatus,
              'Force Reingest': forceReingest ? 'Enabled' : 'Disabled',
              ...(ingestErrorMessage ? { 'Error Message': ingestErrorMessage } : {}),
              'API Endpoint': `${API_BASE}/api/ingest`
            } : {}}
            statusBadge={
              <span className={`badge ${effectiveIngestStatus === 'completed' ? 'bg-success' : effectiveIngestStatus === 'in progress' ? 'bg-warning' : effectiveIngestStatus === 'error' ? 'bg-danger' : 'bg-secondary'}`}>
                {effectiveIngestStatus}
              </span>
            }
            onToggle={handleCardFocus('ingest')}
          >
            <p>Fetch and sync lottery data from NY Open Data.</p>
            <div className="mb-3">
              <label htmlFor="gameSelect" className="form-label text-neon">Select Game</label>
              <GameSelector games={games} onGameSelect={setSelectedGame} includeAllOption={true} allOptionValue={ALL_GAMES_VALUE} allOptionLabel="All Games" />
            </div>
            <div className="form-check mb-3">
              <input
                id="forceReingestToggle"
                type="checkbox"
                className="form-check-input"
                checked={forceReingest}
                onChange={(e) => setForceReingest(e.target.checked)}
                disabled={ingestStatus === 'in progress'}
              />
              <label className="form-check-label" htmlFor="forceReingestToggle">
                Force reingest (reload even when draws already exist)
              </label>
            </div>
            {selectedGame && (
              <div className="alert alert-info">
                {selectedGame === ALL_GAMES_VALUE ? (
                  <><strong>ALL GAMES</strong>: Run ingestion sequentially for every available game</>
                ) : (
                  <><strong>{selectedGame.toUpperCase()}</strong>: {gameContents[selectedGame] || 0} draws currently stored</>
                )}
              </div>
            )}
            <button 
              className="btn btn-primary me-2" 
              onClick={startIngest} 
              disabled={ingestStatus === 'in progress' || !selectedGame}
            >
              {ingestStatus === 'in progress' ? 'Ingesting...' : 'Run Ingest'}
            </button>
            {renderCardErrorAlert('Ingestion Error', ingestErrorMessage, 'mt-3 mb-0')}
            
            {/* Ingestion Progress Panel */}
            {selectedGame !== ALL_GAMES_VALUE && (
              <IngestionProgressPanel 
                game={ingestingGame}
                isActive={ingestStatus === 'in progress'}
                onComplete={handleIngestionComplete}
              />
            )}

            {selectedGame === ALL_GAMES_VALUE && Object.keys(allGamesProgress).length > 0 && (
              <div className="mt-3">
                <h6 className="mb-2">All Games Progress</h6>
                <ProgressiveProgressBar
                  current={allGamesOverallProgressCurrent}
                  total={games.length || 1}
                  status={ingestStatus === 'error' ? 'error' : ingestStatus === 'completed' ? 'completed' : 'active'}
                  label="Overall All Games Ingestion"
                  showMetadata={true}
                  startTime={ingestStartTime}
                  colorScheme="success"
                />
                {games.map((game) => {
                  const gameProgress = allGamesProgress[game] || { status: 'pending', totalRows: 0, rowsFetched: 0 };
                  const gameColorScheme = getGameColorScheme(game);
                  const progressStatus = gameProgress.status === 'completed'
                    ? 'completed'
                    : gameProgress.status === 'error'
                      ? 'error'
                      : gameProgress.status === 'active'
                        ? 'active'
                        : 'idle';
                  const hasRowProgress = Number(gameProgress.totalRows) > 0;
                  const progressCurrent = hasRowProgress
                    ? Number(gameProgress.rowsFetched || 0)
                    : getGameProgressFraction(gameProgress);
                  const progressTotal = hasRowProgress ? Number(gameProgress.totalRows) : 1;

                  return (
                    <ProgressiveProgressBar
                      key={game}
                      current={progressCurrent}
                      total={progressTotal}
                      status={progressStatus}
                      label={`Ingest ${game.toUpperCase()}${gameProgress.totalRows ? ` (${gameProgress.totalRows.toLocaleString()} draws)` : ''}`}
                      labelColor={getGameLabelColor(gameColorScheme)}
                      showMetadata={true}
                      startTime={ingestStartTime}
                      colorScheme={gameColorScheme}
                    />
                  );
                })}
              </div>
            )}
          </ExpandableCard>
        </div>

        {/* Model Training Card */}
        <div className={getFocusColClass('train')}>
          <ExpandableCard
            title="Model Training"
            cardKey="train"
            focusedCard={expandedCard}
            neonBorder={true}
            isActive={trainStatus === 'in progress'}
            metadata={{
              'Model Type': trainingModelTypeLabel || BASE_MODEL_TYPE,
              'Test Size': formatTrainSizePercent(trainParams.testSize),
              'N Estimators': normalizeNEstimators(trainParams.nEstimators),
              'Max Depth': normalizeMaxDepth(trainParams.maxDepth),
              'Max Iterations': normalizeMaxIterations(trainParams.maxIterations),
              'Target Accuracy': `${((trainParams.targetAccuracy ?? 0.90) * 100).toFixed(0)}%`,
              'Effective Target': `${(effectiveTrainingTarget * 100).toFixed(1)}%`,
              ...(trainIncremental?.highest_accuracy != null
                ? { 'Prior Highest': `${(Number(trainIncremental.highest_accuracy) * 100).toFixed(2)}%` }
                : {}),
              'Random State': trainParams.randomState,
              'Ready Games': trainingReadyGames.length,
              'Games Needing Training': gamesWithUntrainedData.length,
              ...(selectedTrainData
                ? { 'Selected Game Data': trainingDataStatusLabel(selectedTrainData.status, selectedTrainData) }
                : {}),
              ...(selectedTrainingExperiment ? { 'Selected Experiment': selectedTrainingExperiment.experiment_id } : {}),
              'Status': trainStatus,
              ...(trainErrorMessage ? { 'Error Message': trainErrorMessage } : {})
            }}
            statusBadge={
              <>
                {gamesWithUntrainedData.length > 0 && (
                  <span className="badge bg-warning text-dark" title="Games with ingested data not yet reflected in the latest model">
                    {gamesWithUntrainedData.length} untrained
                  </span>
                )}
                <span className={`badge ${trainStatus === 'completed' ? 'bg-success' : trainStatus === 'in progress' ? 'bg-warning' : trainStatus === 'error' ? 'bg-danger' : 'bg-secondary'}`}>
                  {trainStatus}
                </span>
              </>
            }
            onToggle={handleCardFocus('train')}
          >
            <p>Train machine learning model on lottery draw history. Each run builds incrementally on the saved model and highest known accuracy.</p>
            <p className="small text-neon mb-3">
              <strong>Model Type:</strong> {trainingModelTypeLabel || BASE_MODEL_TYPE}
            </p>

            {trainIncremental?.incremental_learning && trainIncremental?.highest_accuracy != null && (
              <div className="alert alert-secondary mb-3">
                <strong>Incremental learning:</strong>{' '}
                prior highest {(Number(trainIncremental.highest_accuracy) * 100).toFixed(2)}% — effective training target{' '}
                {(effectiveTrainingTarget * 100).toFixed(2)}% (max of your target and prior best). New models are only saved if they beat the prior floor; otherwise the previous model is retained or ensemble-blended.
                {Array.isArray(trainIncremental.accuracy_history) && trainIncremental.accuracy_history.length > 0 && (
                  <span>{' '}Top accuracies stored: {trainIncremental.accuracy_history.map((v) => `${(Number(v) * 100).toFixed(2)}%`).join(', ')}.</span>
                )}
              </div>
            )}

            {gamesWithUntrainedData.length > 0 && (
              <div className="training-data-summary mb-3">
                <div className="small text-neon mb-2">Games with untrained ingested data</div>
                <div className="d-flex flex-wrap gap-2">
                  {gamesWithUntrainedData.map((game) => {
                    const data = trainDataByGame[game] || {};
                    return (
                      <button
                        key={game}
                        type="button"
                        className={`btn btn-sm training-data-chip ${trainingDataBadgeClass(data.status)}`}
                        onClick={() => {
                          trainParamsAutoAppliedForGame.current = null;
                          trainParamsExperimentSource.current = null;
                          setSelectedTrainGame(game);
                          setExpandedCard('train');
                        }}
                        disabled={trainStatus === 'in progress'}
                        title={data.message || trainingDataStatusLabel(data.status, data)}
                      >
                        {game.toUpperCase()}
                        {data.new_draws > 0 ? ` +${Number(data.new_draws).toLocaleString()}` : ''}
                      </button>
                    );
                  })}
                </div>
              </div>
            )}

            {selectedTrainData?.has_untrained_data && (
              <div className={`alert mb-3 training-data-alert ${selectedTrainData.status === 'never_trained' ? 'alert-warning' : 'alert-info'}`}>
                <strong>
                  {selectedTrainData.status === 'never_trained' ? 'Untrained data' : 'New data since last training'}
                </strong>
                <div className="small mt-1 mb-0">
                  {selectedTrainData.message || trainingDataStatusLabel(selectedTrainData.status, selectedTrainData)}
                </div>
              </div>
            )}

            <div className="mb-3">
              <label htmlFor="trainGameSelect" className="form-label text-neon">Game for Training</label>
              <select
                id="trainGameSelect"
                className="form-select"
                value={selectedTrainGame}
                onChange={(e) => {
                  trainParamsAutoAppliedForGame.current = null;
                  trainParamsExperimentSource.current = null;
                  setSelectedTrainGame(e.target.value);
                }}
                disabled={trainStatus === 'in progress'}
              >
                <option value="">Select game</option>
                {games.map((game) => (
                  <option key={game} value={game}>{formatTrainGameOptionLabel(game)}</option>
                ))}
              </select>
              {selectedTrainData && (
                <div className="form-text">
                  <span className={`badge ${trainingDataBadgeClass(selectedTrainData.status)}`}>
                    {trainingDataStatusLabel(selectedTrainData.status, selectedTrainData)}
                  </span>
                </div>
              )}
            </div>

            <div className="mb-3">
              <label htmlFor="readyGamesSelect" className="form-label text-neon">Games Ready for Experiments</label>
              <select
                id="readyGamesSelect"
                className="form-select"
                value={selectedExperimentReadyGame}
                onChange={(e) => setSelectedExperimentReadyGame(e.target.value)}
                disabled={trainStatus === 'in progress' || trainingReadyGames.length === 0}
              >
                <option value="">Select ready game</option>
                {trainingReadyGames.map((game) => (
                  <option key={game} value={game}>{game.toUpperCase()}</option>
                ))}
              </select>
            </div>

            <div className="mb-3">
              <label htmlFor="trainingExperimentSelect" className="form-label text-neon">Completed Training Experiments</label>
              <select
                id="trainingExperimentSelect"
                className="form-select"
                value={selectedTrainingExperimentId}
                onChange={(e) => {
                  const experimentId = e.target.value;
                  setSelectedTrainingExperimentId(experimentId);
                  const picked = experimentsForReadyGame.find((exp) => exp.experiment_id === experimentId);
                  if (picked) {
                    setTrainParams((prev) => ({ ...prev, ...experimentToTrainParams(picked) }));
                  }
                }}
                disabled={trainStatus === 'in progress' || experimentsForReadyGame.length === 0}
              >
                <option value="">Select experiment</option>
                {experimentsForReadyGame.map((exp) => (
                  <option key={exp.experiment_id} value={exp.experiment_id}>
                    {`${exp.experiment_id} | Accuracy: ${(Number(exp.score ?? exp.final_accuracy ?? exp.accuracy ?? 0) * 100).toFixed(2)}% | ${formatExperimentTimestamp(exp)}`}
                  </option>
                ))}
              </select>
            </div>

            {selectedTrainingExperiment && (
              <div className="alert alert-info mb-3">
                <strong>Experiment Description:</strong>{' '}
                {selectedTrainingExperiment.description || 'No description available for this experiment.'}
              </div>
            )}
            
            {/* Training Parameters */}
            <div className="mb-3 training-params-panel">
              <h6 className="mb-2">Training Parameters</h6>
              <div className="row g-2">
                <div className="col-md-6">
                  <label className="form-label small" title="Fraction of draws used for training (10–50%)">Train Split (%)</label>
                  <input 
                    type="number" 
                    className="form-control form-control-sm"
                    value={Math.round(normalizeTrainSizeFraction(trainParams.testSize) * 100)}
                    onChange={(e) => setTrainParams({
                      ...trainParams,
                      testSize: normalizeTrainSizeFraction(parseFloat(e.target.value), 0.25),
                    })}
                    min="10"
                    max="50"
                    step="5"
                    disabled={trainStatus === 'in progress'}
                  />
                </div>
                <div className="col-md-6">
                  <label className="form-label small">N Estimators</label>
                  <input 
                    type="number" 
                    className="form-control form-control-sm"
                    value={normalizeNEstimators(trainParams.nEstimators)}
                    onChange={(e) => setTrainParams({
                      ...trainParams,
                      nEstimators: normalizeNEstimators(parseInt(e.target.value, 10)),
                    })}
                    min="50"
                    max="600"
                    step="50"
                    disabled={trainStatus === 'in progress'}
                  />
                </div>
                <div className="col-md-6">
                  <label className="form-label small">Max Depth</label>
                  <input 
                    type="number" 
                    className="form-control form-control-sm"
                    value={normalizeMaxDepth(trainParams.maxDepth)}
                    onChange={(e) => setTrainParams({
                      ...trainParams,
                      maxDepth: normalizeMaxDepth(parseInt(e.target.value, 10)),
                    })}
                    min="4"
                    max="32"
                    step="1"
                    disabled={trainStatus === 'in progress'}
                  />
                </div>
                <div className="col-md-6">
                  <label className="form-label small">Max Iterations</label>
                  <input
                    type="number"
                    className="form-control form-control-sm"
                    value={normalizeMaxIterations(trainParams.maxIterations)}
                    onChange={(e) => setTrainParams({
                      ...trainParams,
                      maxIterations: normalizeMaxIterations(parseInt(e.target.value, 10)),
                    })}
                    min="10"
                    max="100"
                    step="5"
                    disabled={trainStatus === 'in progress'}
                  />
                </div>
                <div className="col-md-6">
                  <label className="form-label small">Target Accuracy (%)</label>
                  <input
                    type="number"
                    className="form-control form-control-sm"
                    value={(trainParams.targetAccuracy ?? 0.90) * 100}
                    onChange={(e) => setTrainParams({...trainParams, targetAccuracy: parseFloat(e.target.value) / 100})}
                    min="50"
                    max="99"
                    step="1"
                    disabled={trainStatus === 'in progress'}
                  />
                </div>
                <div className="col-md-6">
                  <label className="form-label small">Random State</label>
                  <input 
                    type="number" 
                    className="form-control form-control-sm"
                    value={trainParams.randomState}
                    onChange={(e) => setTrainParams({...trainParams, randomState: parseInt(e.target.value)})}
                    disabled={trainStatus === 'in progress'}
                  />
                </div>
                <div className="col-md-6">
                  <label className="form-label small">Window Size (draws)</label>
                  <input
                    type="number"
                    className="form-control form-control-sm"
                    value={trainParams.windowSize}
                    onChange={(e) => setTrainParams({ ...trainParams, windowSize: parseInt(e.target.value, 10) })}
                    min="1"
                    max="8"
                    step="1"
                    disabled={trainStatus === 'in progress'}
                  />
                </div>
                <div className="col-md-6">
                  <label className="form-label small">Blend Step (prior mix)</label>
                  <input
                    type="number"
                    className="form-control form-control-sm"
                    value={trainParams.blendStep}
                    onChange={(e) => setTrainParams({ ...trainParams, blendStep: parseFloat(e.target.value) })}
                    min="0.01"
                    max="0.5"
                    step="0.01"
                    disabled={trainStatus === 'in progress'}
                  />
                </div>
                <div className="col-md-6 d-flex align-items-end">
                  <div className="form-check mb-2">
                    <input
                      className="form-check-input"
                      type="checkbox"
                      id="autoTuneCheck"
                      checked={Boolean(trainParams.autoTune)}
                      onChange={(e) => setTrainParams({ ...trainParams, autoTune: e.target.checked })}
                      disabled={trainStatus === 'in progress'}
                    />
                    <label className="form-check-label small" htmlFor="autoTuneCheck">Auto-tune train split</label>
                  </div>
                </div>
              </div>
            </div>

            {trainStatus === 'in progress' && (
              <div className="mb-3">
                <ProgressiveProgressBar
                  current={trainProgress}
                  total={100}
                  status="active"
                  label="Training Progress"
                  showMetadata={true}
                  startTime={trainStartTime}
                  colorScheme="success"
                />
              </div>
            )}
            <button 
              className="btn btn-success" 
              onClick={startTrain} 
              disabled={trainStatus === 'in progress' || effectiveIngestStatus !== 'completed' || !selectedTrainGame || selectedTrainGameStoredDraws <= 0}
            >
              {trainStatus === 'in progress' ? 'Training...' : 'Start Training'}
            </button>
            {renderCardErrorAlert('Training Error', trainErrorMessage, 'mt-3 mb-0')}
          </ExpandableCard>
        </div>

        {/* Suggestion Panel */}
        <div className={getFocusColClass('predict')}>
          <ExpandableCard
            title="Suggestions"
            cardKey="predict"
            focusedCard={expandedCard}
            neonBorder={true}
            metadata={{
              'Status': isTrained ? 'Model Ready' : 'Awaiting Training',
              'Experiments': experiments.length
            }}
            onToggle={handleCardFocus('predict')}
          >
            <PredictionPanel games={games} disabled={!isTrained} />
          </ExpandableCard>
        </div>

        {/* Game Summary */}
        <div className={getFocusColClass('games')}>
          <ExpandableCard
            title="Game Contents"
            cardKey="games"
            focusedCard={expandedCard}
            neonBorder={true}
            metadata={{
              'Total Games': games.length,
              'Total Draws': Object.values(gameContents).reduce((a, b) => a + b, 0),
              ...(gameContentsErrorMessage ? { 'Error Message': gameContentsErrorMessage } : {})
            }}
            onToggle={handleCardFocus('games')}
          >
            {renderCardErrorAlert('Game Content Error', gameContentsErrorMessage, 'mb-3')}
            <GameSummaryPanel games={games} refreshKey={summaryRefreshKey} initialSummaries={gameContents} />
          </ExpandableCard>
        </div>

        {/* ChromaDB Status */}
        <div className={getFocusColClass('chroma')}>
          <ExpandableCard
            title="ChromaDB Collections"
            cardKey="chroma"
            focusedCard={expandedCard}
            neonBorder={true}
            metadata={{
              'Host': 'mensa_chroma',
              'Port': '8000'
            }}
            onToggle={handleCardFocus('chroma')}
          >
            <ChromaStatusPanel />
          </ExpandableCard>
        </div>

        {/* Experiments */}
        <div className={getFocusColClass('experiments')}>
          <ExpandableCard
            title="Training Experiments"
            cardKey="experiments"
            focusedCard={expandedCard}
            neonBorder={true}
            metadata={{
              'Total Experiments': experiments.length,
              'Last Updated': new Date().toLocaleString(),
              ...(experimentsErrorMessage ? { 'Error Message': experimentsErrorMessage } : {})
            }}
            onToggle={handleCardFocus('experiments')}
          >
            {renderCardErrorAlert('Experiments Error', experimentsErrorMessage, 'mb-3')}
            <ExperimentsPanel experiments={experiments} />
          </ExpandableCard>
        </div>
      </div>
    </div>
  );
}
