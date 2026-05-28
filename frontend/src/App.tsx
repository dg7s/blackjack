/**
 * src/App.tsx
 * ===========
 * Root layout component. Acts as a three-screen router:
 *
 *   1. <Auth />      — shown when the user is not authenticated.
 *   2. <Lobby />     — shown when authenticated but no table selected.
 *   3. <GameTable /> + <Leaderboard />  — shown when a table is active.
 *
 * Token revalidation
 * ------------------
 * Zustand persists {user, token, isAuthenticated} to localStorage so the
 * user survives a page refresh without re-logging in. But the token could
 * have been invalidated server-side (e.g. admin logout, password change).
 * On every mount we fire GET /auth/me/ (React Query). If it returns 401,
 * the response interceptor in api.ts clears the token from localStorage,
 * and the useEffect here calls clearAuth() which resets the Zustand store.
 *
 * Table ↔ Game navigation
 * -----------------------
 * selectedTableId  — local state, set when user clicks a table in Lobby.
 * currentGame      — Zustand store, set after createGame() resolves.
 *
 * The leaderboard tableId uses currentGame?.table.id first (accurate after
 * a game starts) then falls back to selectedTableId (so the leaderboard
 * is visible even before the first bet is placed).
 *
 * handleLeaveTable clears both, returning the user to the Lobby.
 */

import { useEffect, useState } from 'react';
import { useQuery }            from '@tanstack/react-query';

import { getMe }         from './api';
import { queryKeys }     from './queryClient';
import {
  useAppStore,
  useCurrentGame,
  useIsAuthenticated,
}                        from './store';

import Auth        from './components/Auth';
import GameTable   from './components/GameTable';
import Leaderboard from './components/Leaderboard';
import Lobby       from './components/Lobby';
import type { Table } from './types';

import styles from './App.module.css';

export default function App() {
  const isAuthenticated = useIsAuthenticated();
  const currentGame     = useCurrentGame();
  const store           = useAppStore();

  /**
   * Which table the player selected from the lobby.
   * Persisted only in component state — intentionally reset on hard refresh
   * so the player always lands in the lobby after reloading.
   */
  const [selectedTable, setSelectedTable] = useState<Table | null>(null);

  // ── Token revalidation ────────────────────────────────────────────────────
  const {
    data:    meData,
    isError: meIsError,
  } = useQuery({
    queryKey: queryKeys.me,
    queryFn:  getMe,
    enabled:  isAuthenticated,   // only runs when we think we're logged in
    retry:    false,             // a 401 should not be retried
    staleTime: 5 * 60 * 1000,   // revalidate at most every 5 min
  });

  // If /me/ comes back 401, force logout
  useEffect(() => {
    if (meIsError) store.clearAuth();
  }, [meIsError]); // eslint-disable-line react-hooks/exhaustive-deps

  // Keep balance in the store fresh from the server
  useEffect(() => {
    if (meData?.balance) store.updateBalance(meData.balance);
  }, [meData?.balance]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Navigation helpers ────────────────────────────────────────────────────
  const handleSelectTable = (table: Table) => {
    setSelectedTable(table);
  };

  /** Called by GameTable's "← Lobby" button. */
  const handleLeaveTable = () => {
    store.clearGame();
    setSelectedTable(null);
  };

  // ── Derived values ────────────────────────────────────────────────────────
  /** The table ID to stream leaderboard events for. */
  const activeTableId: number | null =
    currentGame?.table.id ?? selectedTable?.id ?? null;

  /** True when the player has selected a table or has an active game. */
  const isAtTable =
    selectedTable !== null || currentGame !== null;

  /** The tableId to pass into GameTable for createGame calls. */
  const gameTableId: number =
    currentGame?.table.id ?? selectedTable?.id ?? 0;

  // ── Render ────────────────────────────────────────────────────────────────

  // Not logged in → Auth screen
  if (!isAuthenticated) {
    return <Auth />;
  }

  // Logged in, no table selected → Lobby
  if (!isAtTable) {
    return <Lobby onSelectTable={handleSelectTable} />;
  }

  // At a table → Game screen with side leaderboard
  return (
    <div className={styles.layout}>
      {/* ── Main game area ── */}
      <div className={styles.main}>
        <GameTable
          tableId={gameTableId}
          table={selectedTable ?? undefined}
          onLeaveTable={handleLeaveTable}
        />
      </div>

      {/* ── Leaderboard sidebar ── */}
      {activeTableId !== null && (
        <div className={styles.sidebar}>
          <Leaderboard tableId={activeTableId} />
        </div>
      )}
    </div>
  );
}
