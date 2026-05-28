/**
 * src/queryClient.ts
 * ==================
 * React Query client instance and a typed query-key factory.
 *
 * Separation of concerns
 * ----------------------
 * React Query owns server state (tables list, user profile).
 * Zustand owns UI + ephemeral game state.
 * The two never write to each other's data — but they do communicate
 * in one direction: a global 401 from React Query clears Zustand auth.
 *
 * Query key factory (queryKeys)
 * ------------------------------
 * Centralising keys here gives us:
 *   1. Type safety — no raw string[] scattered across components.
 *   2. Easy cache invalidation — queryClient.invalidateQueries(queryKeys.me)
 *      invalidates everywhere `queryKeys.me` is used.
 *   3. Single point to change a key name without a grep.
 *
 * Retry policy
 * ------------
 * Never retry on 401 (stale token) or 403 (locked table) — retrying those
 * immediately would just hammer the server and confuse the user.
 * All other failures get two retries with React Query's default backoff.
 */

import { QueryClient } from '@tanstack/react-query';
import type { ApiError } from './types';

// ─── Global 401 handler ────────────────────────────────────────────────────────

/**
 * Called lazily (inside the QueryClient factory) so the store import
 * is only resolved after both modules have initialised — avoids circular
 * dependency between queryClient.ts ↔ store.ts.
 */
function handleGlobalAuthError(error: unknown): void {
  const apiError = error as Partial<ApiError>;
  if (apiError?.statusCode === 401) {
    /**
     * Import the store dynamically to break the potential circular dep.
     * In practice this import resolves synchronously because the store
     * module will already be evaluated by the time any query errors.
     */
    import('./store').then(({ useAppStore }) => {
      useAppStore.getState().clearAuth();
    });
  }
}

// ─── QueryClient ───────────────────────────────────────────────────────────────

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      /**
       * 30 s stale window for the tables list and user profile.
       * These change rarely — no need to refetch on every focus.
       */
      staleTime: 30_000,

      /**
       * Don't retry on auth or permission errors; retry twice on
       * transient network failures.
       */
      retry: (failureCount: number, error: unknown) => {
        const apiError = error as Partial<ApiError>;
        if (
          apiError?.statusCode === 401 ||
          apiError?.statusCode === 403
        ) {
          return false;
        }
        return failureCount < 2;
      },

      /**
       * Re-fetch silently when the user tabs back into the app.
       * Keeps the balance display and table lock state fresh.
       */
      refetchOnWindowFocus: true,
    },

    mutations: {
      /**
       * Global mutation error handler.
       * Components can add their own onError in useMutation() — this
       * runs in addition to, not instead of, those handlers.
       */
      onError: handleGlobalAuthError,
    },
  },
});

// ─── Query key factory ─────────────────────────────────────────────────────────

/**
 * Typed, hierarchical cache keys.
 *
 * Usage examples:
 *   useQuery({ queryKey: queryKeys.me, queryFn: getMe })
 *   useQuery({ queryKey: queryKeys.tables, queryFn: getTables })
 *   queryClient.invalidateQueries({ queryKey: queryKeys.me })
 *
 * The `as const` assertions make the tuple types exact (e.g. ['me'] not
 * string[]) so TypeScript can catch typos in invalidateQueries calls.
 */
export const queryKeys = {
  /** Current user profile + balance — GET /api/v1/auth/me/ */
  me: ['me'] as const,

  /** All lobby tables — GET /api/v1/tables/ */
  tables: ['tables'] as const,

  /**
   * Single game detail — GET /api/v1/games/{gameId}/
   * Scoped by gameId so different games don't collide in cache.
   */
  game: (gameId: string) => ['game', gameId] as const,

  /**
   * Leaderboard REST snapshot — GET /api/v1/leaderboard/{tableId}/
   * Separate from the SSE stream — this is the one-shot initial load.
   */
  leaderboard: (tableId: number) => ['leaderboard', tableId] as const,
} as const;