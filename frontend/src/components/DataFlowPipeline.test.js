import React, { act } from 'react';
import { createRoot } from 'react-dom/client';

jest.mock('axios', () => {
  const get = jest.fn();
  const instance = {
    get,
    interceptors: { request: { use: jest.fn() }, response: { use: jest.fn() } },
  };
  return { __esModule: true, default: instance, ...instance };
});

// eslint-disable-next-line import/first
import axios from 'axios';
// eslint-disable-next-line import/first
import DataFlowPipeline, { deriveStages } from './DataFlowPipeline';
// eslint-disable-next-line import/first
import { classifyRequest, beginStages, getFlowActivity, __resetFlowActivity } from '../utils/flowActivity';

global.IS_REACT_ACT_ENVIRONMENT = true;

describe('flowActivity.classifyRequest', () => {
  it('maps API calls to pipeline stages', () => {
    expect(classifyRequest('/api/ingest', 'post')).toEqual(['ingest']);
    expect(classifyRequest('http://x/api/ingest_stream?game=take5', 'get')).toEqual(['ingest']);
    expect(classifyRequest('/api/train_all', 'post')).toEqual(['agents']);
    expect(classifyRequest('/api/predict', 'post')).toEqual(['agents', 'ensemble', 'prediction']);
    expect(classifyRequest('/api/predictions/all', 'get')).toBeNull();
    expect(classifyRequest('/api/train_status', 'get')).toBeNull();
    expect(classifyRequest('/api/ingest_progress?game=x', 'get')).toBeNull();
  });

  it('tracks in-flight and last outcome', () => {
    __resetFlowActivity();
    const done = beginStages(['ensemble', 'prediction']);
    expect(getFlowActivity().inflight.ensemble).toBe(1);
    done(true);
    done(true); // idempotent
    const a = getFlowActivity();
    expect(a.inflight.ensemble).toBe(0);
    expect(a.last.prediction.ok).toBe(true);
  });
});

describe('deriveStages', () => {
  it('shows ingest live with row progress', () => {
    const s = deriveStages({
      startup: { status: 'ingesting', current_game: 'take5', current_game_rows_fetched: 50, current_game_rows_total: 100, populated_games: 0, total: 9 },
      train: { jobs: {} },
      activity: null,
    });
    expect(s.ingest.status).toBe('active');
    expect(s.ingest.detail).toContain('take5');
    expect(s.agents.status).toBe('idle');
  });

  it('marks data loaded, training live, and prediction done', () => {
    const s = deriveStages({
      startup: { status: 'completed', populated_games: 9, total: 9 },
      train: { jobs: { pick3: { game: 'pick3', status: 'running' } } },
      activity: { inflight: {}, last: { prediction: { ok: true, at: Date.now() }, ensemble: { ok: true, at: Date.now() } } },
    });
    expect(s.ingest.status).toBe('done');
    expect(s.agents.status).toBe('active');
    expect(s.agents.detail).toContain('pick3');
    expect(s.prediction.status).toBe('done');
  });

  it('surfaces failures', () => {
    const s = deriveStages({
      startup: { status: 'error' },
      train: { jobs: { a: { game: 'a', status: 'error' } } },
      activity: { inflight: {}, last: { prediction: { ok: false, at: Date.now() } } },
    });
    expect(s.ingest.status).toBe('error');
    expect(s.agents.status).toBe('error');
    expect(s.prediction.status).toBe('error');
  });
});

describe('<DataFlowPipeline />', () => {
  it('renders all four stages from live status', async () => {
    axios.get.mockImplementation((url) => Promise.resolve({
      data: url.includes('startup_status')
        ? { status: 'completed', populated_games: 3, total: 9 }
        : { jobs: {} },
    }));
    const el = document.createElement('div');
    document.body.appendChild(el);
    const root = createRoot(el);
    await act(async () => { root.render(<DataFlowPipeline pollMs={60000} idlePollMs={60000} />); });
    await act(async () => { await Promise.resolve(); });
    ['ingest', 'agents', 'ensemble', 'prediction'].forEach((k) => {
      expect(el.querySelector(`[data-testid="flow-${k}"]`)).not.toBeNull();
    });
    expect(el.querySelector('[data-testid="flow-ingest"]').className).toContain('is-done');
    expect(el.textContent).toContain('3 of 9 games loaded');
    act(() => root.unmount());
  });
});
