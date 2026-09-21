import { resolve } from 'node:path';
import { defineConfig } from 'vitest/config';
import { readSiteUrlSources } from './scripts/mkdocs-site-urls.mjs';

// 与 electron.vite.config.ts 同源注入，单测里断言的就是真实构建值（#1155）。
const { docsBaseUrl, repoUrl } = readSiteUrlSources(resolve(__dirname, '..', '..', 'mkdocs.yml'));

export default defineConfig({
  define: {
    __DOCS_BASE_URL__: JSON.stringify(docsBaseUrl),
    __REPO_URL__: JSON.stringify(repoUrl),
  },
  test: {
    // Only run unit tests (not Playwright specs, not real E2E in CI)
    include: ['src/**/*.test.ts', 'tests/**/*.test.ts'],
    exclude: [
      '**/node_modules/**',
      'tests/smoke/**', // Playwright + real E2E tests
      '**/*.spec.ts', // Playwright specs
    ],
    // Allow passing even if no test files (CI may not have all branches' tests)
    passWithNoTests: true,
  },
});
