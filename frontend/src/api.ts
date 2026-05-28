/**
 * src/api.ts
 * ==========
 * Axios instance + every function that talks to the Django REST API.
 *
 * Token lifecycle
 * ---------------
 * DRF Token auth requires the header:  Authorization: Token <key>
 * The raw token string is kept in localStorage under TOKEN_STORAGE_KEY.
 * The Zustand store mirrors it (for reactive is-logged-in checks), but
 * tokenStorage is the ground truth that the Axios interceptor reads on
 * every request — so even if the store hasn't hydrated yet the header
 * is still attached correctly on page load.
 *
 * Error normalisation
 * -------------------
 * Django returns errors in several incompatible shapes depending on which
 * layer generated them (GameService, DRF serialiser, DRF permissions…).
 * normalizeApiError() flattens all of them into our ApiError type so
 * components only ever deal with a single error shape.
 *
 * SSE URL
 * -------
 * EventSource cannot send custom headers, so the token is appended as
 * a query parameter.  buildSSEUrl() constructs that URL and is used
 * exclusively by the useLeaderboard hook.
 */

import axios, { AxiosError } from 'axios';
import type { AxiosInstance, InternalAxiosRequestConfig } from 'axios';

import type {
  ApiError,
  AuthResponse,
  CreateGamePayload,
  GameState,
  LeaderboardRestResponse,
  LoginPayload,
  RegisterPayload,
  Table,
  UserProfile,
} from './types';

// ─── Token storage ─────────────────────────────────────────────────────────────

const TOKEN_STORAGE_KEY = 'bj_auth_token';

export const tokenStorage = {
  get: (): string | null =>
    localStorage.getItem(TOKEN_STORAGE_KEY),

  set: (token: string): void =>
    localStorage.setItem(TOKEN_STORAGE_KEY, token),

  clear: (): void =>
    localStorage.removeItem(TOKEN_STORAGE_KEY),
};

// ─── Axios instance ────────────────────────────────────────────────────────────

/**
 * VITE_API_BASE_URL controls where requests go:
 *   ""                      → relative paths, handled by Vite dev proxy
 *   "http://localhost:8000" → direct to Django (no proxy)
 *   "https://…onrender.com" → production
 */
const BASE_URL: string = import.meta.env.VITE_API_BASE_URL ?? '';

export const apiClient: AxiosInstance = axios.create({
  baseURL: `${BASE_URL}/api/v1`,
  headers: { 'Content-Type': 'application/json' },
  /**
   * 15 s timeout on game actions — the server resolves synchronously,
   * so anything beyond this is a real network problem.
   */
  timeout: 15_000,
});

// ─── Request interceptor: attach Authorization header ─────────────────────────

apiClient.interceptors.request.use(
  (config: InternalAxiosRequestConfig) => {
    const token = tokenStorage.get();
    if (token) {
      config.headers.Authorization = `Token ${token}`;
    }
    return config;
  },
  (error: unknown) => Promise.reject(error),
);

// ─── Response interceptor: normalise errors + clear stale tokens ──────────────

apiClient.interceptors.response.use(
  (response) => response,
  (error: AxiosError) => {
    if (error.response?.status === 401) {
      /**
       * Token is missing, expired, or invalid on the server side.
       * Clear it from storage now; the Zustand store's auth state will
       * be cleared by the component that catches the resulting ApiError
       * (typically a React Query onError callback in queryClient.ts).
       */
      tokenStorage.clear();
    }
    // Always reject with our normalised shape — never raw AxiosError.
    return Promise.reject(normalizeApiError(error));
  },
);

// ─── Error normaliser ──────────────────────────────────────────────────────────

/**
 * Converts any thrown value into our flat ApiError shape.
 *
 * Django error shapes handled:
 *   { error: "msg" }                GameService / custom view errors
 *   { detail: "msg" }               DRF permission / authentication errors
 *   { field: ["msg1", "msg2"] }     DRF serialiser field validation
 *   { non_field_errors: ["msg"] }   DRF cross-field validation
 */
export function normalizeApiError(error: unknown): ApiError {
  // Non-Axios errors (e.g. programming mistakes, cancelled requests)
  if (!(error instanceof AxiosError)) {
    return {
      message:    error instanceof Error ? error.message : 'An unexpected error occurred.',
      statusCode: 0,
    };
  }

  const statusCode = error.response?.status ?? 0;
  const data = error.response?.data as Record<string, unknown> | undefined;

  // Network failure — no response at all
  if (!data) {
    return {
      message:    'Network error. Please check your connection and try again.',
      statusCode,
    };
  }

  const fieldErrors: Record<string, string[]> = {};
  let primaryMessage = '';

  for (const [key, value] of Object.entries(data)) {
    if (key === 'error' || key === 'detail') {
      // Single-message error from GameService or DRF
      primaryMessage = String(value);
    } else if (key === 'non_field_errors' && Array.isArray(value)) {
      primaryMessage = (value as string[]).join(' ');
    } else if (Array.isArray(value)) {
      // Per-field errors from DRF serialiser — e.g. { username: ["taken"] }
      fieldErrors[key] = value.map(String);
      if (!primaryMessage) {
        primaryMessage = `${key}: ${(value as string[]).join(', ')}`;
      }
    }
  }

  return {
    message:     primaryMessage || 'An unexpected error occurred.',
    statusCode,
    fieldErrors: Object.keys(fieldErrors).length > 0 ? fieldErrors : undefined,
    raw:         data,
  };
}

