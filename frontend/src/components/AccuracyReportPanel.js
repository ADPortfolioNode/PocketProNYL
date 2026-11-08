import React, { useEffect, useRef, useState } from 'react';
import axios from 'axios';
import getApiBase from '../utils/apiBase';
import CandyStripeProgress from './CandyStripeProgress';
import './InsightPanels.css';

const fmtP = (p) => (p === null || p === undefined ? '—' : (p < 0.0001 ? '<0.0001' : Number(p).toFixed(4)));
const fmtNum = (v, digits = 2) => (v === null || v === undefined || Number.isNaN(Number(v)) ? '—' : Number(v).toFixed(digits));

// Lottery draws are scheduled in America/New_York; served_at is stored
// in UTC, so the suggestion time is shown in lottery time.
const fmtServedAt = (servedAt) => {
  if (!servedAt) return null;
  try {
    const d = new Date(servedAt);
    if (Number.isNaN(d.getTime())) return String(servedAt);
    return new Intl.DateTimeFormat('en-US', {
      timeZone: 'America/New_York',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: 'numeric',
      minute: '2-digit',
    }).format(d);
  } catch (e) {
    return String(servedAt);
  }
};

const rowKey = (s) => s.id || `${s.served_at}-${(s.numbers || []).join('-')}`;

function Balls({ numbers = [], hits = [] }) {
  const hitSet = new Set(hits);
  return (
    <span>
      {numbers.map((n, i) => (
        <span key={`${n}-${i}`} className={`insight-ball${hitSet.has(n) ? ' insight-ball-hit' : ''}`}>{n}</span>
      ))}
    </span>
  );
}

function Stat({ label, value }) {
  return (
    <div className="insight-stat">
      <div className="insight-stat-label">{label}</div>
      <div className="insight-stat-value">{value}</div>
    </div>
  );
}

