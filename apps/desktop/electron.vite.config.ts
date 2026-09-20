import { execSync } from 'node:child_process';
import { resolve } from 'path';
import { defineConfig, externalizeDepsPlugin } from 'electron-vite';
import tailwindcss from '@tailwindcss/vite';
import pkg from './package.json';
import { formatDevVersion, type GitVersionInfo } from './src/shared/dev-version';

/**
 * Read the current commit short hash + dirty flag from the repository.
 *
 * Runs only when building the dev-server config (`npm run dev`). Returns null in
 * a non-git environment so the version degrades to a plain `-dev` suffix.
 */
function readGitInfo(): GitVersionInfo | null {
  const repoRoot = resolve(__dirname, '..', '..');
  try {
    const shortHash = execSync('git rev-parse --short HEAD', {
      cwd: repoRoot,
      encoding: 'utf-8',
      stdio: ['ignore', 'pipe', 'ignore'],
    }).trim();
    const dirty =
      execSync('git status --porcelain', {
        cwd: repoRoot,
        encoding: 'utf-8',
        stdio: ['ignore', 'pipe', 'ignore'],
      }).trim().length > 0;
    return { shortHash, dirty };
  } catch {
    return null;
  }
}

export default defineConfig(({ command }) => {
  // #1055: in dev mode embed the commit hash so it is clear which code a locally
  // running app came from. Production builds keep the plain semver.
  const appVersion = command === 'serve' ? formatDevVersion(pkg.version, readGitInfo()) : pkg.version;

  return {
    main: {
      plugins: [externalizeDepsPlugin()],
      build: {
        outDir: 'out/main',
        rollupOptions: {
          input: {
            'electron-trampoline': resolve(__dirname, 'src/main/electron-trampoline.js'),
            index: resolve(__dirname, 'src/main/index.ts'),
          },
        },
      },
    },
    preload: {
      // Do NOT use externalizeDepsPlugin() for preload — sandbox mode cannot
      // require() npm packages at runtime, so all dependencies must be bundled.
      plugins: [],
      build: {
        outDir: 'out/preload',
        rollupOptions: {
          input: {
            index: resolve(__dirname, 'src/preload/index.ts'),
          },
        },
      },
    },
    renderer: {
      root: resolve(__dirname, 'src/renderer'),
      server: {
        host: '127.0.0.1',
      },
      define: {
        __APP_VERSION__: JSON.stringify(appVersion),
      },
      build: {
        outDir: 'out/renderer',
        rollupOptions: {
          input: {
            index: resolve(__dirname, 'src/renderer/index.html'),
            splash: resolve(__dirname, 'src/renderer/splash.html'),
          },
        },
      },
      plugins: [tailwindcss()],
      resolve: {
        alias: {
          '@': resolve(__dirname, 'src/renderer'),
          '@shared': resolve(__dirname, 'src/shared'),
        },
      },
    },
  };
});
