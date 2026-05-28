/**
 * vite.config.ts
 * ==============
 * Vite + React build config for the Blackjack frontend.
 *
 * Dev-proxy strategy
 * ------------------
 * React dev server runs on :5173, Django on :8000.
 * All /api/** and /sse/** requests are proxied through Vite so the
 * browser sees a single origin and CORS never becomes an issue in dev.
 *
 *   /api/** → http://localhost:8000/api/**   (REST)
 *   /sse/** → http://localhost:8000/sse/**   (SSE stream)
 *
 * The SSE proxy entry needs one extra step: tell http-proxy not to buffer
 * the response so Server-Sent Events flow through immediately rather than
 * being held until the connection closes.
 *
 * Production (Render.com)
 * -----------------------
 * When the React build is served from the same domain as Django (one
 * Render service), set VITE_API_BASE_URL="" in the build environment and
 * all axios / EventSource calls use relative paths — no CORS, no proxy.
 */

import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import type { ServerOptions } from 'vite';

const DJANGO_ORIGIN = 'http://localhost:8000';

const proxyConfig: ServerOptions['proxy'] = {
  // ── REST API ──────────────────────────────────────────────────────────────
  '/api': {
    target:       DJANGO_ORIGIN,
    changeOrigin: true,
    // No rewrite — /api/v1/... passes through unchanged.
  },

  // ── SSE leaderboard stream ────────────────────────────────────────────────
  '/sse': {
    target:       DJANGO_ORIGIN,
    changeOrigin: true,
    /**
     * Disable proxy-level response buffering.
     * Without this the SSE "data: {...}\n\n" chunks are held in memory and
     * the browser's EventSource.onmessage never fires during development.
     */
    configure: (proxy) => {
      proxy.on('proxyRes', (proxyRes) => {
        // Mirror the headers Django already sets, just to be safe.
        proxyRes.headers['x-accel-buffering'] = 'no';
        proxyRes.headers['cache-control']      = 'no-cache';
      });
    },
  },
};

export default defineConfig({
  plugins: [react()],

  // In production Django serves index.html and WhiteNoise serves /static/...
  // Setting base to '/static/' makes Vite embed the correct prefix in all
  // asset URLs inside index.html so they match WhiteNoise's STATIC_URL.
  // The Vite dev server is unaffected — it rewrites paths transparently.
  base: process.env.NODE_ENV === 'production' ? '/static/' : '/',

  server: {
    port:  5173,
    proxy: proxyConfig,
  },

  build: {
    /**
     * Output to blackjack/dist — a neutral staging directory.
     * Django's STATICFILES_DIRS points here so collectstatic picks it up
     * and copies into STATIC_ROOT (backend/staticfiles) for WhiteNoise.
     * Never put the Vite output directly inside STATIC_ROOT: collectstatic
     * --clear would wipe it before re-collecting.
     */
    outDir:    '../dist',
    emptyOutDir: true,
  },
});