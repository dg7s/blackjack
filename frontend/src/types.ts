/**
 * src/types.ts
 * ============
 * Single source of truth for every TypeScript interface used across
 * the frontend.  Each type maps directly to a Django API contract:
 *   - Response shapes from GameService._build_response()  (services.py)
 *   - Serializer output shapes                            (serializers.py)
 *   - SSE event payloads                                  (views.py)
 *
 * Decimal-as-string convention
 * ----------------------------
 * Django serialises all DecimalField values as strings
 * (DRF setting: COERCE_DECIMAL_TO_STRING = True).  Every monetary
 * value in this file is therefore typed `string`.  Render them
 * directly; never use them in arithmetic without a safe parser.
 */

// ─── Card primitives ───────────────────────────────────────────────────────────

/**
 * A card is a 2-character string: {rank}{suit}
 * Ranks : A 2 3 4 5 6 7 8 9 T J Q K
 * Suits : H D C S  (Hearts Diamonds Clubs Spades)
 * Examples: "AH" = Ace of Hearts, "TC" = Ten of Clubs
 *
 * Card strings are generated ONLY by the Django backend.
 * The frontend never constructs them; it only receives and displays them.
 */
export type CardString = string;

export type Rank = 'A'|'2'|'3'|'4'|'5'|'6'|'7'|'8'|'9'|'T'|'J'|'Q'|'K';
export type Suit = 'H' | 'D' | 'C' | 'S';

/** Decompose a CardString into its rank and suit. */
export function parseCard(card: CardString): { rank: Rank; suit: Suit } {
  return { rank: card[0] as Rank, suit: card[1] as Suit };
}

export const RANK_DISPLAY: Record<Rank, string> = {
  A:'A', '2':'2', '3':'3', '4':'4', '5':'5',
  '6':'6', '7':'7', '8':'8', '9':'9',
  T:'10', J:'J', Q:'Q', K:'K',
};

export const SUIT_SYMBOL: Record<Suit, string> = {
  H:'♥', D:'♦', C:'♣', S:'♠',
};

/** True for Hearts and Diamonds — used for card colour in the UI. */
export function isRedSuit(suit: Suit): boolean {
  return suit === 'H' || suit === 'D';
}

// ─── Status enums (string unions matching Django TextChoices) ──────────────────

export type HandStatus  = 'ACTIVE' | 'STAND' | 'BUST' | 'BLACKJACK';
export type GameStatus  = 'BETTING' | 'IN_PROGRESS' | 'COMPLETED';

/**
 * Outcome of a completed game.
 * 'BLACKJACK' is a special WIN variant that pays 3:2 instead of 1:1.
 * null while the game is still in progress.
 */
export type GameOutcome = 'WIN' | 'LOSE' | 'PUSH' | 'BLACKJACK' | null;

// ─── Hand types ────────────────────────────────────────────────────────────────

export interface PlayerHand {
  cards:   CardString[];
  /** Best non-busting value (Aces already resolved to 1 or 11). */
  value:   number;
  /** True when an Ace is still counted as 11 — shown as "Soft X" in the UI. */
  is_soft: boolean;
  status:  HandStatus;
}

/**
 * Dealer's hand.
 *
 * When hole_card_hidden = true (game IN_PROGRESS), `cards` contains only
 * the face-up card at index 0.  The backend never sends the hole card
 * until the round resolves — this is cheat-proof by design.
 */
export interface DealerHand {
  cards:            CardString[];
  value:            number;
  status:           HandStatus;
  hole_card_hidden: boolean;
}

/** One AI bot's hand — fully revealed only after resolution. */
export interface BotHand {
  bot_index: number;
  cards:     CardString[];
  value:     number;
  status:    HandStatus;
}

// ─── Table info (embedded in GameState) ───────────────────────────────────────

/**
 * Compact table snapshot returned inside every GameState.
 * Lets the game screen show bet limits without a separate /tables/ call.
 */
export interface GameTableInfo {
  id:        number;
  level:     number;
  bot_count: number;
  min_bet:   string;  // Decimal string
  max_bet:   string;  // Decimal string
}

// ─── Side bets ─────────────────────────────────────────────────────────────────

export interface SideBetResult {
  bet:        string;        // Decimal string
  outcome:    string | null; // 'perfect'/'colored'/'mixed' | 'flush'/'straight'/... | null
  net_payout: string;        // Decimal string (positive = profit, negative = loss)
}

export interface SideBets {
  perfect_pairs?:    SideBetResult;
  twenty_one_three?: SideBetResult;
}

// ─── Game state ────────────────────────────────────────────────────────────────

/**
 * Canonical game snapshot returned by ALL game-action endpoints:
 *   POST /api/v1/games/                  (create)
 *   GET  /api/v1/games/{id}/             (detail / reconnect)
 *   POST /api/v1/games/{id}/hit/
 *   POST /api/v1/games/{id}/stand/
 *   POST /api/v1/games/{id}/double/
 *
 * Produced by GameService._build_response() in services.py.
 *
 * Lifecycle:
 *   IN_PROGRESS → outcome = null, new_balance absent, hole card hidden.
 *   COMPLETED   → outcome set, new_balance present, all cards revealed.
 */
export interface GameState {
  game_id:      string;       // UUID
  status:       GameStatus;
  table:        GameTableInfo;
  player_bet:   string;       // Decimal string

