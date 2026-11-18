import { isTrainSuccessStatus } from './trainingUtils';

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function isPending(status) {
  return ['started', 'already_running', 'running', 'queued'].includes(String(status || '').toLowerCase());
}

// A start request that fails WITHOUT an explicit server answer — the
// axios 20s timeout, a network drop, or a proxy 502/503/504 — does not
// prove the server refused the job: the request may have landed and
// training may already be running. Only those failures are recoverable
// (see the start recovery in both runners below). An HTTP answer with
// any other status is a real refusal and fails immediately.
function isTransientStartError(error) {
  const status = error?.response?.status;
  if (status === 502 || status === 503 || status === 504) return true;
  if (status) return false;
  return error?.code === 'ECONNABORTED' || /timeout|network/i.test(String(error?.message || ''));
}

export async function runTrainingJob(axiosClient, apiBase, body, { timeoutMs = 3600000, pollMs = 3000, onStatus } = {}) {
  let start;
  try {
    start = await axiosClient.post(`${apiBase}/api/train`, body, { timeout: 20000 });
  } catch (error) {
    if (!isTransientStartError(error)) throw error;
    // Start recovery: the request may have reached the server before it
    // failed. If a job for this game is running (or already finished),
    // attach to it instead of reporting a failure.
    let statusData = null;
    try {
      const poll = await axiosClient.get(
        `${apiBase}/api/train_status?game=${encodeURIComponent(body.game)}`,
        { timeout: 8000 },
      );
      statusData = poll.data || null;
    } catch (_) { /* status check failed too; fall through to one retry */ }
    const st = String(statusData?.status || '').toLowerCase();
    if (statusData && (isPending(st) || st === 'completed' || st === 'success')) {
      start = { data: statusData };
    } else {
      // Nothing is running for this game, so the start truly did not
      // land. Retry it once; a second failure is a real failure.
      start = await axiosClient.post(`${apiBase}/api/train`, body, { timeout: 20000 });
    }
  }
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
  let start;
  try {
    start = await axiosClient.post(`${apiBase}/api/train_all`, body, { timeout: 20000 });
  } catch (error) {
    if (!isTransientStartError(error)) throw error;
    // Start recovery: the request may have reached the server before it
    // failed. If training jobs are already running, attach to them
    // instead of reporting a failure.
    let jobs = [];
    try {
      const poll = await axiosClient.get(`${apiBase}/api/train_status`, { timeout: 8000 });
      const raw = poll.data?.jobs || poll.data || {};
      jobs = Array.isArray(raw) ? raw : Object.values(raw);
    } catch (_) { /* status check failed too; fall through to one retry */ }
    const running = jobs.filter((job) => isPending(job.status));
    if (running.length) {
      start = { data: { status: 'already_running', started: running.map((job) => job.game) } };
    } else {
      // Nothing is running, so the start truly did not land. Retry it
      // once; a second failure is a real failure.
      start = await axiosClient.post(`${apiBase}/api/train_all`, body, { timeout: 20000 });
    }
  }
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
    let poll;
    try {
      poll = await axiosClient.get(`${apiBase}/api/train_status`, { timeout: 8000 });
    } catch (error) {
      // Transient poll failure (504 gateway timeout, network timeout, or
      // a busy backend) must NOT abort the Train All wait: the training
      // thread on the server keeps running, and the next poll will show
      // its real status. This mirrors runTrainingJob, which already
      // swallows poll errors. Only an explicit job error below, or the
      // 60-minute deadline, ends the wait with a failure.
      continue;
    }
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
