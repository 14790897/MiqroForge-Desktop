/// <reference types="vite/client" />

import type { MiQiAPI } from '../preload/index';

declare global {
  const __APP_VERSION__: string;
  const __DOCS_BASE_URL__: string;
  const __REPO_URL__: string;

  interface Window {
    miqi: MiQiAPI;
  }
}
