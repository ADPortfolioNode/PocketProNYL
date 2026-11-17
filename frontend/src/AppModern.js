import React, { useState } from 'react';
import Dashboard from './components/DashboardModern';
import StartupProgress from './components/StartupProgress';
import Header from './components/Header';
import './styles/modern.css';
import './index.css';

export default function App() {
  const [startupComplete, setStartupComplete] = useState(false);

  const handleStartupComplete = () => {
    setStartupComplete(true);
  };

  if (!startupComplete) {
    return <StartupProgress onComplete={handleStartupComplete} />;
  }

  return (
    <div className="container">
      <Header />
      <Dashboard />
    </div>
  );
}
