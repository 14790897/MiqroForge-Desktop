/// <reference types="vite/client" />

import type { MiQiAPI } from '../preload/index';

declare global {
  const __APP_VERSION__: string;

  interface Window {
    miqi: MiQiAPI;
  }
}
