import React from 'react';
import '../styles/header.css';

export default function Header() {
  return (
    <header className="app-header">
      <div className="header-content">
        <div className="logo-section">
          <h1 className="app-title">
            <span className="title-accent">⚡</span> Mensa Lottery
          </h1>
          <p className="app-subtitle">AI-Powered Lottery Data Analysis & Suggestions</p>
        </div>
        <div className="header-status">
          <div className="status-indicator">
            <span className="status-dot"></span>
            <span className="status-text">Ready</span>
          </div>
        </div>
      </div>
    </header>
  );
}