function WeightsDetail({ weights = {}, contributions = {} }) {
  const weightEntries = Object.entries(weights || {})
    .filter(([, w]) => w !== null && w !== undefined && !Number.isNaN(Number(w)))
    .sort(([, a], [, b]) => Number(b) - Number(a));
  const contributionEntries = Object.entries(contributions || {})
    .filter(([, nums]) => Array.isArray(nums) && nums.length > 0);
  if (weightEntries.length === 0 && contributionEntries.length === 0) {
    return (
      <p className="insight-muted mb-0">
        No weights recorded for this suggestion (older entries were saved before weights were kept).
      </p>
    );
  }
  return (
    <div>
      {weightEntries.length > 0 && (
        <table className="insight-table">
          <thead>
            <tr>
              <th>Strategy</th>
              <th>Weight</th>
            </tr>
          </thead>
          <tbody>
            {weightEntries.map(([name, w]) => (
              <tr key={name}>
                <td>{name}</td>
                <td>{fmtNum(w, 3)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {contributionEntries.length > 0 && (
        <div className="mt-2">
          {contributionEntries.map(([name, nums]) => (
            <div key={name} className="mb-1">
              <span className="insight-muted">{name}: </span>
              <Balls numbers={nums} />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export function SuggestionDetail({ suggestion, onClose }) {
  if (!suggestion) return null;
  const s = suggestion;
  const targetDate = s.expected_draw_date || s.target_draw_date;
  const targetSession = s.expected_draw_session || s.target_draw_session;
  const isGraded = s.status === 'graded';
  return (
    <div className="insight-stat mt-2" data-testid="suggestion-detail">
      <div className="d-flex align-items-center justify-content-between gap-2">
        <strong>Suggestion detail</strong>
        <button type="button" className="insight-btn" onClick={onClose} data-testid="suggestion-detail-close">
          Close
        </button>
      </div>

      <div className="mt-2">
        <div className="insight-stat-label">Suggested on</div>
        <div>{fmtServedAt(s.served_at) || s.suggestion_date || '—'}</div>
        {s.suggestion_date && fmtServedAt(s.served_at)
          ? <div className="insight-muted">Suggestion date: {s.suggestion_date} (served between draws)</div>
          : null}
      </div>

      <div className="mt-2">
        <div className="insight-stat-label">Suggested numbers</div>
        <div><Balls numbers={s.numbers} hits={s.matched_numbers || []} /></div>
        {(s.bonus_numbers || []).length > 0 && (
          <div className="insight-muted">Bonus: {(s.bonus_numbers || []).join(', ')}</div>
        )}
      </div>

      <div className="mt-2">
        <div className="insight-stat-label">Target draw (immediate next draw after the suggestion)</div>
        <div>
          {targetDate || '—'}
          {targetSession ? <span className="insight-muted"> · {targetSession}</span> : null}
        </div>
      </div>

      <div className="mt-2">
        <div className="insight-stat-label">Drawn</div>
        {isGraded ? (
          <div>
            <Balls numbers={s.actual_numbers} hits={s.matched_numbers || []} />
            {(s.actual_bonus_numbers || []).length > 0 && (
              <span className="insight-muted"> · Bonus: {(s.actual_bonus_numbers || []).join(', ')}</span>
            )}
            <div className="insight-muted">
              {s.actual_draw_date || targetDate || '—'}
              {s.actual_draw_session ? ` · ${s.actual_draw_session}` : ''}
              {s.draw_source === 'verified_official' ? ' · Verified official draw' : ''}
            </div>
          </div>
        ) : (
          <span className="insight-muted">
            {s.status === 'pending' ? 'Draw not in yet — awaiting ' : `${s.reason || s.status} — `}{targetDate || '—'}
            {targetSession ? ` (${targetSession})` : ''}
          </span>
        )}
      </div>

      {isGraded && (
        <div className="mt-2">
          <div className="insight-stat-label">Result</div>
          <div>
            {`${s.hits} hits vs ${fmtNum(s.expected_hits_by_chance)} expected by chance`}
            {` · p-value ${fmtP(s.p_value_at_least_these_hits)}`}
            {s.tier?.tier ? ` · ${s.tier.tier}` : ''}
          </div>
        </div>
      )}

      <div className="mt-2">
        <div className="insight-stat-label">Weights behind this suggestion (repeat it with these)</div>
        <WeightsDetail weights={s.strategy_weights} contributions={s.strategy_contributions} />
      </div>

      <div className="insight-muted mt-2">
        {s.model ? `Model: ${s.model}` : ''}
        {s.model_version ? ` · version ${s.model_version}` : ''}
        {s.source ? ` · source ${s.source}` : ''}
        {s.recent_k !== null && s.recent_k !== undefined ? ` · recent ${s.recent_k} draws` : ''}
      </div>
    </div>
  );
}

export function AccuracyReportView({ report }) {
  const [selectedKey, setSelectedKey] = useState(null);

  useEffect(() => {
    setSelectedKey(null);
  }, [report]);

  if (!report) return null;
  const pooled = report.pooled_last || {};
  const allTime = report.pooled_all_time || {};
  const backtest = report.backtest || {};
  const isReal = report.data_label === 'Real';
  const champion = report.model_champion;
  const suggestions = report.suggestions || [];
  const selected = suggestions.find((s) => rowKey(s) === selectedKey) || null;
  return (
    <div data-testid="accuracy-report">
      <div className="d-flex align-items-center gap-2 flex-wrap">
        <span className={`insight-badge ${isReal ? 'insight-badge-real' : 'insight-badge-sim'}`}>
          {isReal ? 'Real' : 'Simulated'}
        </span>
        <span className="insight-muted">{report.data_label_note}</span>
      </div>

      {suggestions.length === 0 ? (
        <p className="insight-muted mt-2 mb-0">No suggestions served for this game yet. Press Suggest first.</p>
      ) : (
        <table className="insight-table mt-2">
          <thead>
            <tr>
              <th>Suggested on</th>
              <th>Expected draw</th>
              <th>Suggested</th>
              <th>Drawn</th>
              <th>Hits vs chance</th>
              <th>Tier</th>
              <th>p-value</th>
            </tr>
          </thead>
          <tbody>
            {suggestions.map((s) => {
              const key = rowKey(s);
              const isSelected = selectedKey === key;
              return (
                <tr
                  key={key}
                  data-testid="suggestion-row"
                  onClick={() => setSelectedKey(isSelected ? null : key)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault();
                      setSelectedKey(isSelected ? null : key);
                    }
                  }}
                  tabIndex={0}
                  aria-selected={isSelected}
                  title="Click for detail"
                  style={{ cursor: 'pointer', background: isSelected ? 'var(--ip-line, #eee)' : undefined }}
                >
                  <td>{s.suggestion_date || '—'}</td>
                  <td>
                    {s.expected_draw_date || s.target_draw_date || '—'}
                    {(s.expected_draw_session || s.target_draw_session) ? <div className="insight-muted">{s.expected_draw_session || s.target_draw_session}</div> : null}
                  </td>
                  <td><Balls numbers={s.numbers} hits={s.matched_numbers || []} /></td>
                  <td>
                    {s.status === 'graded'
                      ? <Balls numbers={s.actual_numbers} hits={s.matched_numbers || []} />
                      : <span className="insight-muted">{s.status === 'pending' ? 'Draw not in yet' : (s.reason || s.status)}</span>}
                  </td>
                  <td>
                    {s.status === 'graded' ? `${s.hits} vs ${fmtNum(s.expected_hits_by_chance)}` : `— vs ${fmtNum(s.expected_hits_by_chance)}`}
                  </td>
                  <td>{s.tier?.tier || '—'}{s.tier?.prize ? <div className="insight-muted">${Number(s.tier.prize).toLocaleString()}</div> : null}</td>
                  <td>{s.status === 'graded' ? fmtP(s.p_value_at_least_these_hits) : '—'}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}

      {selected && (
        <SuggestionDetail suggestion={selected} onClose={() => setSelectedKey(null)} />
      )}

      <div className="insight-grid">
        <Stat label={`Graded (last ${report.last})`} value={pooled.graded ?? 0} />
        <Stat label="Hits / expected by chance" value={pooled.graded ? `${pooled.total_hits} / ${fmtNum(pooled.expected_total_hits_by_chance)}` : '—'} />
        <Stat label="Exact pooled p-value" value={fmtP(pooled.p_value_exact)} />
        <Stat label="All-time graded" value={allTime.graded ?? 0} />
        <Stat label="All-time lift vs chance" value={allTime.graded ? `${fmtNum(allTime.lift_vs_chance)}x` : '—'} />
      </div>
      {pooled.verdict && <p className="mb-1">{pooled.verdict}</p>}

      <div className="insight-stat mt-2">
        <div className="d-flex align-items-center gap-2">
          <span className="insight-badge insight-badge-sim">Simulated</span>
          <span className="insight-stat-label">Backtest ({backtest.source || 'n/a'}) vs real</span>
        </div>
        {backtest.available ? (
          <div className="mt-1">
            Backtest mean hits {fmtNum(backtest.simulated_mean_hits, 3)}
            {' '}(random {fmtNum(backtest.simulated_random_baseline_mean_hits, 3)})
            {backtest.real_mean_hits !== undefined && (
              <> · real {fmtNum(backtest.real_mean_hits, 3)} · gap {fmtNum(backtest.gap_real_minus_simulated, 3)}</>
            )}
          </div>
        ) : (
          <div className="insight-muted mt-1">{backtest.note || 'No backtest yet.'}</div>
        )}
      </div>

      {champion && (
        <div className="insight-muted mt-2" data-testid="model-champion">
          Model in use: <strong>{champion.champion === 'chained_ensemble' ? 'Chained ensemble' : 'Incumbent ensemble'}</strong>
          {champion.last_evaluated_at ? ` (last checked ${champion.last_evaluated_at})` : ''}
          {champion.reason ? ` — ${champion.reason}` : ''}
        </div>
      )}

      <div className="insight-disclaimer">{report.disclaimer}</div>
    </div>
  );
}

export default function AccuracyReportPanel({ game, last = 3 }) {
  const [report, setReport] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const activeGameRef = useRef(game);

  useEffect(() => {
    activeGameRef.current = game;
    setReport(null);
    setError('');
    setLoading(false);
  }, [game]);

  const check = async () => {
    const requestedGame = game;
    setLoading(true);
    setError('');
    try {
      const r = await axios.get(`${getApiBase()}/api/accuracy_report`, { params: { game, last }, timeout: 120000 });
      if (activeGameRef.current !== requestedGame) return;
      const payload = r.data || {};
      if (payload.game && payload.game !== requestedGame) {
        setError(`Report was for ${payload.game}, not ${requestedGame}.`);
        return;
      }
      if (payload.status === 'error') {
        setError(payload.message || 'Accuracy report failed.');
        return;
      }
      setReport(payload);
    } catch (e) {
      if (activeGameRef.current !== requestedGame) return;
      setError(e.response?.data?.detail || e.message || 'Accuracy report failed.');
    } finally {
      if (activeGameRef.current === requestedGame) setLoading(false);
    }
  };

  return (
    <div className="insight-panel">
      <div className="d-flex align-items-center justify-content-between flex-wrap gap-2">
        <h6 className="mb-0">Real-world accuracy</h6>
        <button type="button" className="insight-btn" onClick={check} disabled={loading}>
          {loading ? 'Checking…' : `Check last ${last} suggestions`}
        </button>
      </div>
      <p className="insight-muted mb-0 mt-1">
        Grades the suggestions this app served for {game} against the real draws they targeted.
      </p>
      {loading && (
        <div className="mt-2">
          <CandyStripeProgress status="in_progress" value={null} detail="Grading served suggestions against real draws…" testId="accuracy-progress" />
        </div>
      )}
      {error && !loading && (
        <div className="mt-2">
          <CandyStripeProgress status="error" error={error} onRetry={check} showPercent={false} testId="accuracy-error" />
        </div>
      )}
      {report && !error && <AccuracyReportView report={report} />}
    </div>
  );
}
