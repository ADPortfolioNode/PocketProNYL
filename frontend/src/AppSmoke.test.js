import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { installAuthFetch } from './utils/authFetch';
import { installFlowActivity } from './utils/flowActivity';
import App from './App';

test('full app mounts with auth + flow instrumentation installed', async () => {
  global.fetch = jest.fn(() => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}), text: () => Promise.resolve('') }));
  window.fetch = global.fetch;
  installAuthFetch();
  installFlowActivity();
  const errors = [];
  const origErr = console.error;
  console.error = (...a) => { errors.push(a.join(' ')); };
  const div = document.createElement('div');
  document.body.appendChild(div);
  await act(async () => { createRoot(div).render(<MemoryRouter><App /></MemoryRouter>); });
  console.error = origErr;
  expect(div.innerHTML.length).toBeGreaterThan(100);
});
