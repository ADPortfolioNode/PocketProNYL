// Live pipeline activity tracker for the data-flow view.
//
// Observes the app's own API calls (axios + fetch + EventSource) without changing
// them: requests still go out exactly as before, this module only counts which
// pipeline stage is busy and remembers the last outcome per stage.
//
// Stages: ingest -> agents (strategies & agents) -> ensemble -> prediction.

import axios from 'axios';

export const STAGES = ['ingest', 'agents', 'ensemble', 'prediction'];

const ROUTES = [
  // order matters: first match wins
  { re: /\/api\/(ingest_stream|ingest|startup_init)(\b|\/|\?|$)/, stages: ['ingest'] },
  { re: /\/api\/(train_all|train)(\b|\/|\?|$)/, stages: ['agents'], method: 'post' },
  { re: /\/api\/(predict_all|predict)(\b|\/|\?|$)/, stages: ['agents', 'ensemble', 'prediction'], method: 'post' },
  { re: /\/api\/chat(\b|\/|\?|$)/, stages: ['agents'], method: 'post' },
];

const state = {
  inflight: { ingest: 0, agents: 0, ensemble: 0, prediction: 0 },
  last: { ingest: null, agents: null, ensemble: null, prediction: null },
};
const listeners = new Set();

function emit() {
  const snapshot = getFlowActivity();
  listeners.forEach((fn) => {
    try { fn(snapshot); } catch (e) { /* listener errors never break API calls */ }
  });
}

export function getFlowActivity() {
  return {
    inflight: { ...state.inflight },
    last: { ...state.last },
  };
}

export function subscribeFlowActivity(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function classifyRequest(url, method) {
  if (!url) return null;
  const m = String(method || 'get').toLowerCase();
  let path = String(url);
  try {
    path = new URL(path, typeof window !== 'undefined' ? window.location.href : 'http://localhost').pathname;
  } catch (e) { /* keep raw */ }
  for (const route of ROUTES) {
    if (route.method && route.method !== m) continue;
    if (route.re.test(path)) return route.stages;
  }
  return null;
}

export function beginStages(stages) {
  if (!stages) return () => {};
  stages.forEach((s) => { state.inflight[s] += 1; });
  emit();
  let finished = false;
  return (ok) => {
    if (finished) return;
    finished = true;
    const at = Date.now();
    stages.forEach((s) => {
      state.inflight[s] = Math.max(0, state.inflight[s] - 1);
      state.last[s] = { ok: !!ok, at };
    });
    emit();
  };
}

let installed = false;

export function installFlowActivity() {
  if (installed || typeof window === 'undefined') return;
  installed = true;

  // axios (most of the dashboard)
  axios.interceptors.request.use((config) => {
    const stages = classifyRequest(config.url, config.method);
    if (stages) config.__flowDone = beginStages(stages);
    return config;
  });
  axios.interceptors.response.use(
    (response) => {
      if (response && response.config && response.config.__flowDone) {
        const body = response.data || {};
        const failed = typeof body === 'object' && String(body.status || '').toLowerCase() === 'error';
        response.config.__flowDone(!failed);
      }
      return response;
    },
    (error) => {
      if (error && error.config && error.config.__flowDone) error.config.__flowDone(false);
      return Promise.reject(error);
    },
  );

  // fetch (chat, auth wrapper) — wraps whatever fetch is installed, keys included
  if (typeof window.fetch === 'function') {
    const prevFetch = window.fetch;
    window.fetch = (input, init) => {
      const url = typeof input === 'string' ? input : input && input.url;
      const method = (init && init.method) || (input && input.method) || 'get';
      const done = beginStages(classifyRequest(url, method));
      return prevFetch(input, init).then(
        (res) => { done(res && res.ok); return res; },
        (err) => { done(false); throw err; },
      );
    };
  }

  // EventSource (live ingest stream): active while open
  if (typeof window.EventSource === 'function') {
    const Prev = window.EventSource;
    const Tracked = function (url, config) {
      const es = new Prev(url, config);
      const stages = classifyRequest(url, 'get');
      if (stages) {
        const done = beginStages(stages);
        const origClose = es.close.bind(es);
        es.close = () => { done(true); origClose(); };
        es.addEventListener('error', () => {
          if (es.readyState === Prev.CLOSED) done(false);
        });
      }
      return es;
    };
    Tracked.prototype = Prev.prototype;
    Tracked.CONNECTING = Prev.CONNECTING;
    Tracked.OPEN = Prev.OPEN;
    Tracked.CLOSED = Prev.CLOSED;
    window.EventSource = Tracked;
  }
}

// test helper
export function __resetFlowActivity() {
  STAGES.forEach((s) => { state.inflight[s] = 0; state.last[s] = null; });
  emit();
}
