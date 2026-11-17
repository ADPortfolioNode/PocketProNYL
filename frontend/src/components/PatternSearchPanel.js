import React, { useCallback, useEffect, useRef, useState } from 'react';
import axios from 'axios';
import getApiBase from '../utils/apiBase';
import CandyStripeProgress from './CandyStripeProgress';
import './InsightPanels.css';

const fmt = (v, d = 2) => (v === null || v === undefined ? '—' : Number(v).toFixed(d));
const errorText = (e) => e?.response?.data?.detail || e?.response?.data?.message || e?.message || 'Request failed.';
const when = (iso) => {
  if (!iso) return 'never';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? String(iso) : d.toLocaleString();
};

const VERDICT = {
  passed_holdout: { text: 'passed holdout', cls: 'insight-badge insight-badge-real' },
  validation_only: { text: 'validation only', cls: 'insight-badge insight-badge-sim' },
  not_significant: { text: 'not significant', cls: 'insight-badge' },
  not_applicable: { text: 'n/a', cls: 'insight-badge' },
};

const TREND = {
  strengthening: { arrow: '▲', cls: 'pattern-trend-up', text: 'strengthening' },
  fading: { arrow: '▼', cls: 'pattern-trend-down', text: 'fading' },
  stable: { arrow: '→', cls: 'pattern-trend-flat', text: 'stable' },
  insufficient: { arrow: '·', cls: 'pattern-trend-flat', text: 'not enough draws' },
};

const EVENT = {
  promote: { icon: '▲', cls: 'pattern-trend-up', verb: 'promoted' },
  demote: { icon: '▼', cls: 'pattern-trend-down', verb: 'demoted' },
  rejected: { icon: '✕', cls: 'pattern-trend-flat', verb: 'not promoted' },
};

const TOP_ROWS = 12;

