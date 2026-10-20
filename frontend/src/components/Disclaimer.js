import React from 'react';

// Shared legal / responsible-play notice. Keep this text in sync with
// marketing/js/nav.js (site footer).
export const DISCLAIMER_TEXT =
  'For statistical analysis and entertainment only. Lottery draws are random; no tool can predict or improve your odds, and past results do not influence future draws. Not affiliated with, endorsed by, or sponsored by the New York Lottery or New York State Gaming Commission. Must be 18+ (21+ for Quick Draw where required). Gambling problem? Call 1-877-8-HOPENY or text HOPENY (467369).';

export default function Disclaimer() {
  return (
    <footer className="app-disclaimer" role="contentinfo">
      <p>{DISCLAIMER_TEXT}</p>
    </footer>
  );
}
