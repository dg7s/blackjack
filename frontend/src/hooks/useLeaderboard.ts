/**
 * src/hooks/useLeaderboard.ts
 * ===========================
 * React hook that manages a Server-Sent Events connection to
 * /sse/leaderboard/{tableId}/?token=<key>
 *
 * Responsibilities
 * ----------------
 *   • Open / close the EventSource when tableId changes or the
 *     component unmounts.
 *   • Parse incoming SSE data events and update leaderboard state.
 *   • Reconnect automatically with exponential backoff on error.
 *   • Expose the connection status so the UI can show "● Live" or
 *     "⚠ Reconnecting…" badges without any extra logic in components.
 *
 * Why not use the browser's built-in auto-reconnect?
 * ---------------------------------------------------
 * The browser does reconnect EventSource automatically, but:
 *   1. We cannot distinguish a temporary network blip from a permanent
 *      401 (the EventSource API hides HTTP status codes in onerror).
 *   2. We want to surface 'reconnecting' in the UI with a countdown.
 *   3. We want to give up after MAX_RECONNECT_ATTEMPTS instead of
 *      retrying forever.
 * So we disable the native reconnect (by closing the EventSource in
 * onerror) and implement our own controlled backoff.
 *
 * SSE event format (from views.py::_leaderboard_event_stream)
 * -----------------------------------------------------------
 *   data: {"type":"leaderboard","table_id":1,"data":[...]}
 *
 * Heartbeat comments (": heartbeat") are ignored by the browser
 * automatically — no special handling needed here.
 *
 * Usage
 * -----
 *   const { leaderboard, connectionStatus, error, reconnect } =
 *     useLeaderboard(currentGame?.table.id ?? null);
 *
 * Pass null to disconnect (e.g. when the user leaves the game screen).
 */

import {
  useCallback,
  useEffect,
  useRef,
  useState,
} from 'react';

import { buildSSEUrl } from '../api';
import type {
  LeaderboardEntry,
  SSEConnectionStatus,
  SSEPayload,
} from '../types';

// ─── Reconnect schedule ────────────────────────────────────────────────────────

/**
 * Delay (ms) before each reconnect attempt.
 * Index 0 = 1st retry, index 1 = 2nd retry, etc.
 * After the last entry the hook gives up and sets status = 'error'.
 */
const RECONNECT_DELAYS_MS = [
  1_000,   // 1 s
  2_000,   // 2 s
  5_000,   // 5 s
  15_000,  // 15 s
  30_000,  // 30 s — then give up
] as const;

const MAX_RECONNECT_ATTEMPTS = RECONNECT_DELAYS_MS.length;

// ─── Hook return type ──────────────────────────────────────────────────────────

export interface UseLeaderboardReturn {
  /** Current Top-N leaderboard.  Empty array before the first push. */
  leaderboard:       LeaderboardEntry[];
  /** Lifecycle state of the SSE connection. */
  connectionStatus:  SSEConnectionStatus;
  /**
   * Human-readable error message when connectionStatus = 'error'.
   * Null otherwise.
   */
  error:             string | null;
  /**
   * Manually trigger a fresh connection attempt, resetting the backoff
   * counter.  Useful for a "Retry" button in the UI.
   */
  reconnect:         () => void;
  /**
   * Increments each time the SSE stream delivers a reshuffle event.
   * Consumers can watch this in a useEffect to trigger animations.
   */
  reshuffleSignal:   number;
}

// ─── Hook ──────────────────────────────────────────────────────────────────────

