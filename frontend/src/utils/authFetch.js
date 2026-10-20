// Optional API-key support for subscriber deployments.
//
// Does nothing unless the backend runs with AUTH_MODE=apikey: a key is only sent
// once one is stored, and the prompt only appears after the server answers 401
// with the X-Auth-Required header. Local/default installs never see a change.

const STORAGE_KEY = 'pocketpro_api_key';

export function getApiKey() {
  try {
    return window.localStorage.getItem(STORAGE_KEY) || '';
  } catch (e) {
    return '';
  }
}

export function setApiKey(value) {
  try {
    if (value) window.localStorage.setItem(STORAGE_KEY, value.trim());
    else window.localStorage.removeItem(STORAGE_KEY);
  } catch (e) {
    /* storage unavailable (private mode) — key lives for this request only */
  }
}

function isApiUrl(url) {
  try {
    const u = new URL(url, window.location.href);
    return u.pathname.startsWith('/api/') || u.pathname.startsWith('/collection/');
  } catch (e) {
    return false;
  }
}

let promptOpen = false;
function promptForKey(message) {
  if (promptOpen) return '';
  promptOpen = true;
  try {
    const entered = window.prompt(message || 'Enter your PocketPro access key');
    if (entered) setApiKey(entered);
    return entered ? entered.trim() : '';
  } finally {
    promptOpen = false;
  }
}

export function installAuthFetch() {
  if (typeof window === 'undefined' || window.__pocketproAuthInstalled) return;
  window.__pocketproAuthInstalled = true;

  const originalFetch = window.fetch.bind(window);

  const withKey = (input, init, key) => {
    if (!key) return [input, init];
    const headers = new Headers((init && init.headers) || (input instanceof Request ? input.headers : undefined));
    if (!headers.has('X-API-Key')) headers.set('X-API-Key', key);
    return [input, { ...(init || {}), headers }];
  };

  window.fetch = async (input, init) => {
    const url = typeof input === 'string' ? input : input && input.url;
    if (!url || !isApiUrl(url)) return originalFetch(input, init);

    let response = await originalFetch(...withKey(input, init, getApiKey()));
    if (response.status === 401 && response.headers.get('X-Auth-Required')) {
      const key = promptForKey(getApiKey() ? 'That access key was not accepted. Enter your PocketPro access key' : undefined);
      if (key) response = await originalFetch(...withKey(input, init, key));
    }
    return response;
  };

  // EventSource cannot send headers, so the key travels as ?api_key=.
  if (typeof window.EventSource === 'function') {
    const OriginalEventSource = window.EventSource;
    const Patched = function (url, config) {
      const key = getApiKey();
      let finalUrl = url;
      if (key && isApiUrl(url)) {
        const u = new URL(url, window.location.href);
        if (!u.searchParams.has('api_key')) u.searchParams.set('api_key', key);
        finalUrl = u.toString();
      }
      return new OriginalEventSource(finalUrl, config);
    };
    Patched.prototype = OriginalEventSource.prototype;
    Patched.CONNECTING = OriginalEventSource.CONNECTING;
    Patched.OPEN = OriginalEventSource.OPEN;
    Patched.CLOSED = OriginalEventSource.CLOSED;
    window.EventSource = Patched;
  }
}

export default installAuthFetch;
