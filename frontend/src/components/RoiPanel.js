import React, { useEffect, useRef, useState } from 'react';
import axios from 'axios';
import getApiBase from '../utils/apiBase';
import CandyStripeProgress from './CandyStripeProgress';
import './InsightPanels.css';

export const ODDS_STATEMENT = 'Lottery draws are random. No pick, model, filter or wheel can change the odds of winning. '
  + 'These tools only show the odds, the cost, and the risk of sharing a prize.';

const parseNumbers = (text) => String(text || '').split(/[^0-9]+/).filter(Boolean).map((n) => parseInt(n, 10));
const money = (v) => (v === null || v === undefined ? '—' : `$${Number(v).toLocaleString(undefined, { maximumFractionDigits: 2 })}`);
const errorText = (e) => e?.response?.data?.detail || e?.response?.data?.message || e?.message || 'Request failed.';

function useRequest() {
  const [state, setState] = useState({ loading: false, error: '', data: null });
  const run = async (fn) => {
    setState((s) => ({ ...s, loading: true, error: '' }));
    try {
      const data = await fn();
      setState({ loading: false, error: '', data });
      return data;
    } catch (e) {
      setState((s) => ({ ...s, loading: false, error: errorText(e) }));
      return null;
    }
  };
  return [state, run];
}

function Busy({ state, label, onRetry }) {
  if (state.loading) return <CandyStripeProgress status="in_progress" value={null} detail={label} />;
  if (state.error) return <CandyStripeProgress status="error" error={state.error} onRetry={onRetry} showPercent={false} />;
  return null;
}

