// Compute a browser-resolvable API base at runtime.
export function getApiBase() {
  try {
    const raw = (process.env.REACT_APP_API_BASE || '').toString().trim();
    // Prefer explicit http(s) env var when provided
    if (raw && /^https?:\/\//i.test(raw)) return raw.replace(/\/+$/, '');
    // Allow relative API base (e.g., /api) for same-origin proxy
    if (raw && raw.startsWith('/')) return raw.replace(/\/+$/, '');
    // If env provided without scheme, assume http
    if (raw) return `http://${raw.replace(/\/+$/, '')}`;

    // Empty base means use relative paths with nginx proxy (production)
    return '';
  } catch (e) {
    return '';
  }
}

// Export the function as the default so callers can compute the base at runtime:
export default getApiBase;
