import { runTrainingJob, runTrainAllJobs } from './runTrainJob';

const API = 'http://backend';
const OPTS = { pollMs: 5, timeoutMs: 5000 };

function timeoutError() {
  const err = new Error('timeout of 20000ms exceeded');
  err.code = 'ECONNABORTED';
  return err;
}

function httpError(status, data = {}) {
  const err = new Error(`Request failed with status code ${status}`);
  err.response = { status, data };
  return err;
}

// Train All start recovery: the original bug was that a failed START
// request aborted Train All with the "gateway timeout" alert even
// though the server had accepted the job and training was running.

test('train all: start POST times out but jobs are running -> attaches and completes', async () => {
  const client = {
    post: jest.fn().mockRejectedValueOnce(timeoutError()),
    get: jest.fn()
      .mockResolvedValueOnce({ data: { jobs: [{ game: 'take5', status: 'running' }] } })
      .mockResolvedValueOnce({ data: { jobs: [{ game: 'take5', status: 'completed' }] } }),
  };
  const result = await runTrainAllJobs(client, API, {}, OPTS);
  expect(result.status).toBe('completed');
  expect(result.trained).toBe(1);
  expect(client.post).toHaveBeenCalledTimes(1);
});

test('train all: start POST 504 and nothing running -> retries start once and completes', async () => {
  const client = {
    post: jest.fn()
      .mockRejectedValueOnce(httpError(504))
      .mockResolvedValueOnce({ data: { status: 'started', started: ['take5'] } }),
    get: jest.fn()
      .mockResolvedValueOnce({ data: { jobs: [] } })
      .mockResolvedValueOnce({ data: { jobs: [{ game: 'take5', status: 'completed' }] } }),
  };
  const result = await runTrainAllJobs(client, API, {}, OPTS);
  expect(result.status).toBe('completed');
  expect(client.post).toHaveBeenCalledTimes(2);
});

test('train all: start times out, nothing running, retry also times out -> honest failure', async () => {
  const client = {
    post: jest.fn().mockRejectedValue(timeoutError()),
    get: jest.fn().mockResolvedValue({ data: { jobs: [] } }),
  };
  await expect(runTrainAllJobs(client, API, {}, OPTS)).rejects.toThrow(/timeout/i);
  expect(client.post).toHaveBeenCalledTimes(2);
});

test('train all: start refused with HTTP 400 -> fails immediately, no status check', async () => {
  const client = {
    post: jest.fn().mockRejectedValue(httpError(400)),
    get: jest.fn(),
  };
  await expect(runTrainAllJobs(client, API, {}, OPTS)).rejects.toThrow(/400/);
  expect(client.get).not.toHaveBeenCalled();
});

test('train all: explicit error status from start -> still fails fast', async () => {
  const client = {
    post: jest.fn().mockResolvedValue({ data: { status: 'error', message: 'boom' } }),
    get: jest.fn(),
  };
  await expect(runTrainAllJobs(client, API, {}, OPTS)).rejects.toThrow('boom');
  expect(client.get).not.toHaveBeenCalled();
});

test('single game: start POST times out but job is running -> attaches and completes', async () => {
  const client = {
    post: jest.fn().mockRejectedValueOnce(timeoutError()),
    get: jest.fn()
      .mockResolvedValueOnce({ data: { status: 'running' } })
      .mockResolvedValueOnce({ data: { status: 'completed' } }),
  };
  const result = await runTrainingJob(client, API, { game: 'take5' }, OPTS);
  expect(result.status).toBe('completed');
  expect(client.post).toHaveBeenCalledTimes(1);
});

test('single game: start POST times out and job already completed -> returns it', async () => {
  const client = {
    post: jest.fn().mockRejectedValueOnce(timeoutError()),
    get: jest.fn().mockResolvedValue({ data: { status: 'completed' } }),
  };
  const result = await runTrainingJob(client, API, { game: 'take5' }, OPTS);
  expect(result.status).toBe('completed');
  expect(client.get).toHaveBeenCalledTimes(1);
});
