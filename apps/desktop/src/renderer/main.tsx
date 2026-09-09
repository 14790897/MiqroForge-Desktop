import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import { ErrorBoundary } from './components/ErrorBoundary';
import './i18n'; // init i18next (sync, inline resources) before first render
import './styles/globals.css';
import { applyStoredUiPreferences } from './lib/uiPreferences';

// 浅色主题默认，如需深色可取消注释
// document.documentElement.classList.add('dark')

applyStoredUiPreferences();

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </React.StrictMode>
);