function EvRanking() {
  const [state, run] = useRequest();
  const [jackpots, setJackpots] = useState('');
  const load = () => run(async () => (await axios.get(`${getApiBase()}/api/roi/ev_ranking`, {
    params: jackpots.trim() ? { jackpots: jackpots.trim() } : {}, timeout: 30000,
  })).data);
  return (
    <div className="mb-3" data-testid="ev-ranking">
      <h6>Game EV ranking</h6>
      <p className="insight-muted mb-2">Expected return per $1 from published odds and prizes (before taxes, ignoring jackpot splits).</p>
      <div className="d-flex gap-2 flex-wrap mb-2">
        <input className="form-control" style={{ maxWidth: 360 }} placeholder="Optional jackpot cash: powerball:267000000"
          value={jackpots} onChange={(e) => setJackpots(e.target.value)} aria-label="Jackpot cash values" />
        <button type="button" className="insight-btn" onClick={load} disabled={state.loading}>Rank games</button>
      </div>
      <Busy state={state} label="Computing expected value per dollar…" onRetry={load} />
      {state.data && !state.loading && (
        <table className="insight-table">
          <thead><tr><th>#</th><th>Game</th><th>EV per $1</th><th>Expected loss per $1</th><th>Notes</th></tr></thead>
          <tbody>
            {state.data.ranking.map((r) => (
              <tr key={r.game}>
                <td>{r.rank}</td>
                <td>{r.game}</td>
                <td>{money(r.ev_per_dollar)}</td>
                <td>{money(r.expected_loss_per_dollar)}</td>
                <td className="insight-muted">
                  {r.note}
                  {r.break_even_jackpot_cash ? ` Break-even jackpot (cash): ${money(r.break_even_jackpot_cash)}.` : ''}
                </td>
              </tr>
            ))}
            {state.data.not_ranked.map((r) => (
              <tr key={r.game}><td>—</td><td>{r.game}</td><td colSpan={3} className="insight-muted">{r.note}</td></tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function SplitRisk({ games }) {
  const [game, setGame] = useState(games.includes('take5') ? 'take5' : (games[0] || ''));
  const [numbers, setNumbers] = useState('');
  const [state, run] = useRequest();
  const check = () => run(async () => (await axios.post(`${getApiBase()}/api/roi/anti_popular`, {
    game, numbers: parseNumbers(numbers),
  }, { timeout: 30000 })).data);
  return (
    <div className="mb-3" data-testid="split-risk">
      <h6>Prize-sharing risk</h6>
      <p className="insight-muted mb-2">For shared (pari-mutuel) prizes, avoiding popular picks such as birthdays and patterns reduces how many winners you might split with. It does not change your odds of winning.</p>
      <div className="d-flex gap-2 flex-wrap mb-2">
        <select className="form-select" style={{ maxWidth: 180 }} value={game} onChange={(e) => setGame(e.target.value)} aria-label="Game">
          {games.map((g) => <option key={g} value={g}>{g}</option>)}
        </select>
        <input className="form-control" style={{ maxWidth: 260 }} placeholder="e.g. 1 2 3 4 5" value={numbers}
          onChange={(e) => setNumbers(e.target.value)} aria-label="Numbers to check" />
        <button type="button" className="insight-btn" onClick={check} disabled={state.loading || !numbers.trim()}>Check</button>
      </div>
      <Busy state={state} label="Scoring how often these numbers are played…" onRetry={check} />
      {state.data && !state.loading && (
        <div>
          <div>Popularity {state.data.original_popularity.score}/100{state.data.original_popularity.flags.length ? ` (${state.data.original_popularity.flags.join(', ')})` : ''}</div>
          {state.data.applied && <div>Less shared alternative: <strong>{state.data.numbers.join(' ')}</strong> (popularity {state.data.popularity.score}/100)</div>}
          <div className="insight-muted">{state.data.note}</div>
        </div>
      )}
    </div>
  );
}

function WheelBuilder({ games }) {
  const uniqueGames = games.filter((g) => !['pick3', 'numbers', 'win4'].includes(g));
  const [game, setGame] = useState(uniqueGames.includes('take5') ? 'take5' : (uniqueGames[0] || ''));
  const [pool, setPool] = useState('');
  const [guarantee, setGuarantee] = useState(3);
  const [ifMatch, setIfMatch] = useState(4);
  const [state, run] = useRequest();
  const build = () => run(async () => (await axios.post(`${getApiBase()}/api/roi/wheel`, {
    game, pool: parseNumbers(pool), guarantee: Number(guarantee), if_match: Number(ifMatch),
  }, { timeout: 60000 })).data);
  return (
    <div className="mb-3" data-testid="wheel-builder">
      <h6>Wheel builder</h6>
      <p className="insight-muted mb-2">Spreads up to 15 numbers over several tickets with a checked guarantee. More tickets cost more; each ticket's odds stay the same.</p>
      <div className="d-flex gap-2 flex-wrap mb-2 align-items-center">
        <select className="form-select" style={{ maxWidth: 160 }} value={game} onChange={(e) => setGame(e.target.value)} aria-label="Wheel game">
          {uniqueGames.map((g) => <option key={g} value={g}>{g}</option>)}
        </select>
        <input className="form-control" style={{ maxWidth: 260 }} placeholder="Pool, e.g. 3 8 12 19 24 27 33 36" value={pool}
          onChange={(e) => setPool(e.target.value)} aria-label="Wheel pool" />
        <span className="insight-muted">at least</span>
        <input type="number" min="1" className="form-control" style={{ maxWidth: 70 }} value={guarantee} onChange={(e) => setGuarantee(e.target.value)} aria-label="Guaranteed matches" />
        <span className="insight-muted">if</span>
        <input type="number" min="1" className="form-control" style={{ maxWidth: 70 }} value={ifMatch} onChange={(e) => setIfMatch(e.target.value)} aria-label="Drawn numbers in pool" />
        <button type="button" className="insight-btn" onClick={build} disabled={state.loading || !pool.trim()}>Build</button>
      </div>
      <Busy state={state} label="Searching for the smallest covering wheel…" onRetry={build} />
      {state.data && !state.loading && (
        <div>
          <div>{state.data.ticket_count} tickets ({money(state.data.cost)}) instead of {state.data.full_wheel_ticket_count} for a full wheel. {state.data.guarantee} {state.data.guarantee_verified ? '(checked)' : '(not met)'}</div>
          <div className="insight-muted">{state.data.condition_note}</div>
          <div className="mt-1">{state.data.tickets.slice(0, 30).map((t) => <div key={t.join('-')}>{t.join(' ')}</div>)}</div>
        </div>
      )}
    </div>
  );
}

function Bankroll({ games }) {
  const [game, setGame] = useState(games[0] || '');
  const [stake, setStake] = useState('');
  const [winnings, setWinnings] = useState('');
  const [state, run] = useRequest();
  const load = () => run(async () => (await axios.get(`${getApiBase()}/api/roi/bankroll`, { timeout: 30000 })).data);
  const add = () => run(async () => {
    await axios.post(`${getApiBase()}/api/roi/bankroll`, { game, stake: Number(stake || 0), winnings: Number(winnings || 0) }, { timeout: 30000 });
    setStake('');
    setWinnings('');
    return (await axios.get(`${getApiBase()}/api/roi/bankroll`, { timeout: 30000 })).data;
  });
  return (
    <div className="mb-3" data-testid="bankroll">
      <h6>Bankroll</h6>
      <div className="d-flex gap-2 flex-wrap mb-2">
        <select className="form-select" style={{ maxWidth: 160 }} value={game} onChange={(e) => setGame(e.target.value)} aria-label="Bankroll game">
          {games.map((g) => <option key={g} value={g}>{g}</option>)}
        </select>
        <input type="number" min="0" step="0.01" className="form-control" style={{ maxWidth: 120 }} placeholder="Spent $" value={stake} onChange={(e) => setStake(e.target.value)} aria-label="Spent" />
        <input type="number" min="0" step="0.01" className="form-control" style={{ maxWidth: 120 }} placeholder="Won $" value={winnings} onChange={(e) => setWinnings(e.target.value)} aria-label="Won" />
        <button type="button" className="insight-btn" onClick={add} disabled={state.loading || !stake}>Add</button>
        <button type="button" className="insight-btn" onClick={load} disabled={state.loading}>Show totals</button>
      </div>
      <Busy state={state} label="Loading bankroll…" onRetry={load} />
      {state.data && !state.loading && (
        <div>
          <div>Spent {money(state.data.total_staked)} · Won {money(state.data.total_won)} · Net {money(state.data.net)}</div>
          {state.data.games.map((g) => (
            <div key={g.game} className="insight-muted">
              {g.game}: net {money(g.net)}{g.roi_percent !== null ? ` (${g.roi_percent}%)` : ''}{g.expected_net !== undefined ? ` · expected ${money(g.expected_net)}` : ''}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function ModelCheck({ games }) {
  const [game, setGame] = useState(games[0] || '');
  const [job, setJob] = useState(null);
  const [error, setError] = useState('');
  const timer = useRef(null);
  useEffect(() => () => clearTimeout(timer.current), []);
  const poll = async (g) => {
    try {
      const r = await axios.get(`${getApiBase()}/api/chain/status`, { params: { game: g }, timeout: 15000 });
      setJob(r.data);
      if (r.data?.job?.status === 'running') timer.current = setTimeout(() => poll(g), 3000);
    } catch (e) {
      setError(errorText(e));
    }
  };
  const start = async () => {
    setError('');
    try {
      await axios.post(`${getApiBase()}/api/chain/evaluate`, { game }, { timeout: 30000 });
      setJob({ job: { status: 'running' } });
      poll(game);
    } catch (e) {
      setError(errorText(e));
    }
  };
  const status = job?.job?.status;
  const result = job?.job?.result;
  return (
    <div className="mb-3" data-testid="model-check">
      <h6>Chained ensemble check</h6>
      <p className="insight-muted mb-2">Tests the chained ensemble against the current model on recent draws it never trained on. It replaces the current model only if it wins on both validation and the untouched holdout.</p>
      <div className="d-flex gap-2 flex-wrap mb-2">
        <select className="form-select" style={{ maxWidth: 160 }} value={game} onChange={(e) => setGame(e.target.value)} aria-label="Model check game">
          {games.map((g) => <option key={g} value={g}>{g}</option>)}
        </select>
        <button type="button" className="insight-btn" onClick={start} disabled={status === 'running'}>Run check</button>
      </div>
      {status === 'running' && <CandyStripeProgress status="in_progress" value={null} detail="Walk-forward evaluation running (about two minutes per game)…" />}
      {(error || status === 'error') && (
        <CandyStripeProgress status="error" error={error || job?.job?.message} onRetry={start} showPercent={false} />
      )}
      {result && status === 'completed' && (
        <div>
          <div><strong>{result.promoted ? 'Promoted' : 'Not promoted'}</strong>: {result.decision_reason}</div>
          {result.holdout && (
            <div className="insight-muted">
              Holdout mean hits: chained {result.holdout.chained?.mean_hits} vs current {result.holdout.incumbent?.mean_hits} (random {result.holdout.chained?.random_baseline_mean_hits}), p = {result.holdout.paired_permutation_p_value}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function RoiPanel({ games = [] }) {
  return (
    <div className="insight-panel" data-testid="roi-panel">
      <div className="insight-disclaimer mt-0 mb-3" role="note"><strong>{ODDS_STATEMENT}</strong></div>
      {games.length === 0 ? (
        <p className="insight-muted mb-0">Load game history first.</p>
      ) : (
        <>
          <EvRanking />
          <SplitRisk games={games} />
          <WheelBuilder games={games} />
          <Bankroll games={games} />
          <ModelCheck games={games} />
        </>
      )}
    </div>
  );
}
