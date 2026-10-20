import React, { useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import getApiBase from '../utils/apiBase';
import { getFlowActivity, subscribeFlowActivity } from '../utils/flowActivity';
import '../styles/dataflow.css';

const ACTIVE_WORDS = ['ingesting', 'running', 'queued', 'fetching', 'in progress', 'in_progress', 'training', 'tuning', 'started'];
const DONE_WORDS = ['completed', 'complete', 'done', 'success', 'ready', 'trained', 'idle_trained'];

const isActive = (s) => ACTIVE_WORDS.includes(String(s || '').toLowerCase());
const isDone = (s) => DONE_WORDS.includes(String(s || '').toLowerCase());
const isError = (s) => ['error', 'failed', 'failure'].includes(String(s || '').toLowerCase());

const STAGE_META = [
  { key: 'ingest', label: 'Ingest', glyph: '⇣', hint: 'NY Open Data draws into ChromaDB' },
  { key: 'agents', label: 'Strategies & Agents', glyph: '◈', hint: 'RF, frequency, overdue, hot/cold, hybrid' },
  { key: 'ensemble', label: 'Ensemble', glyph: '⟁', hint: 'Weighted blend of model votes' },
  { key: 'prediction', label: 'Prediction', glyph: '✦', hint: 'Ranked number suggestions' },
];

const STATUS_TEXT = { idle: 'Standby', ready: 'Ready', active: 'Live', done: 'Done', error: 'Check' };

function timeAgo(ts) {
  if (!ts) return '';
  const s = Math.max(0, Math.round((Date.now() - ts) / 1000));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  return `${Math.round(s / 3600)}h ago`;
}

export function deriveStages({ startup, train, activity }) {
  const inflight = (activity && activity.inflight) || {};
  const last = (activity && activity.last) || {};
  const out = {};

  // Ingest
  const st = startup || {};
  const ingestStatus = String(st.status || '').toLowerCase();
  const populated = Number(st.populated_games || st.completed_games || 0);
  const total = Number(st.total || (st.available_games || []).length || 0);
  if (inflight.ingest > 0 || isActive(ingestStatus)) {
    const rows = Number(st.current_game_rows_fetched || 0);
    const rowsTotal = Number(st.current_game_rows_total || 0);
    out.ingest = {
      status: 'active',
      detail: st.current_game ? `${st.current_game}${rowsTotal ? ` · ${rows.toLocaleString()} / ${rowsTotal.toLocaleString()} rows` : ''}` : 'Pulling draw history',
      percent: typeof st.percent_complete === 'number' ? st.percent_complete : null,
    };
  } else if (isError(ingestStatus) || (last.ingest && last.ingest.ok === false)) {
    out.ingest = { status: 'error', detail: 'Last ingest reported an error' };
  } else if (populated > 0) {
    out.ingest = { status: 'done', detail: `${populated}${total ? ` of ${total}` : ''} games loaded`, percent: total ? (populated / total) * 100 : 100 };
  } else {
    out.ingest = { status: 'idle', detail: startup ? 'No draw data yet' : 'Connecting…' };
  }

  // Strategies & agents (training, tuning, chat)
  const jobs = Object.values((train && train.jobs) || {});
  const running = jobs.filter((j) => isActive(j.status));
  const tuning = st.tuning || {};
  const trained = jobs.filter((j) => isDone(j.status)).length;
  if (running.length) {
    out.agents = { status: 'active', detail: `Training ${running.map((j) => j.game).filter(Boolean).slice(0, 3).join(', ') || 'models'}` };
  } else if (isActive(tuning.status)) {
    out.agents = { status: 'active', detail: `Tuning ${tuning.current_game || tuning.game || 'weights'}` };
  } else if (inflight.agents > 0) {
    out.agents = { status: 'active', detail: 'Agents scoring the draw history' };
  } else if (jobs.some((j) => isError(j.status)) || (last.agents && last.agents.ok === false)) {
    out.agents = { status: 'error', detail: 'A strategy run reported an error' };
  } else if (trained || (last.agents && last.agents.ok)) {
    out.agents = { status: 'done', detail: trained ? `${trained} model${trained === 1 ? '' : 's'} trained this session` : `Ran ${timeAgo(last.agents.at)}` };
  } else {
    out.agents = { status: populated > 0 ? 'ready' : 'idle', detail: populated > 0 ? 'Strategies standing by' : 'Waiting for data' };
  }

  // Ensemble + Prediction follow the prediction calls
  ['ensemble', 'prediction'].forEach((k) => {
    if (inflight[k] > 0) {
      out[k] = { status: 'active', detail: k === 'ensemble' ? 'Blending strategy votes' : 'Ranking suggestions' };
    } else if (last[k] && last[k].ok === false) {
      out[k] = { status: 'error', detail: 'Last request failed' };
    } else if (last[k] && last[k].ok) {
      out[k] = { status: 'done', detail: `${k === 'ensemble' ? 'Blended' : 'Suggested'} ${timeAgo(last[k].at)}` };
    } else {
      out[k] = { status: populated > 0 ? 'ready' : 'idle', detail: populated > 0 ? 'Ready on request' : 'Waiting for data' };
    }
  });

  return out;
}

export default function DataFlowPipeline({ pollMs = 5000, idlePollMs = 15000 }) {
  const [startup, setStartup] = useState(null);
  const [train, setTrain] = useState(null);
  const [activity, setActivity] = useState(getFlowActivity());
  const [online, setOnline] = useState(true);
  const [, setTick] = useState(0);
  const anyActiveRef = useRef(false);

  useEffect(() => subscribeFlowActivity(setActivity), []);

  // keep "x s ago" labels fresh
  useEffect(() => {
    const id = setInterval(() => setTick((t) => t + 1), 15000);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    let cancelled = false;
    let timer = null;
    const base = getApiBase();
    const poll = async () => {
      if (typeof document !== 'undefined' && document.hidden) {
        timer = setTimeout(poll, idlePollMs);
        return;
      }
      const [s, t] = await Promise.allSettled([
        axios.get(`${base}/api/startup_status`, { timeout: 10000 }),
        axios.get(`${base}/api/train_status`, { timeout: 10000 }),
      ]);
      if (cancelled) return;
      if (s.status === 'fulfilled') setStartup(s.value.data || {});
      if (t.status === 'fulfilled') setTrain(t.value.data || {});
      setOnline(s.status === 'fulfilled' || t.status === 'fulfilled');
      timer = setTimeout(poll, anyActiveRef.current ? pollMs : idlePollMs);
    };
    poll();
    return () => { cancelled = true; if (timer) clearTimeout(timer); };
  }, [pollMs, idlePollMs]);

  const stages = useMemo(() => deriveStages({ startup, train, activity }), [startup, train, activity]);
  const anyActive = Object.values(stages).some((s) => s.status === 'active');
  anyActiveRef.current = anyActive;

  return (
    <section className={`dataflow card mb-4 ${anyActive ? 'is-live' : ''}`} aria-label="Data flow pipeline">
      <header className="dataflow__head">
        <div>
          <p className="dataflow__eyebrow">Pipeline</p>
          <h4 className="dataflow__title">Data flow</h4>
        </div>
        <span className={`dataflow__beacon ${online ? (anyActive ? 'is-live' : 'is-idle') : 'is-offline'}`} role="status">
          <i aria-hidden="true" />
          {online ? (anyActive ? 'Processing' : 'Idle') : 'Backend offline'}
        </span>
      </header>

      <ol className="dataflow__track">
        {STAGE_META.map((meta, i) => {
          const s = stages[meta.key];
          const next = STAGE_META[i + 1] && stages[STAGE_META[i + 1].key];
          const flowing = s.status === 'active' || (next && next.status === 'active');
          return (
            <React.Fragment key={meta.key}>
              <li className={`dataflow__node is-${s.status}`} data-testid={`flow-${meta.key}`} title={meta.hint}>
                <span className="dataflow__glyph" aria-hidden="true">{meta.glyph}</span>
                <span className="dataflow__label">{meta.label}</span>
                <span className="dataflow__chip">{STATUS_TEXT[s.status]}</span>
                <span className="dataflow__detail">{s.detail}</span>
                {typeof s.percent === 'number' && (
                  <span className="dataflow__meter" aria-hidden="true">
                    <span style={{ width: `${Math.max(2, Math.min(100, s.percent))}%` }} />
                  </span>
                )}
              </li>
              {next && (
                <li className={`dataflow__link ${flowing ? 'is-flowing' : ''}`} aria-hidden="true">
                  <span /><span /><span />
                </li>
              )}
            </React.Fragment>
          );
        })}
      </ol>
    </section>
  );
}
