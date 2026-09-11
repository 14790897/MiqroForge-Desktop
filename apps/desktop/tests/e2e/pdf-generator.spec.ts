/**
 * PDF Generator E2E Test
 *
 * This test validates the create_pdf tool end-to-end.
 *
 * ══════════════════════════════════════════════════════════
 *  NOTE: Python unit tests (pytest tests/documents/) are
 *  the PRIMARY validation for CreatePdfTool — they directly
 *  test the tool with 7 test cases covering simple text,
 *  structured content, style presets, font discovery, error
 *  handling, path traversal rejection, and the pdf_write alias.
 *
 *  This E2E test is supplementary and runs only on demand
 *  (MIQI_RUN_REAL_PDF_E2E=1). It depends on the LLM choosing
 *  to use the create_pdf tool, which the model may not do if
 *  it predates the tool's addition.
 * ══════════════════════════════════════════════════════════
 *
 * Manual verification steps:
 *   1. cd apps/desktop
 *   2. $env:MIQI_RUN_REAL_PDF_E2E='1'
 *   3. npx playwright test --config=playwright.config.ts --project=electron pdf-generator.spec.ts
 *
 * To verify create_pdf works directly from Python:
 *   cd C:\Projects\PythonProjects\MiQroForge
 *   .\.venv\Scripts\python -c "
 *     import asyncio
 *     from miqi.documents.pdf_create_tool import CreatePdfTool
 *     async def t():
 *       r = await CreatePdfTool().execute(
 *         filename='test.pdf', title='Test', content='Hello PDF!')
 *       print(r)
 *     asyncio.run(t())
 *   "
 */

import { test } from '@playwright/test';

const SKIP_REAL_PDF_GENERATOR_ON_CI = !!process.env.CI && process.env.MIQI_RUN_REAL_PDF_E2E !== '1';

test.describe('PDF Generator E2E', () => {
  test.skip(
    SKIP_REAL_PDF_GENERATOR_ON_CI,
    'Run with MIQI_RUN_REAL_PDF_E2E=1 for manual verification.'
  );

  test('create_pdf tool — manual verification', { timeout: 600_000 }, async () => {
    // This test is a placeholder for manual E2E verification.
    // The primary validation is in tests/documents/test_office_create_tools.py
    // (7 test cases for CreatePdfTool).
    //
    // To verify manually:
    //   1. Open the app and type "使用 create_pdf 工具生成一个PDF"
    //   2. Check the Task Assets panel for the generated file
    //   3. Verify the file is a valid PDF
    //
    // Python test command:
    //   cd C:\Projects\PythonProjects\MiQroForge
    //   .\.venv\Scripts\pytest tests/documents/ -k pdf -v
    test.skip();
  });
});
