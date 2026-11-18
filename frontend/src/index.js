import React from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import App from './App';
import { installAuthFetch } from './utils/authFetch';
import { installFlowActivity } from './utils/flowActivity';
import './index.css';
import 'bootstrap/dist/css/bootstrap.min.css';

installAuthFetch();
installFlowActivity();

const container = document.getElementById('root');
const root = createRoot(container);
root.render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>
);
