/**
 * src/store.ts
 * ============
 * Zustand global store — the single source of truth for all client-side
 * state that is NOT owned by React Query.
 *
 * What lives here vs React Query
 * --------------------------------
 *   React Query  →  server-derived lists (tables, user profile /me/)
 *   Zustand      →  auth token, current game state, UI flags
 *
 * The game state (currentGame) is owned here rather than by React Query
 * because:
 *   1. Every game action returns the full new state in one response.
 *      There is nothing to "re-fetch" — we just set the new value.
 *   2. Game state has tight coupling with animation flags (isAnimating*)
 *      that need to live next to it.
 *   3. The game state is ephemeral: intentionally lost on hard refresh
 *      (the player must start a new game).  React Query's caching would
 *      fight that intentional design.
 *
 * Persistence strategy
 * --------------------
 * The `persist` middleware saves ONLY the auth slice to localStorage.
 * Keeping the token in the store means components can reactively
 * respond to login/logout without polling localStorage directly.
 *
 * The `partialize` option excludes game and UI state from persistence —
 * they reset cleanly on every page load.
 *
 * Animation contract (used by Step 5 components)
 * -----------------------------------------------
 * When stand / double-down resolves, the backend returns the complete
 * final state in one response (all bot cards + all dealer cards revealed).
 * Rather than rendering everything instantly, components animate the
 * "reveal" sequence using the card arrays from the final state:
 *
 *   1. Component calls stand() → receives full resolved GameState.
 *   2. store.setGame(resolvedState) stores the final state immediately.
 *   3. store.setAnimatingResolution(true) — action buttons stay disabled.
 *   4. Component steps through bot_hands and dealer_hand.cards with
 *      setTimeout delays to produce a deal animation.
 *   5. After the last card is "dealt", store.setAnimatingResolution(false).
 *   6. ResultModal opens (watches isAnimatingResolution + game.status).
 */

import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';

import type { GameAction, GameState, ModalType, UserProfile } from './types';

// ─── Store shape ───────────────────────────────────────────────────────────────

export interface AppState {
  // ── Auth (persisted to localStorage) ──────────────────────────────────────
  /** Null when logged out. */
  user:            UserProfile | null;
  /** Raw DRF token string kept in sync with tokenStorage in api.ts. */
  token:           string | null;
  isAuthenticated: boolean;

  // ── Current game (ephemeral — not persisted) ───────────────────────────────
  /**
   * The latest GameState returned by any game-action API call.
   * Null when in the lobby or between games.
   */
  currentGame: GameState | null;

  /**
   * Which game-action API call is currently awaiting a response.
   * Use this to disable action buttons and show spinner overlays.
   *   null     → no call in flight
   *   'create' → POST /games/
   *   'hit'    → POST /games/{id}/hit/
   *   'stand'  → POST /games/{id}/stand/
   *   'double' → POST /games/{id}/double/
   */
  actionInFlight: GameAction;

  /**
   * True while the frontend is animating the resolution sequence after
   * stand / double-down.  The ResultModal should only open when this
   * transitions back to false.
   *
   * Lifecycle:
   *   stand/double API call resolves
   *     → setGame(resolvedState)         (store all final cards)
   *     → setAnimatingResolution(true)   (keep UI locked)
   *     → [component animates cards]
   *     → setAnimatingResolution(false)  (unlock UI, modal opens)
   */
  isAnimatingResolution: boolean;

  // ── UI (ephemeral) ─────────────────────────────────────────────────────────
  activeModal:  ModalType;
  /** Message rendered in a global toast / error banner. */
  globalError:  string | null;

  // ── Auth actions ───────────────────────────────────────────────────────────
  /**
   * Called immediately after register() or login() resolves.
   * Sets all three auth fields atomically so no component ever sees a
   * half-initialised auth state.
   */
  setAuth:       (user: UserProfile, token: string) => void;
  /**
   * Called on logout or a global 401.
   * Resets auth AND clears game/UI state — the user is back to a clean slate.
   */
  clearAuth:     () => void;
  /**
   * Sync balance from a completed game or a fresh /me/ response without
   * re-fetching the whole UserProfile.
   */
  updateBalance: (balance: string) => void;

  // ── Game actions ───────────────────────────────────────────────────────────
  /**
   * Store a new GameState and, if the game just completed, sync the
   * updated balance into user.balance so the header reflects it instantly
   * without waiting for a /me/ refetch.
   */
  setGame:                 (game: GameState) => void;
  /** Return to lobby — clear game and all transient UI state. */
  clearGame:               () => void;
  setActionInFlight:       (action: GameAction) => void;
  setAnimatingResolution:  (value: boolean) => void;

