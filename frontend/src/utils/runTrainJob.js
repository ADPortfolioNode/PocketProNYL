import { isTrainSuccessStatus } from './trainingUtils';

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function isPending(status) {
  return ['started', 'already_running', 'running', 'queued'].includes(String(status || '').toLowerCase());
}

export async function runTrainingJob(axiosClient, apiBase, body, { timeoutMs = 3600000, pollMs = 3000, onStatus } = {}) {
  const start = await axiosClient.post(`${apiBase}/api/train`, body, { timeout: 20000 });
  let data = start.data || {};
  const startStatus = String(data.status || '').toLowerCase();
  onStatus?.(data);

  if (isTrainSuccessStatus(data.status) && !isPending(data.status)) {
    return data;
  }
  if (startStatus === 'error' || startStatus === 'failed') {
    const err = new Error(data.message || 'Training failed.');
    err.response = { data, status: 500 };
    throw err;
  }

  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    await sleep(pollMs);
    try {
      const poll = await axiosClient.get(
        `${apiBase}/api/train_status?game=${encodeURIComponent(body.game)}`,
        { timeout: 8000 },
      );
      data = poll.data || {};
      onStatus?.(data);
      const st = String(data.status || '').toLowerCase();
      if (st === 'completed' || st === 'success') return data;
      if (st === 'error' || st === 'failed') {
        const err = new Error(data.message || 'Training failed.');
        err.response = { data, status: 500 };
        throw err;
      }
    } catch (error) {
      const nested = String(error?.response?.data?.status || '').toLowerCase();
      if (nested === 'error' || nested === 'failed') throw error;
    }
  }

  const err = new Error('Training is still running after 60 minutes. Check Completed Training Experiments.');
  err.code = 'TRAIN_POLL_TIMEOUT';
  throw err;
}

export async function runTrainAllJobs(axiosClient, apiBase, body, { timeoutMs = 3600000, pollMs = 4000, onStatus } = {}) {
  const start = await axiosClient.post(`${apiBase}/api/train_all`, body, { timeout: 20000 });
  const data = start.data || {};
  onStatus?.(data);
  const startStatus = String(data.status || '').toLowerCase();
  if (startStatus === 'error' || startStatus === 'failed') {
    const err = new Error(data.message || 'Train all failed.');
    err.response = { data, status: 500 };
    throw err;
  }

  const watched = Array.isArray(data.started) && data.started.length
    ? data.started
    : (Array.isArray(data.games) ? data.games : []);
  const deadline = Date.now() + timeoutMs;

  while (Date.now() < deadline) {
    await sleep(pollMs);
    const poll = await axiosClient.get(`${apiBase}/api/train_status`, { timeout: 8000 });
    const raw = poll.data?.jobs || poll.data || {};
    const list = Array.isArray(raw) ? raw : Object.values(raw);
    const relevant = watched.length
      ? list.filter((job) => watched.includes(job.game))
      : list;
    if (!relevant.length) continue;
    onStatus?.({ phase: relevant.some((job) => job.phase === 'optimize_weights') ? 'optimize_weights' : 'fit', jobs: relevant });
    const pending = relevant.some((job) => isPending(job.status));
    const failed = relevant.find((job) => ['error', 'failed'].includes(String(job.status || '').toLowerCase()));
    if (!pending) {
      if (failed) {
        const err = new Error(failed.message || `Training failed for ${failed.game}.`);
        err.response = { data: failed, status: 500 };
        throw err;
      }
      return {
        status: 'completed',
        message: `Training finished for ${relevant.length} game(s).`,
        results: relevant,
        trained: relevant.length,
      };
    }
  }

  const err = new Error('Train all is still running after 60 minutes. Check Completed Training Experiments.');
  err.code = 'TRAIN_POLL_TIMEOUT';
  throw err;
}
