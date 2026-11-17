import React, { useState, useEffect, useRef } from 'react';
import { marked } from 'marked';
import Prism from 'prismjs';
import 'prismjs/themes/prism.css';
import './ChatPanelRAG.css';
import getApiBase from '../utils/apiBase';
import { formatApiError } from '../utils/errorUtils';

const API_BASE = getApiBase();
const CHAT_REQUEST_TIMEOUT_MS = 120000;

const LM_OPTIONS = [
  { value: 'auto', label: 'Auto' },
  { value: 'gemini', label: 'Gemini' },
  { value: 'chatgpt', label: 'ChatGPT' },
];

const GAME_DISPLAY_NAMES = {
  take5: 'Take 5',
  pick3: 'Pick 3',
  powerball: 'Powerball',
  megamillions: 'Mega Millions',
  pick10: 'Pick 10',
  cash4life: 'Cash4Life',
  quickdraw: 'Quick Draw',
  nylotto: 'NY Lotto',
  win4: 'Win 4',
};

const STARTER_CHIPS = [
  "What's hot in Take 5 lately?",
  'How do I train Pick 3 without a gateway timeout?',
  'Which games are ready for suggestions?',
  'Train all games',
  'Optimize suggestions for Take 5',
  'Tune all games for the next suggestion pass',
];

async function fetchWithTimeout(url, options = {}, timeoutMs = CHAT_REQUEST_TIMEOUT_MS) {
  const controller = new AbortController();
  const timeoutId = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...options, signal: controller.signal });
  } catch (error) {
    if (error?.name === 'AbortError') {
      throw new Error(`Request timed out after ${Math.round(timeoutMs / 1000)}s`);
    }
    throw error;
  } finally {
    window.clearTimeout(timeoutId);
  }
}

function buildGreeting(selectedGame) {
  const key = String(selectedGame || '').toLowerCase();
  const label = GAME_DISPLAY_NAMES[key] || (key ? key.toUpperCase() : 'all games');
  return (
    `Hi — I'm PocketPro Concierge. Ask about draws, training, suggestions, tuning, or errors`
    + (key ? ` for **${label}**` : '')
    + `. Toggle RAG to ground answers in Chroma history.`
  );
}

function renderMarkdown(text) {
  if (typeof text !== 'string') return '';
  return marked(text);
}

function providerLabel(value) {
  return LM_OPTIONS.find((opt) => opt.value === value)?.label || value || 'Auto';
}

function isAllGamesScope(value) {
  const token = String(value || '').trim().toLowerCase();
  const compact = token.replace(/[^a-z0-9]+/g, '');
  return !token || ['all', '*', 'all_games', '__all_games__'].includes(token) || compact === 'all' || compact === 'allgames';
}

function detectGameFromPrompt(text, selectedGame) {
  const normalized = String(text || '').toLowerCase();
  if (!normalized) return isAllGamesScope(selectedGame) ? 'all' : (selectedGame || 'all');

  const aliases = {
    take5: ['take 5', 'take5', 'take-five'],
    pick3: ['pick 3', 'pick3'],
    powerball: ['powerball'],
    megamillions: ['mega millions', 'megamillions'],
    pick10: ['pick 10', 'pick10'],
    cash4life: ['cash 4 life', 'cash4life'],
    quickdraw: ['quick draw', 'quickdraw'],
    nylotto: ['ny lotto', 'nylotto'],
    win4: ['win 4', 'win4', 'win-four'],
  };

  for (const [gameKey, patterns] of Object.entries(aliases)) {
    if (patterns.some((pattern) => normalized.includes(pattern))) {
      return gameKey;
    }
  }

  if (/(all games|every game|all lottery games)/i.test(normalized) || isAllGamesScope(selectedGame)) {
    return 'all';
  }

  return selectedGame || 'all';
}