// ══════════════════════════════════════════════════════════════════════════════
// AUTH
// ══════════════════════════════════════════════════════════════════════════════

/**
 * Register a new account.
 * Persists the returned token to localStorage automatically.
 */
export async function register(payload: RegisterPayload): Promise<AuthResponse> {
  const { data } = await apiClient.post<AuthResponse>('/auth/register/', payload);
  tokenStorage.set(data.token);
  return data;
}

/**
 * Log in with username + password.
 * Persists the returned token to localStorage automatically.
 */
export async function login(payload: LoginPayload): Promise<AuthResponse> {
  const { data } = await apiClient.post<AuthResponse>('/auth/login/', payload);
  tokenStorage.set(data.token);
  return data;
}

/**
 * Invalidate the token server-side and clear it locally.
 * The `finally` block guarantees the local token is always cleared even
 * if the server returns an error (e.g. token was already invalidated).
 */
export async function logout(): Promise<void> {
  try {
    await apiClient.post('/auth/logout/');
  } finally {
    tokenStorage.clear();
  }
}

/**
 * Fetch the current user's profile + live balance.
 * Called on app mount (via React Query) to re-validate a persisted token
 * and to sync the balance after navigating back to the lobby.
 */
export async function getMe(): Promise<UserProfile> {
  const { data } = await apiClient.get<UserProfile>('/auth/me/');
  return data;
}

// ══════════════════════════════════════════════════════════════════════════════
// LOBBY
// ══════════════════════════════════════════════════════════════════════════════

/**
 * Fetch all tables with their is_locked status computed for the current user.
 * Results are cached by React Query (staleTime = 30 s in queryClient.ts).
 */
export async function getTables(): Promise<Table[]> {
  const { data } = await apiClient.get<Table[]>('/tables/');
  return data;
}

// ══════════════════════════════════════════════════════════════════════════════
// GAME ACTIONS
// ══════════════════════════════════════════════════════════════════════════════

/**
 * Start a new game at `table_id` with the given bet.
 * Returns the initial GameState: two cards dealt to every participant,
 * dealer hole card hidden (hole_card_hidden = true).
 */
export async function createGame(payload: CreateGamePayload): Promise<GameState> {
  const { data } = await apiClient.post<GameState>('/games/', payload);
  return data;
}

/**
 * Fetch a game by ID.  Used to restore an in-progress game on page refresh
 * (the game_id is persisted in the Zustand store via localStorage).
 */
export async function getGame(gameId: string): Promise<GameState> {
  const { data } = await apiClient.get<GameState>(`/games/${gameId}/`);
  return data;
}

/**
 * Player hits: draw one card.
 * If the player busts, the response has status = 'COMPLETED'.
 */
export async function hit(gameId: string): Promise<GameState> {
  const { data } = await apiClient.post<GameState>(`/games/${gameId}/hit/`);
  return data;
}

/**
 * Player stands.
 * Triggers full synchronous resolution on the server:
 *   bots play → dealer plays → outcomes compared → balances updated.
 * The response is always status = 'COMPLETED' with all cards revealed.
 */
export async function stand(gameId: string): Promise<GameState> {
  const { data } = await apiClient.post<GameState>(`/games/${gameId}/stand/`);
  return data;
}

/**
 * Player doubles down: bet doubled, exactly one more card dealt, then stand.
 * Only valid on the initial 2-card hand.
 * Response is always status = 'COMPLETED'.
 */
export async function doubleDown(gameId: string): Promise<GameState> {
  const { data } = await apiClient.post<GameState>(`/games/${gameId}/double/`);
  return data;
}

/**
 * Player leaves a mid-hand game.
 * The bet was already escrowed, so the balance is unchanged — the game is
 * simply closed with outcome = LOSE.
 * Response is the completed game state (status = 'COMPLETED', outcome = 'LOSE').
 */
export async function leaveGame(gameId: string): Promise<GameState> {
  const { data } = await apiClient.post<GameState>(`/games/${gameId}/leave/`);
  return data;
}

// ══════════════════════════════════════════════════════════════════════════════
// LEADERBOARD  (REST — initial load before SSE stream connects)
// ══════════════════════════════════════════════════════════════════════════════

/**
 * Fetch the current Top-5 players for a table.
 * Called once when the game screen mounts; subsequent updates come from SSE.
 */
export async function getLeaderboard(tableId: number): Promise<LeaderboardRestResponse> {
  const { data } = await apiClient.get<LeaderboardRestResponse>(
    `/leaderboard/${tableId}/`,
  );
  return data;
}

// ══════════════════════════════════════════════════════════════════════════════
// SSE  (used by useLeaderboard hook)
// ══════════════════════════════════════════════════════════════════════════════

/**
 * Build the EventSource URL for the leaderboard SSE stream.
 *
 * The browser's EventSource API does not support custom request headers,
 * so the DRF auth token is passed as a query parameter instead:
 *   /sse/leaderboard/{tableId}/?token=<key>
 *
 * This is an accepted SSE pattern; the connection is always over HTTPS
 * in production, so the token-in-URL approach is safe in practice.
 *
 * Returns null if no token is present (user not logged in), which the
 * useLeaderboard hook uses to skip connecting.
 */
export function buildSSEUrl(tableId: number): string | null {
  const token = tokenStorage.get();
  if (!token) return null;
  return `${BASE_URL}/sse/leaderboard/${tableId}/?token=${token}`;
}