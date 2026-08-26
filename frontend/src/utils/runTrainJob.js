import { isTrainSuccessStatus } from './trainingUtils';

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function isPending(status) {
  return ['started', 'already_running', 'running', 'queued'].includes(String(status || '').toLowerCase());
}

export async function runTrainingJob(axiosClient, apiBase, body, { timeoutMs = 3600000, pollMs = 3000 } = {}) {
  const start = await axiosClient.post(`${apiBase}/api/train`, body, { timeout: 20000 });
  let data = start.data || {};
  const startStatus = String(data.status || '').toLowerCase();

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