function scopeLabel(game) {
  const key = String(game || '').toLowerCase();
  return GAME_DISPLAY_NAMES[key] || (key ? key.toUpperCase() : 'All games');
}

// RAG process display. This is a process panel, never bubble text: the
// bubble stays message-only and sources stay in the "View sources"
// list. Steps show only real request facts — the live box shows the
// step the request is actually in (retrieval runs first, synchronously,
// before generation, so "Retrieving…" is the honest live step), and the
// completed box is built from the backend rag_trace.
function RagProcessSteps({ trace, live = false }) {
  if (!trace) return null;
  const steps = [];
  if (trace.tool) {
    steps.push({ state: live ? 'active' : 'done', text: 'Tool action — no RAG retrieval' });
  } else if (!trace.ragEnabled) {
    steps.push({ state: live ? 'active' : 'done', text: 'RAG OFF — answering without retrieval' });
  } else if (live) {
    steps.push({ state: 'done', text: 'Question received' });
    steps.push({ state: 'active', text: `Retrieving from Chroma draw history (${trace.scope || 'All games'})` });
    steps.push({ state: 'pending', text: 'Generating answer' });
  } else if (trace.retrieval === 'failed') {
    steps.push({ state: 'failed', text: 'Retrieval failed — answer is not Chroma-grounded' });
  } else if (trace.retrieval === 'skipped') {
    steps.push({ state: 'done', text: 'No retrieval run for this answer' });
  } else if (trace.sources_count > 0) {
    steps.push({ state: 'done', text: `Retrieved ${trace.sources_count} chunk${trace.sources_count === 1 ? '' : 's'} from ${trace.scope || 'All games'}` });
  } else {
    steps.push({ state: 'warn', text: `No chunks returned for ${trace.scope || 'All games'} — answer is not Chroma-grounded` });
  }
  if (!live && !trace.tool && trace.provider) {
    steps.push({
      state: trace.fallback ? 'warn' : 'done',
      text: trace.fallback
        ? 'Language models unavailable — answered from retrieved text'
        : `Answered by ${providerLabel(trace.provider)}`,
    });
  }
  const mark = (state) => (state === 'done' ? '✓' : state === 'failed' ? '✕' : state === 'warn' ? '!' : state === 'active' ? '…' : '○');
  return (
    <div className="rag-process" aria-label="RAG process">
      <div className="rag-process-title">{live ? 'RAG process — running' : 'RAG process'}</div>
      <ul className="rag-process-steps">
        {steps.map((step, i) => (
          <li key={i} className={`rag-process-step ${step.state}`}>
            <span className="rag-process-mark">{mark(step.state)}</span>
            <span>{step.text}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

const ChatPanelRAG = ({ game = null, isExpanded = false, onActivityChange = null, prefill = null }) => {
  const [messages, setMessages] = useState(() => ([
    { sender: 'bot', text: buildGreeting(game), sources: [] },
  ]));
  const [input, setInput] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [pendingTrace, setPendingTrace] = useState(null);
  const [useRag, setUseRag] = useState(true);
  const [lmProvider, setLmProvider] = useState(() => {
    try {
      return localStorage.getItem('pocketpro.chat.lmProvider') || 'auto';
    } catch {
      return 'auto';
    }
  });
  const messagesEndRef = useRef(null);
  const greetedGameRef = useRef(game);
  const prefillRef = useRef(null);

  // Ingest → chat link from the Dashboard: a prefill object fills the
  // composer (it never auto-sends) so the user can ask about the
  // latest ingest without leaving the Dashboard.
  useEffect(() => {
    if (!prefill || prefill.at === prefillRef.current || isLoading) return;
    prefillRef.current = prefill.at;
    setInput(prefill.text || '');
  }, [prefill, isLoading]);

  useEffect(() => {
    Prism.highlightAll();
  }, [messages]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, isLoading]);

  useEffect(() => {
    if (onActivityChange) onActivityChange(isLoading);
  }, [isLoading, onActivityChange]);

  useEffect(() => {
    try {
      localStorage.setItem('pocketpro.chat.lmProvider', lmProvider);
    } catch {
      /* ignore */
    }
  }, [lmProvider]);

  // Refresh greeting only when game changes and chat is still on the initial bot message.
  useEffect(() => {
    if (greetedGameRef.current === game) return;
    greetedGameRef.current = game;
    setMessages((prev) => {
      if (prev.length === 1 && prev[0]?.sender === 'bot') {
        return [{ sender: 'bot', text: buildGreeting(game), sources: [] }];
      }
      return prev;
    });
  }, [game]);

  const sendMessage = async (rawText) => {
    const text = String(rawText || '').trim();
    if (!text || isLoading) return;

    const isQuestion = /\?/.test(text) || /^(how|what|why|when|where|can|could|should|do i|please explain)\b/i.test(text);
    const trainIntent = !isQuestion && /\b(retrain|start training|train)\b/i.test(text);
    const tuningIntent = /(optimi[sz]e|tune|tuning|retune|re-tune|suggestion)/i.test(text);
    const toolGame = detectGameFromPrompt(text, game);

    setMessages((prev) => [...prev, { sender: 'user', text }]);
    setInput('');
    setPendingTrace(
      (trainIntent && !tuningIntent) || tuningIntent
        ? { tool: true, ragEnabled: useRag, scope: scopeLabel(toolGame) }
        : { tool: false, ragEnabled: useRag, scope: scopeLabel(game) },
    );
    setIsLoading(true);

    try {
      let payload;
      if (trainIntent && !tuningIntent) {
        payload = {
          text,
          tool: {
            name: 'train_models',
            params: { game: toolGame === 'all' ? 'all' : toolGame },
          },
        };
      } else if (tuningIntent) {
        payload = {
          text,
          tool: {
            name: 'optimize_suggestions',
            params: { game: toolGame === 'all' ? 'all' : toolGame },
          },
        };
      } else {
        payload = {
          text,
          game: isAllGamesScope(game) ? null : game,
          use_rag: useRag,
          lm_provider: lmProvider || 'auto',
        };
      }

      const response = await fetchWithTimeout(`${API_BASE}/api/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });

      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(formatApiError({ response: { data } }, `Request failed (${response.status})`));
      }

      setMessages((prev) => [
        ...prev,
        {
          sender: 'bot',
          text: data.response || "Sorry, I didn't get a response.",
          sources: data.sources || [],
          contextUsed: Boolean(data.context_used),
          sourcesCount: data.sources_count || 0,
          lmProvider: data.lm_provider || lmProvider,
          ragTrace: data.rag_trace ? {
            tool: Boolean(data.tool_name),
            ragEnabled: data.rag_trace.rag_enabled,
            retrieval: data.rag_trace.retrieval,
            reason: data.rag_trace.reason || null,
            scope: data.rag_trace.scope,
            sources_count: data.rag_trace.sources_count || 0,
            provider: data.rag_trace.provider || null,
            fallback: Boolean(data.rag_trace.fallback),
          } : null,
        },
      ]);
    } catch (error) {
      setMessages((prev) => [
        ...prev,
        {
          sender: 'bot',
          text: `Error: ${error?.message || 'Could not reach the chat service.'}`,
          sources: [],
          ragTrace: {
            ragEnabled: useRag,
            retrieval: 'failed',
            scope: scopeLabel(game),
            sources_count: 0,
            provider: null,
            fallback: false,
          },
        },
      ]);
    } finally {
      setPendingTrace(null);
      setIsLoading(false);
    }
  };

  const clearChat = () => {
    if (isLoading) return;
    setMessages([{ sender: 'bot', text: buildGreeting(game), sources: [] }]);
  };

  const panelClassName = `chat-panel-rag ${isExpanded || isLoading ? 'is-expanded' : 'is-compact'}`;

  return (
    <div className={panelClassName}>
      <div className="chat-header-rag">
        <div className="chat-header-left">
          <h3>Concierge</h3>
          {game && <span className="chat-game-badge">{String(game).toUpperCase()}</span>}
        </div>
        <div className="chat-header-right">
          <label className="chat-select-label" htmlFor="chat-lm-provider">
            Model
            <select
              id="chat-lm-provider"
              className="chat-select"
              value={lmProvider}
              onChange={(e) => setLmProvider(e.target.value)}
              disabled={isLoading}
            >
              {LM_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>{opt.label}</option>
              ))}
            </select>
          </label>
          <label className="rag-toggle">
            <input
              type="checkbox"
              checked={useRag}
              onChange={() => setUseRag((v) => !v)}
              disabled={isLoading}
            />
            <span className={`toggle-label ${useRag ? 'active' : ''}`}>
              RAG {useRag ? 'ON' : 'OFF'}
            </span>
          </label>
          <button
            type="button"
            className="chat-clear-btn"
            onClick={clearChat}
            disabled={isLoading}
            title="Clear conversation"
          >
            Clear
          </button>
        </div>
      </div>

      <div className="chat-window-rag">
        {messages.map((msg, index) => (
          <div key={index} className={`chat-message-rag ${msg.sender}`}>
            <div className="message-container">
              <div className="message-bubble">
                {msg.sender === 'user' ? (
                  msg.text
                ) : (
                  <div dangerouslySetInnerHTML={{ __html: renderMarkdown(msg.text) }} />
                )}
              </div>
              {msg.sender === 'bot' && (
                <div className="message-meta">
                  {msg.lmProvider && (
                    <span className="meta-chip">{providerLabel(msg.lmProvider)}</span>
                  )}
                  {msg.contextUsed && msg.sources?.length > 0 && (
                    <span className="sources-badge">
                      {msg.sources.length} source{msg.sources.length === 1 ? '' : 's'}
                    </span>
                  )}
                </div>
              )}
            </div>

            {msg.sources?.length > 0 && (
              <details className="message-sources">
                <summary>View sources</summary>
                {msg.sources.map((source, i) => (
                  <div key={i} className="source-item">
                    {source.game && <span className="source-game">{source.game}</span>}
                    <span className="source-content">{source.content}</span>
                    {typeof source.distance === 'number' && (
                      <span className="source-distance">
                        Score: {(1 - source.distance).toFixed(3)}
                      </span>
                    )}
                  </div>
                ))}
              </details>
            )}

            {msg.sender === 'bot' && msg.ragTrace && (
              <RagProcessSteps trace={msg.ragTrace} />
            )}
          </div>
        ))}

        {isLoading && pendingTrace && (
          <RagProcessSteps trace={pendingTrace} live />
        )}

        {isLoading && (
          <div className="chat-message-rag bot">
            <div className="message-bubble loading">
              <span className="spinner-dot" />
              <span className="spinner-dot" />
              <span className="spinner-dot" />
            </div>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          sendMessage(input);
        }}
        className="chat-input-form-rag"
      >
        {messages.length <= 1 && !isLoading && (
          <div className="chat-chips" aria-label="Starter questions">
            {STARTER_CHIPS.map((chip) => (
              <button
                key={chip}
                type="button"
                className="chat-chip"
                onClick={() => sendMessage(chip)}
              >
                {chip}
              </button>
            ))}
          </div>
        )}
        <div className="input-wrapper">
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder={useRag ? 'Ask about lottery data or workflow issues…' : 'Ask anything…'}
            disabled={isLoading}
            className="chat-input-rag"
            aria-label="Chat message input"
          />
          <button
            type="submit"
            disabled={isLoading || !input.trim()}
            className="chat-send-btn"
          >
            {isLoading ? '…' : 'Send'}
          </button>
        </div>
      </form>
    </div>
  );
};

export default ChatPanelRAG;
