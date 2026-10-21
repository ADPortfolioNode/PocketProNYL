import React, { useEffect, useRef, useState } from 'react';
import axios from 'axios';
import getApiBase from '../utils/apiBase';
import './InsightPanels.css';

const fmtP = (p) => (p === null || p === undefined ? '—' : (p < 0.0001 ? '<0.0001' : Number(p).toFixed(4)));
const fmtNum = (v, digits = 2) => (v === null || v === undefined || Number.isNaN(Number(v)) ? '—' : Number(v).toFixed(digits));

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

export function AccuracyReportView({ report }) {
  if (!report) return null;
  const pooled = report.pooled_last || {};
  const allTime = report.pooled_all_time || {};
  const backtest = report.backtest || {};
  const isReal = report.data_label === 'Real';
  const champion = report.model_champion;
  return (
    <div data-testid="accuracy-report">
      <div className="d-flex align-items-center gap-2 flex-wrap">
        <span className={`insight-badge ${isReal ? 'insight-badge-real' : 'insight-badge-sim'}`}>
          {isReal ? 'Real' : 'Simulated'}
        </span>
        <span className="insight-muted">{report.data_label_note}</span>
      </div>

      {(report.suggestions || []).length === 0 ? (
        <p className="insight-muted mt-2 mb-0">No suggestions served for this game yet. Press Suggest first.</p>
      ) : (
        <table className="insight-table mt-2">
          <thead>
            <tr>
              <th>Target draw</th>
              <th>Suggested</th>
              <th>Drawn</th>
              <th>Hits vs chance</th>
              <th>Tier</th>
              <th>p-value</th>
            </tr>
          </thead>
          <tbody>
            {report.suggestions.map((s) => (
              <tr key={s.id || `${s.served_at}-${(s.numbers || []).join('-')}`}>
                <td>
                  {s.target_draw_date || '—'}
                  {s.target_draw_session ? <div className="insight-muted">{s.target_draw_session}</div> : null}
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
            ))}
          </tbody>
        </table>
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
      {error && <div className="alert alert-danger mt-2 mb-0">{error}</div>}
      {report && !error && <AccuracyReportView report={report} />}
    </div>
  );
}
