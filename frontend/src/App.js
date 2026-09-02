import React, { useState } from 'react';
import Dashboard from './components/Dashboard';
import StartupProgress from './components/StartupProgress';
import Header from './components/Header';
import { useStartupStatusPoll } from './hooks/useStartupStatusPoll';
import './styles/modern.css';
import './App.css';
import './styles/magazine.css';

export default function App() {
  const [startupComplete, setStartupComplete] = useState(false);
  const { startupStatus, errorMessage: startupErrorMessage } = useStartupStatusPoll({
    enabled: startupComplete,
    intervalMs: 10000,
    stopWhenCompleted: true,
  });

  const handleStartupComplete = () => {
    setStartupComplete(true);
  };

  return (
    <div className="magazine-app">
      <img
        className="magazine-photo"
        src="/css/assets/nyl-bg/skyline.jpg"
        alt=""
        aria-hidden="true"
      />
      <div className="magazine-overlay" aria-hidden="true" />
      <div className="magazine-shell">
        {!startupComplete ? (
          <StartupProgress onComplete={handleStartupComplete} />
        ) : (
          <>
            <Header startupStatus={startupStatus} />
            <Dashboard startupStatus={startupStatus} startupErrorMessage={startupErrorMessage} />
          </>
        )}
      </div>
    </div>
  );
}
