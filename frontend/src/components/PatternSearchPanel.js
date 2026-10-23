import React, { useState } from 'react';
import axios from 'axios';
import getApiBase from '../utils/apiBase';
import CandyStripeProgress from './CandyStripeProgress';
import './InsightPanels.css';

const fmt = (v, d = 2) => (v === null || v === undefined ? '—' : Number(v).toFixed(d));
const errorText = (e) => e?.response?.data?.detail || e?.response?.data?.message || e?.message || 'Request failed.';

const VERDICT = {
  passed_holdout: { text: 'passed holdout', cls: 'insight-badge insight-badge-real' },
  validation_only: { text: 'validation only', cls: 'insight-badge insight-badge-sim' },
  not_significant: { text: 'not significant', cls: 'insight-badge' },
  not_applicable: { text: 'n/a', cls: 'insight-badge' },
};

export default function PatternSearchPanel({ games = [] }) {
  const [game, setGame] = useState(games.includes('take5') ? 'take5' : (games[0] || ''));
  const [state, setState] = useState({ loading: false, error: '', data: null });

  const load = async (refresh = false) => {
    if (!game) return;
    setState((s) => ({ ...s, loading: true, error: '' }));
    try {
      const { data } = await axios.get(`${getApiBase()}/api/pattern_report`, {
        params: refresh ? { game, refresh: true } : { game }, timeout: 120000,
      });
      if (data?.status === 'error') throw new Error(data.message || 'Pattern search failed.');
      setState({ loading: false, error: '', data });
    } catch (e) {
      setState((s) => ({ ...s, loading: false, error: errorText(e) }));
    }
  };

  const report = state.data;
  const rows = (report?.signals || []).filter((r) => r.status !== 'not_applicable');
  const passed = rows.filter((r) => r.status === 'passed_holdout').length;

  return (
    <div className="insight-panel" data-testid="pattern-search-panel">
      <h6>Pattern search</h6>
      <p className="insight-muted mb-2">
        Each signal predicts every draw from earlier draws only, scored against chance with a permutation test and
        BH-FDR. Only signals that also win on the untouched holdout count as real.
      </p>
      <div className="d-flex gap-2 flex-wrap mb-2">
        <select className="form-select" style={{ maxWidth: 220 }} value={game} aria-label="Pattern search game"
          onChange={(e) => { setGame(e.target.value); setState({ loading: false, error: '', data: null }); }}>
          {games.map((g) => <option key={g} value={g}>{g}</option>)}
        </select>
        <button type="button" className="insight-btn" onClick={() => load(false)} disabled={state.loading || !game}>
          Run pattern search
        </button>
      </div>
      {state.loading && <CandyStripeProgress status="in_progress" value={null} detail="Scoring signals walk-forward…" />}
      {state.error && !state.loading && (
        <CandyStripeProgress status="error" error={state.error} onRetry={() => load(true)} showPercent={false} />
      )}
      {report && !state.loading && report.status === 'insufficient_data' && (
        <p className="insight-muted mb-0">{report.note || 'Not enough draws for an honest test.'}</p>
      )}
      {report && !state.loading && report.status === 'ok' && (
        <>
          <p className="insight-muted mb-2" data-testid="pattern-summary">
            {passed > 0
              ? `${passed} of ${rows.length} signals passed holdout`
              : `No signal passed holdout (${rows.length} tested)`}
            {` · validation ${report.split?.validation_draws} draws · holdout ${report.split?.holdout_draws} draws`}
          </p>
          <table className="insight-table">
            <thead>
              <tr><th>Signal</th><th>Lift vs chance</th><th>p</th><th>q (FDR)</th><th>Holdout lift</th><th>Verdict</th></tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const v = VERDICT[r.status] || VERDICT.not_significant;
                return (
                  <tr key={r.signal} title={r.description}>
                    <td>{r.signal}</td>
                    <td>{fmt(r.validation?.lift)}×</td>
                    <td>{fmt(r.validation?.p_value, 3)}</td>
                    <td>{fmt(r.q_value, 3)}</td>
                    <td>{r.holdout ? `${fmt(r.holdout.lift)}×` : '—'}</td>
                    <td><span className={v.cls}>{v.text}</span></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}
