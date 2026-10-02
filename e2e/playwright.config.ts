import { defineConfig } from '@playwright/test'
import * as dotenv from 'dotenv'

dotenv.config()

const CI = !!process.env.CI

/**
 * Target is selected entirely by env (see .env.example):
 *   E2E_BASE_URL — frontend under test (local vite or the deployed dev site)
 *   E2E_API_URL  — FastAPI backend (local :8000 or the Azure dev container)
 * Specs that need privileged access (realtime seeding) skip themselves when
 * their env is absent, so a bare `npm run e2e` is always safe to run.
 *
 * In CI (.github/workflows/e2e.yml): a junit report the workflow counts, one
 * retry, and no trace or video. A trace records every value typed into a
 * field, the password included, and the artifacts of a public repository are
 * downloadable by anyone signed in to GitHub. Screenshots on failure stay, and
 * helpers/test.ts prints the failing page's URL, console errors and text into
 * the job log, which is where a failure gets read.
 */
export default defineConfig({
  testDir: './specs',
  timeout: 120_000,
  expect: { timeout: 15_000 },
  retries: CI ? 1 : 0,
  workers: 1, // specs share one seeded account; serial keeps state predictable
  reporter: CI
    ? [['list'], ['junit', { outputFile: 'results.xml' }], ['html', { open: 'never' }]]
    : [['list'], ['html', { open: 'never' }]],
  use: {
    baseURL: process.env.E2E_BASE_URL || 'http://localhost:5173',
    trace: CI ? 'off' : 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: CI ? 'off' : 'retain-on-failure',
  },
})
