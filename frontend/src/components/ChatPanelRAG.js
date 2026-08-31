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
};

const STARTER_CHIPS = [
  "What's hot in Take 5 lately?",
  'How do I train Pick 3 without a gateway timeout?',
  'Which games are ready for suggestions?',
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
    `Hi — I'm PocketPro Concierge. Ask about draws, training, suggestions, or errors`
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

const ChatPanelRAG = ({ game = null, isExpanded = false, onActivityChange = null }) => {
  const [messages, setMessages] = useState(() => ([
    { sender: 'bot', text: buildGreeting(game), sources: [] },
  ]));
  const [input, setInput] = useState('');
  const [isLoading, setIsLoading] = useState(false);
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

    setMessages((prev) => [...prev, { sender: 'user', text }]);
    setInput('');
    setIsLoading(true);

    try {
      const response = await fetchWithTimeout(`${API_BASE}/api/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          text,
          game,
          use_rag: useRag,
          lm_provider: lmProvider || 'auto',
        }),
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
        },
      ]);
    } catch (error) {
      setMessages((prev) => [
        ...prev,
        {
          sender: 'bot',
          text: `Error: ${error?.message || 'Could not reach the chat service.'}`,
          sources: [],
        },
      ]);
    } finally {
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
          </div>
        ))}

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