export default function PatternSearchPanel({ games = [], pollMs = 2500 }) {
  const [game, setGame] = useState(games.includes('take5') ? 'take5' : (games[0] || ''));
  const [state, setState] = useState({ loading: false, error: '', data: null });
  const [showAll, setShowAll] = useState(false);
  const timer = useRef(null);
  const alive = useRef(true);

  useEffect(() => () => { alive.current = false; clearTimeout(timer.current); }, []);

  const load = useCallback(async (refresh = false, polling = false) => {
    if (!game) return;
    clearTimeout(timer.current);
    if (!polling) setState((s) => ({ ...s, loading: true, error: '' }));
    try {
      const { data } = await axios.get(`${getApiBase()}/api/pattern_report`, {
        params: refresh ? { game, refresh: true } : { game }, timeout: 30000,
      });
      if (!alive.current) return;
      if (data?.status === 'error' && data?.scan_status !== 'running') {
        throw new Error(data.message || 'Pattern search failed.');
      }
      setState({ loading: false, error: '', data });
      if (data?.scan_status === 'running') {
        timer.current = setTimeout(() => load(false, true), pollMs);
      }
    } catch (e) {
      if (!alive.current) return;
      if (polling) {
        // A hiccup while a scan runs is not an error: keep the stripe and poll again.
        timer.current = setTimeout(() => load(false, true), pollMs * 2);
        return;
      }
      setState((s) => ({ ...s, loading: false, error: errorText(e) }));
    }
  }, [game, pollMs]);

  const report = state.data;
  const running = state.loading || report?.scan_status === 'running';
  const rows = (report?.signals || [])
    .filter((r) => r.status !== 'not_applicable')
    .slice()
    .sort((a, b) => (a.q_value ?? 1) - (b.q_value ?? 1));
  const shown = showAll ? rows : rows.slice(0, TOP_ROWS);
  const passed = rows.filter((r) => r.status === 'passed_holdout').length;
  const trends = rows.reduce((acc, r) => {
    const t = r.drift?.trend;
    if (t === 'strengthening' || t === 'fading') acc[t] += 1;
    return acc;
  }, { strengthening: 0, fading: 0 });
  const history = (report?.history || []).slice(-10).reverse();
  const promoted = report?.promoted_signals || [];

  return (
    <div className="insight-panel" data-testid="pattern-search-panel">
      <h6>Pattern search</h6>
      <p className="insight-muted mb-2">
        Rescans automatically after new draws. Every signal is tested at several lookback windows, each one counted
        in the BH-FDR family. Survivors face the champion/challenger holdout; only a significant holdout win promotes,
        and a promoted signal that fails on new draws is demoted.
      </p>
      <div className="d-flex gap-2 flex-wrap mb-2 align-items-center">
        <select className="form-select" style={{ maxWidth: 220 }} value={game} aria-label="Pattern search game"
          onChange={(e) => {
            clearTimeout(timer.current);
            setGame(e.target.value);
            setState({ loading: false, error: '', data: null });
          }}>
          {games.map((g) => <option key={g} value={g}>{g}</option>)}
        </select>
        <button type="button" className="insight-btn" onClick={() => load(false)} disabled={state.loading || !game}>
          Run pattern search
        </button>
        <button type="button" className="insight-btn pattern-btn-violet" onClick={() => load(true)}
          disabled={running || !game}>
          Rescan
        </button>
        {report && (
          <span className="insight-muted" data-testid="pattern-last-run">
            Last run: {when(report.last_run_at)}
            {report.draw_count ? ` · ${report.draw_count} draws` : ''}
            {report.scan_status === 'stale' ? ' · new draws since' : ''}
          </span>
        )}
      </div>
      {running && (
        <CandyStripeProgress status="in_progress" value={null}
          detail={report?.cached ? 'Rescanning signals in the background… showing the last result.' : 'Scanning signals walk-forward…'} />
      )}
      {state.error && !running && (
        <CandyStripeProgress status="error" error={state.error} onRetry={() => load(true)} showPercent={false} />
      )}
      {report && report.status === 'insufficient_data' && (
        <p className="insight-muted mb-0">{report.note || 'Not enough draws for an honest test.'}</p>
      )}
      {report && report.status === 'ok' && (
        <>
          <p className="insight-muted mb-2" data-testid="pattern-summary">
            {passed > 0
              ? `${passed} of ${rows.length} signals passed holdout`
              : `No signal passed holdout (${rows.length} tested)`}
            {` · validation ${report.split?.validation_draws} draws · holdout ${report.split?.holdout_draws} draws`}
            {report.fdr_q ? ` · q ≤ ${report.fdr_q}` : ''}
            {report.windows ? ` · windows ${report.windows.join('/')}` : ''}
            {` · ▲ ${trends.strengthening} strengthening · ▼ ${trends.fading} fading`}
          </p>
          {promoted.length > 0 && (
            <p className="mb-2" data-testid="pattern-promoted">
              Promoted: {promoted.map((s) => <span key={s} className="insight-badge insight-badge-real me-1">{s}</span>)}
            </p>
          )}
          <table className="insight-table">
            <thead>
              <tr><th>Signal</th><th>Window</th><th>Lift vs chance</th><th>p</th><th>q (FDR)</th><th>Trend</th><th>Holdout lift</th><th>Verdict</th></tr>
            </thead>
            <tbody>
              {shown.map((r) => {
                const v = VERDICT[r.status] || VERDICT.not_significant;
                const t = TREND[r.drift?.trend] || TREND.insufficient;
                const tip = r.drift?.rolling_lift?.length ? `rolling lift ${r.drift.rolling_lift.map((x) => fmt(x)).join(' → ')}` : t.text;
                return (
                  <tr key={r.signal} title={r.description}>
                    <td>{r.signal}</td>
                    <td>{r.window ?? '—'}</td>
                    <td>{fmt(r.validation?.lift)}×</td>
                    <td>{fmt(r.validation?.p_value, 3)}</td>
                    <td>{fmt(r.q_value, 3)}</td>
                    <td><span className={t.cls} title={tip} aria-label={t.text} data-testid="pattern-trend">{t.arrow}</span></td>
                    <td>{r.holdout ? `${fmt(r.holdout.lift)}×` : '—'}</td>
                    <td><span className={v.cls}>{v.text}</span></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {rows.length > TOP_ROWS && (
            <button type="button" className="insight-btn mt-2" onClick={() => setShowAll((x) => !x)}>
              {showAll ? 'Show top signals' : `Show all ${rows.length}`}
            </button>
          )}
        </>
      )}
      {report && (report.status === 'ok' || history.length > 0) && (
        <div className="mt-3" data-testid="pattern-history">
          <div className="insight-muted mb-1">Promote / demote history</div>
          {history.length === 0 ? (
            <p className="insight-muted mb-0">No promotions or demotions yet.</p>
          ) : (
            <ul className="pattern-history">
              {history.map((e, i) => {
                const meta = EVENT[e.type] || EVENT.rejected;
                const names = (e.signals || [e.signal]).filter(Boolean).join(', ');
                return (
                  <li key={`${e.at}-${i}`}>
                    <span className={meta.cls}>{meta.icon}</span> {meta.verb} {names}
                    <span className="insight-muted"> · {when(e.at)}{e.reason ? ` · ${e.reason}` : ''}</span>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