export function useLeaderboard(tableId: number | null): UseLeaderboardReturn {
  const [leaderboard,      setLeaderboard]      = useState<LeaderboardEntry[]>([]);
  const [connectionStatus, setConnectionStatus] = useState<SSEConnectionStatus>('idle');
  const [error,            setError]            = useState<string | null>(null);
  const [reshuffleSignal,  setReshuffleSignal]  = useState<number>(0);

  // Refs live outside React's render cycle so the callbacks they're used in
  // never become stale without needing them in dependency arrays.
  const eventSourceRef        = useRef<EventSource | null>(null);
  const reconnectTimerRef     = useRef<ReturnType<typeof setTimeout> | null>(null);
  const reconnectAttemptsRef  = useRef<number>(0);
  /**
   * Set to true before we intentionally close the EventSource (unmount or
   * tableId change).  This prevents onerror from kicking off a reconnect
   * after we deliberately tore down the connection.
   */
  const isIntentionalCloseRef = useRef<boolean>(false);

  // ── Teardown ───────────────────────────────────────────────────────────────

  const teardown = useCallback((): void => {
    // Cancel any pending reconnect timer.
    if (reconnectTimerRef.current !== null) {
      clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
    // Close the EventSource if one is open.
    if (eventSourceRef.current !== null) {
      isIntentionalCloseRef.current = true;
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }
  }, []);

  // ── Connect ────────────────────────────────────────────────────────────────

  const connect = useCallback((): void => {
    if (tableId === null) return;

    const url = buildSSEUrl(tableId);
    if (!url) {
      setConnectionStatus('error');
      setError('No authentication token found. Please log in again.');
      return;
    }

    // Tear down any existing connection before opening a new one.
    teardown();
    isIntentionalCloseRef.current = false;

    setConnectionStatus(
      reconnectAttemptsRef.current > 0 ? 'reconnecting' : 'connecting',
    );

    const es = new EventSource(url);
    eventSourceRef.current = es;

    // ── onopen ───────────────────────────────────────────────────────────────
    es.onopen = (): void => {
      setConnectionStatus('connected');
      setError(null);
      reconnectAttemptsRef.current = 0; // reset backoff on a clean connect
    };

    // ── onmessage ────────────────────────────────────────────────────────────
    es.onmessage = (event: MessageEvent<string>): void => {
      try {
        const payload = JSON.parse(event.data) as SSEPayload;
        if (payload.type === 'leaderboard' && Array.isArray(payload.data)) {
          setLeaderboard(payload.data);
        } else if (payload.type === 'reshuffle') {
          setReshuffleSignal(n => n + 1);
        }
      } catch {
        // Malformed JSON from the server — log and continue.
        console.warn('[useLeaderboard] Failed to parse SSE event:', event.data);
      }
    };

    // ── onerror ──────────────────────────────────────────────────────────────
    es.onerror = (): void => {
      // Guard: don't reconnect after an intentional teardown.
      if (isIntentionalCloseRef.current) return;

      /**
       * We cannot inspect the HTTP status code here — the EventSource API
       * hides it.  We close and reconnect; if it was a permanent 401 the
       * reconnect will fail again and we'll eventually give up after
       * MAX_RECONNECT_ATTEMPTS.
       */
      es.close();
      eventSourceRef.current = null;

      const attempt = reconnectAttemptsRef.current;

      if (attempt >= MAX_RECONNECT_ATTEMPTS) {
        setConnectionStatus('error');
        setError(
          'Lost connection to the live leaderboard. ' +
          'Click Retry or refresh the page.',
        );
        return;
      }

      const delayMs = RECONNECT_DELAYS_MS[attempt];
      reconnectAttemptsRef.current += 1;
      setConnectionStatus('reconnecting');

      reconnectTimerRef.current = setTimeout(() => {
        connect();
      }, delayMs);
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tableId, teardown]);
  // `connect` intentionally does not depend on itself (recursive stable ref).

  // ── Effect: connect on mount / tableId change; disconnect on unmount ───────

  useEffect(() => {
    if (tableId === null) {
      teardown();
      setConnectionStatus('idle');
      setLeaderboard([]);
      setError(null);
      return;
    }

    reconnectAttemptsRef.current = 0;
    connect();

    return (): void => {
      teardown();
      setConnectionStatus('closed');
    };
  }, [tableId, connect, teardown]);

  // ── Manual reconnect (exposed for UI "Retry" button) ──────────────────────

  const reconnect = useCallback((): void => {
    reconnectAttemptsRef.current = 0;
    setError(null);
    connect();
  }, [connect]);

  return { leaderboard, connectionStatus, error, reconnect, reshuffleSignal };
}