  // ── UI actions ─────────────────────────────────────────────────────────────
  openModal:      (modal: ModalType) => void;
  closeModal:     () => void;
  setGlobalError: (message: string | null) => void;
}

// ─── Initial (non-persisted) state ────────────────────────────────────────────

const INITIAL_GAME_STATE = {
  currentGame:           null,
  actionInFlight:        null  as GameAction,
  isAnimatingResolution: false,
} satisfies Partial<AppState>;

const INITIAL_UI_STATE = {
  activeModal:  null as ModalType,
  globalError:  null,
} satisfies Partial<AppState>;

// ─── Store factory ─────────────────────────────────────────────────────────────

export const useAppStore = create<AppState>()(
  persist(
    (set, get) => ({
      // ── Initial state ──────────────────────────────────────────────────────
      user:            null,
      token:           null,
      isAuthenticated: false,
      ...INITIAL_GAME_STATE,
      ...INITIAL_UI_STATE,

      // ── Auth actions ───────────────────────────────────────────────────────
      setAuth: (user, token) =>
        set({ user, token, isAuthenticated: true }),

      clearAuth: () =>
        set({
          user:            null,
          token:           null,
          isAuthenticated: false,
          ...INITIAL_GAME_STATE,
          ...INITIAL_UI_STATE,
        }),

      updateBalance: (balance) => {
        const { user } = get();
        if (user) {
          set({ user: { ...user, balance } });
        }
      },

      // ── Game actions ───────────────────────────────────────────────────────
      setGame: (game) => {
        const updates: Partial<AppState> = { currentGame: game };

        /**
         * If the game just completed, immediately sync the new balance
         * into the user profile so every component that reads
         * user.balance (e.g. the header chip count) updates without a
         * round-trip to /auth/me/.
         */
        if (game.status === 'COMPLETED' && game.new_balance) {
          const { user } = get();
          if (user) {
            updates.user = { ...user, balance: game.new_balance };
          }
        }

        set(updates);
      },

      clearGame: () =>
        set({ ...INITIAL_GAME_STATE, ...INITIAL_UI_STATE }),

      setActionInFlight: (action) =>
        set({ actionInFlight: action }),

      setAnimatingResolution: (value) =>
        set({ isAnimatingResolution: value }),

      // ── UI actions ─────────────────────────────────────────────────────────
      openModal:  (modal)   => set({ activeModal: modal }),
      closeModal: ()        => set({ activeModal: null }),
      setGlobalError: (msg) => set({ globalError: msg }),
    }),

    {
      /**
       * localStorage key for the persisted slice.
       * Change this to force all existing sessions to re-login
       * (e.g. after a breaking change in UserProfile shape).
       */
      name:    'bj-auth-v1',
      storage: createJSONStorage(() => localStorage),

      /**
       * ONLY persist the auth slice.
       * Game, animation, and UI state are intentionally ephemeral —
       * they reset cleanly on every hard refresh.
       */
      partialize: (state): Pick<AppState, 'user' | 'token' | 'isAuthenticated'> => ({
        user:            state.user,
        token:           state.token,
        isAuthenticated: state.isAuthenticated,
      }),

      /**
       * Called after the persisted data is rehydrated from localStorage.
       * We re-validate the stored token via /auth/me/ in App.tsx, but we
       * can at least set isAuthenticated = false here if there is no user,
       * to catch corrupted storage without waiting for the network.
       */
      onRehydrateStorage: () => (state) => {
        if (state && !state.user) {
          state.isAuthenticated = false;
          state.token = null;
        }
      },
    },
  ),
);

// ─── Convenience selector hooks ────────────────────────────────────────────────
// Exporting fine-grained selectors prevents components from subscribing to
// the whole store and re-rendering on unrelated state changes.

/** Current authenticated user (or null). */
export const useUser            = () => useAppStore((s) => s.user);
/** True when the user is logged in. */
export const useIsAuthenticated = () => useAppStore((s) => s.isAuthenticated);
/** The current in-progress or just-completed game. */
export const useCurrentGame     = () => useAppStore((s) => s.currentGame);
/** Which action API call is in flight right now. */
export const useActionInFlight  = () => useAppStore((s) => s.actionInFlight);
/** True while the resolution animation is playing. */
export const useIsAnimating     = () => useAppStore((s) => s.isAnimatingResolution);
/** Currently open modal, if any. */
export const useActiveModal     = () => useAppStore((s) => s.activeModal);
/** Global error message for the toast banner. */
export const useGlobalError     = () => useAppStore((s) => s.globalError);