  player_hand:  PlayerHand;
  dealer_hand:  DealerHand;
  bot_hands:    BotHand[];

  /** Cards remaining in the shared deck — fun for card counters. */
  cards_remaining: number;

  /** Side bets resolved at deal time. Empty object if none were placed. */
  side_bets: SideBets;

  /** Cards in the table's discard pile (played in previous hands). */
  discard_count: number;

  /**
   * True while the CUT sentinel was drawn during this round but the
   * end-of-round reshuffle hasn't fired yet. Use this to show a
   * "reshuffle coming" indicator in the UI.
   */
  reshuffle_pending: boolean;

  /**
   * True when the end-of-round reshuffle fired during this API call.
   * When true: cards_remaining resets to ~312 and discard_count resets to 0.
   * Use this to trigger the reshuffle animation / notification in the UI.
   */
  reshuffle_occurred: boolean;

  outcome:   GameOutcome;

  /**
   * Net P&L applied to the player's balance.
   * Positive = profit, negative = loss, "0.00" = push.
   * null while game is in progress.
   */
  net_payout: string | null;

  /**
   * Updated balance AFTER payout.  Only present when COMPLETED.
   * Sync this to the Zustand user.balance to avoid an extra /me/ call.
   */
  new_balance?: string;
}

// ─── Lobby table ───────────────────────────────────────────────────────────────

/** Full table record returned by GET /api/v1/tables/ */
export interface Table {
  id:             number;
  level:          number;
  bot_count:      number;
  min_bet:        string;   // Decimal string
  max_bet:        string;   // Decimal string
  unlock_balance: string;   // Decimal string
  /**
   * True when the player's balance < unlock_balance.
   * Computed server-side — the frontend never holds economy logic.
   */
  is_locked:      boolean;
}

// ─── Auth / user ───────────────────────────────────────────────────────────────

/** Shape returned by GET /api/v1/auth/me/ */
export interface UserProfile {
  user_id:    number;
  username:   string;
  email:      string;
  balance:    string;    // Decimal string — can be negative
  created_at: string;    // ISO 8601
}

/** Shape returned by /auth/register/ and /auth/login/ */
export interface AuthResponse {
  token: string;
  user:  UserProfile;
}

// ─── Leaderboard ──────────────────────────────────────────────────────────────

export interface LeaderboardEntry {
  rank:     number;
  username: string;
  balance:  string;   // Decimal string
}

/** Response body from GET /api/v1/leaderboard/{table_id}/ */
export interface LeaderboardRestResponse {
  table_id:    number;
  leaderboard: LeaderboardEntry[];
}

/**
 * Payload of every SSE `data:` event on /sse/leaderboard/{table_id}/
 * Matches the JSON emitted by _leaderboard_event_stream() in views.py.
 */
export interface SSELeaderboardPayload {
  type:     'leaderboard';
  table_id: number;
  data:     LeaderboardEntry[];
}

/** Emitted by the SSE stream when a shoe reshuffle occurs at this table. */
export interface SSEReshufflePayload {
  type:     'reshuffle';
  table_id: number;
}

export type SSEPayload = SSELeaderboardPayload | SSEReshufflePayload;

// ─── API request payloads ──────────────────────────────────────────────────────

export interface RegisterPayload {
  username:         string;
  password:         string;
  password_confirm: string;
  email?:           string;
}

export interface LoginPayload {
  username: string;
  password: string;
}

/**
 * Send bet as a string to preserve decimal precision across the HTTP
 * boundary.  Build with: `bet.toFixed(2)` from a numeric form input.
 */
export interface CreateGamePayload {
  table_id:              number;
  bet:                   string;
  bot_count?:            number;
  perfect_pairs_bet?:    string;  // Decimal string
  twenty_one_three_bet?: string;  // Decimal string
  /** If true, backend resets the shoe to a fresh 312-card deck before dealing. */
  fresh_shoe?: boolean;
}

// ─── API error ─────────────────────────────────────────────────────────────────

/**
 * Normalised error produced by normalizeApiError() in api.ts.
 *
 * Django returns errors in several shapes that get flattened here:
 *   { error: "msg" }                GameService domain errors
 *   { detail: "msg" }               DRF auth / permission errors
 *   { field: ["msg"] }              DRF serializer field errors
 *   { non_field_errors: ["msg"] }   DRF cross-field validation errors
 */
export interface ApiError {
  /** Human-readable string — safe to render directly in the UI. */
  message:      string;
  /** HTTP status code; 0 = network failure before server responded. */
  statusCode:   number;
  /** Per-field validation errors keyed by field name. Used for form hints. */
  fieldErrors?: Record<string, string[]>;
  /** Original raw response body for debugging. */
  raw?:         unknown;
}

// ─── Zustand store auxiliary types ────────────────────────────────────────────

/** Which game-action API call is currently in flight. */
export type GameAction = 'create' | 'hit' | 'stand' | 'double' | 'leave' | null;

/** Which modal overlay is visible. */
export type ModalType = 'result' | 'rules' | null;

/** SSE connection lifecycle states. */
export type SSEConnectionStatus =
  | 'idle'
  | 'connecting'
  | 'connected'
  | 'reconnecting'
  | 'error'
  | 'closed';