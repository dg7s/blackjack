"""
game/services.py
================
Core GameService layer — the only place game logic lives.

Architecture contract:
    • Views are dumb: they validate HTTP input, call a single GameService
      method, and return the result as JSON.
    • GameService owns: deck management, dealing, hit/stand/double-down,
      bot AI, dealer AI, payout calculation, balance updates, SSE signalling.
    • The deck NEVER travels to the frontend. Every card draw is a
      server-side operation whose result is returned to the client.
    • All public methods are wrapped in ``transaction.atomic()`` to guarantee
      that partial state (e.g. cards dealt but balance not updated) can
      never be persisted on an error.

Card notation
    Format  : {rank}{suit}
    Ranks   : A 2 3 4 5 6 7 8 9 T J Q K
    Suits   : H D C S  (Hearts Diamonds Clubs Spades)
    Examples: "AH" = Ace of Hearts, "TC" = Ten of Clubs, "7D" = 7 of Diamonds

Shoe mechanics (CUT card system)
    6 standard decks (312 cards) shuffled together. A CUT sentinel string
    ("CUT") is inserted at a random position 234–260 cards from the top of
    the shuffled shoe. Cards are drawn directly from the shared table shoe
    during play — there is no per-game buffer.

    When the CUT sentinel is encountered during a draw it is discarded and
    ``table.needs_reshuffle`` is set True. Play continues normally with the
    next real card. The actual reshuffle (merge shoe remainder + discard into
    a new shuffled shoe with a fresh CUT placement) fires at the END of the
    round — never mid-hand.

    Played cards accumulate in ``table.discard_state`` hand-over-hand.
    Between hands: shoe_real + discard_real == 312 (CUT not counted).
"""

import logging
import random
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from django.contrib.auth.models import User
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from .models import Game, GameEvent, Hand, Table, UserProfile

logger = logging.getLogger(__name__)


# ─── Card Constants ────────────────────────────────────────────────────────────

SUITS: list[str] = ["H", "D", "C", "S"]
RANKS: list[str] = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "T", "J", "Q", "K"]

# 6-deck shoe
SHOE_DECKS: int = 6
TOTAL_SHOE_CARDS: int = SHOE_DECKS * 52  # 312 — invariant: shoe_real + discard = 312 between hands

# CUT card system: sentinel inserted ~1.5 decks from bottom; triggers end-of-round reshuffle
CUT_SENTINEL: str = "CUT"
CUT_CARD_POS_MIN: int = 234  # earliest position from top the CUT may appear
CUT_CARD_POS_MAX: int = 260  # latest position from top the CUT may appear

# Base card values — Ace handled separately in calculate_hand_value()
CARD_VALUES: dict[str, int] = {
    "A": 11,
    "2": 2,
    "3": 3,
    "4": 4,
    "5": 5,
    "6": 6,
    "7": 7,
    "8": 8,
    "9": 9,
    "T": 10,
    "J": 10,
    "Q": 10,
    "K": 10,
}

RANK_DISPLAY: dict[str, str] = {
    "A": "Ace",
    "2": "2",
    "3": "3",
    "4": "4",
    "5": "5",
    "6": "6",
    "7": "7",
    "8": "8",
    "9": "9",
    "T": "10",
    "J": "Jack",
    "Q": "Queen",
    "K": "King",
}

SUIT_DISPLAY: dict[str, str] = {
    "H": "♥",
    "D": "♦",
    "C": "♣",
    "S": "♠",
}

# Blackjack pays 3:2
BLACKJACK_MULTIPLIER: Decimal = Decimal("1.5")
LEADERBOARD_CACHE_TTL: int = 30
LEADERBOARD_SIZE: int = 5
DEFAULT_STARTING_BALANCE: Decimal = Decimal("1000.00")
TWO_PLACES = Decimal("0.01")

# ─── Side-bet tables ───────────────────────────────────────────────────────────

SUIT_COLORS: dict[str, str] = {"H": "red", "D": "red", "C": "black", "S": "black"}

# Perfect Pairs multipliers (bet × multiplier = PROFIT; stake is also returned)
PERFECT_PAIRS_PAYOUTS: dict[str, Decimal] = {
    "perfect": Decimal("25"),   # Same rank + same suit
    "colored": Decimal("10"),   # Same rank + same color, different suit
    "mixed":   Decimal("5"),    # Same rank, different color
}

# 21+3 multipliers (bet × multiplier = PROFIT; stake is also returned)
TWENTY_ONE_THREE_PAYOUTS: dict[str, Decimal] = {
    "suited_trips":    Decimal("100"),
    "straight_flush":  Decimal("40"),
    "three_of_a_kind": Decimal("30"),
    "straight":        Decimal("10"),
    "flush":           Decimal("5"),
}

# Rank ordering used for straight detection (Ace = index 0, King = 12)
RANK_ORDER: list[str] = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "T", "J", "Q", "K"]


# ─── Custom Exceptions ─────────────────────────────────────────────────────────


class GameServiceError(Exception):
    pass


class InvalidBetError(GameServiceError):
    pass


class TableAccessDeniedError(GameServiceError):
    pass


class InvalidGameActionError(GameServiceError):
    pass


class ActiveGameExistsError(GameServiceError):
    pass


# ─── Pure Card Logic Helpers ───────────────────────────────────────────────────


def build_deck() -> list[str]:
    """Return a new, unshuffled 52-card deck."""
    return [f"{rank}{suit}" for suit in SUITS for rank in RANKS]


def build_shoe(n: int = SHOE_DECKS) -> list[str]:
    """Return a new, unshuffled shoe of n standard 52-card decks."""
    return build_deck() * n


def build_shoe_with_cut() -> list[str]:
    """Return a shuffled 6-deck shoe (312 cards) with a CUT sentinel inserted."""
    shoe = build_shoe()
    random.shuffle(shoe)
    pos = random.randint(CUT_CARD_POS_MIN, CUT_CARD_POS_MAX)
    shoe.insert(pos, CUT_SENTINEL)
    return shoe


def shuffle_deck(deck: list[str]) -> list[str]:
    """Return a new shuffled copy of deck."""
    shuffled = deck.copy()
    random.shuffle(shuffled)
    return shuffled


def calculate_hand_value(cards: list[str]) -> tuple[int, bool]:
    """
    Calculate the optimal (highest non-busting) value for a hand.

    Returns (total, is_soft) where is_soft is True when an Ace is still counted as 11.
    """
    total: int = 0
    ace_count: int = 0

    for card in cards:
        rank = card[0]
        if rank not in CARD_VALUES:
            continue  # skip sentinel or invalid strings
        total += CARD_VALUES[rank]
        if rank == "A":
            ace_count += 1

    while total > 21 and ace_count > 0:
        total -= 10
        ace_count -= 1

    is_soft: bool = ace_count > 0
    return total, is_soft


def is_blackjack(cards: list[str]) -> bool:
    if len(cards) != 2:
        return False
    value, _ = calculate_hand_value(cards)
    return value == 21


def is_bust(cards: list[str]) -> bool:
    value, _ = calculate_hand_value(cards)
    return value > 21


def card_display(card: str) -> str:
    rank, suit = card[0], card[1]
    return f"{RANK_DISPLAY[rank]} of {SUIT_DISPLAY[suit]}"


def resolve_perfect_pairs(card1: str, card2: str) -> tuple[str | None, Decimal]:
    """Return (outcome_key, multiplier) for a Perfect Pairs side bet, or (None, 0) for no pair."""
    r1, s1 = card1[0], card1[1]
    r2, s2 = card2[0], card2[1]
    if r1 != r2:
        return None, Decimal("0")
    if s1 == s2:
        return "perfect", PERFECT_PAIRS_PAYOUTS["perfect"]
    if SUIT_COLORS[s1] == SUIT_COLORS[s2]:
        return "colored", PERFECT_PAIRS_PAYOUTS["colored"]
    return "mixed", PERFECT_PAIRS_PAYOUTS["mixed"]


def _is_consecutive_ranks(rank_indices: list[int]) -> bool:
    """True when three rank indices span exactly 2 with no duplicates."""
    s = sorted(rank_indices)
    return s[2] - s[0] == 2 and len(set(s)) == 3


def resolve_twenty_one_three(c1: str, c2: str, c3: str) -> tuple[str | None, Decimal]:
    """
    Return (outcome_key, multiplier) for a 21+3 side bet, or (None, 0) for no win.

    Winning hands (highest to lowest):
        suited_trips    — same rank + same suit  (100:1)
        straight_flush  — consecutive ranks + same suit  (40:1)
        three_of_a_kind — same rank, mixed suits  (30:1)
        straight        — consecutive ranks, mixed suits  (10:1)
        flush           — same suit, non-consecutive  (5:1)

    Ace is treated as LOW (before 2) or HIGH (after K) for straight detection.
    """
    ranks = [x[0] for x in (c1, c2, c3)]
    suits = [x[1] for x in (c1, c2, c3)]
    all_same_suit = len(set(suits)) == 1
    all_same_rank = len(set(ranks)) == 1

    raw_idxs = [RANK_ORDER.index(r) for r in ranks]
    # Ace-high variant: treat Ace as index 13 (after King)
    alt_idxs = [13 if r == "A" else RANK_ORDER.index(r) for r in ranks]
    is_straight = _is_consecutive_ranks(raw_idxs) or _is_consecutive_ranks(alt_idxs)

    if all_same_rank and all_same_suit:
        return "suited_trips",    TWENTY_ONE_THREE_PAYOUTS["suited_trips"]
    if is_straight and all_same_suit:
        return "straight_flush",  TWENTY_ONE_THREE_PAYOUTS["straight_flush"]
    if all_same_rank:
        return "three_of_a_kind", TWENTY_ONE_THREE_PAYOUTS["three_of_a_kind"]
    if is_straight:
        return "straight",        TWENTY_ONE_THREE_PAYOUTS["straight"]
    if all_same_suit:
        return "flush",           TWENTY_ONE_THREE_PAYOUTS["flush"]
    return None, Decimal("0")


# ─── GameService ───────────────────────────────────────────────────────────────


class GameService:
    """
    Central service layer for all Blackjack game operations.

    Public interface:
        create_game(user, table_id, bet, bot_count)  → game state dict
        player_hit(game_id, user)                    → game state dict
        player_stand(game_id, user)                  → game state dict
        player_double_down(game_id, user)            → game state dict
        leave_game(game_id, user)                    → game state dict
        get_leaderboard(table_id, limit)             → list[dict]
    """

    # ══════════════════════════════════════════════════════════════════════════
    # PUBLIC API
    # ══════════════════════════════════════════════════════════════════════════

    @classmethod
    @transaction.atomic
    def create_game(
        cls,
        user: User,
        table_id: int,
        bet: Decimal,
        bot_count: int | None = None,
        perfect_pairs_bet: Decimal | None = None,
        twenty_one_three_bet: Decimal | None = None,
        fresh_shoe: bool = False,
    ) -> dict[str, Any]:
        """
        Start a new Blackjack game.

        Steps
        -----
        1.  Validate table access, main bet, and optional side bets.
        2.  Ensure no other IN_PROGRESS game exists for this user.
        3.  Deduct main bet + side bets from balance (escrow).
        4.  Initialize shoe: fresh (fresh_shoe=True or empty) or continue existing shoe.
        5.  Create Game and Hand rows.
        6.  Deal right-to-left (bots by index ascending = seats descending).
        7.  Resolve side bets immediately (Perfect Pairs, 21+3).
        8.  Play all bot hands (bots always sit right of player — they act first).
        9.  Check for immediate Blackjack.
        """
        profile: UserProfile = cls._get_or_create_profile(user)

        try:
            table: Table = (
                Table.objects
                .select_for_update()
                .get(id=table_id)
            )
        except Table.DoesNotExist:
            raise InvalidGameActionError(f"Table with id={table_id} does not exist.")

        cls._validate_table_access(profile, table)
        cls._validate_bet(profile, table, bet)
        cls._validate_side_bets(profile, table, bet, perfect_pairs_bet, twenty_one_three_bet)
        cls._check_no_active_game(user)

        # ── Escrow main bet + side bets ───────────────────────────────────────
        total_escrow = bet
        if perfect_pairs_bet:
            total_escrow += perfect_pairs_bet
        if twenty_one_three_bet:
            total_escrow += twenty_one_three_bet
        profile.balance -= total_escrow
        profile.save(update_fields=["balance"])

        # ── Initialize shoe ───────────────────────────────────────────────────
        if fresh_shoe:
            # Player just entered the table — start a pristine shoe
            table.shoe_state = build_shoe_with_cut()
            table.discard_state = []
            table.needs_reshuffle = False
            logger.info("Fresh shoe built for table_id=%s (fresh_shoe requested)", table_id)
        elif not table.shoe_state:
            if table.discard_state:
                all_cards = list(table.discard_state)
                random.shuffle(all_cards)
                pos = random.randint(CUT_CARD_POS_MIN, CUT_CARD_POS_MAX)
                all_cards.insert(pos, CUT_SENTINEL)
                table.shoe_state = all_cards
                table.discard_state = []
                logger.info(
                    "Shoe rebuilt from discard at create_game: %d cards, table_id=%s",
                    len(all_cards), table_id,
                )
            else:
                table.shoe_state = build_shoe_with_cut()
                logger.info("Fresh shoe built for table_id=%s", table_id)
        table.save(update_fields=["shoe_state", "discard_state", "needs_reshuffle"])

        cls._verify_card_count(table, 0, f"pre_deal_t{table_id}")

        # ── Create Game ───────────────────────────────────────────────────────
        game: Game = Game.objects.create(
            table=table,
            player=user,
            deck_state=[],
            status=Game.Status.IN_PROGRESS,
            player_bet=bet,
        )
        game.table = table
        game.player = user

        # ── Create Hand rows ──────────────────────────────────────────────────
        actual_bot_count: int = (
            table.bot_count
            if bot_count is None
            else max(0, min(bot_count, table.bot_count))
        )

        bot_hands: list[Hand] = [
            Hand.objects.create(game=game, hand_type=Hand.HandType.BOT, bot_index=i, cards=[])
            for i in range(actual_bot_count)
        ]

        player_hand: Hand = Hand.objects.create(game=game, hand_type=Hand.HandType.PLAYER, cards=[])
        dealer_hand: Hand = Hand.objects.create(game=game, hand_type=Hand.HandType.DEALER, cards=[])

        # ── Deal two rounds right-to-left (bots by index = rightmost seat first) ──
        for _round in range(2):
            for bot_hand in bot_hands:          # bot_index 0 = seat 4, 1 = seat 3, …
                cls._deal_card_to_hand(game, bot_hand, table)
            cls._deal_card_to_hand(game, player_hand, table)
            cls._deal_card_to_hand(game, dealer_hand, table)

        # ── Evaluate initial statuses ─────────────────────────────────────────
        cls._update_hand_status(player_hand)
        for bot_hand in bot_hands:
            cls._update_hand_status(bot_hand)

        # ── Resolve side bets immediately after deal ──────────────────────────
        # Side bets depend only on the 2 initial player cards + dealer face-up card.
        # We resolve them now, before bot play, so the result is always available.
        cls._apply_side_bets(
            game, player_hand, dealer_hand, profile,
            perfect_pairs_bet, twenty_one_three_bet,
        )
        # Reload profile so _resolve_game sees the updated balance (side-bet credits).
        profile.refresh_from_db(fields=["balance"])

        # ── Immediate Blackjack resolution ────────────────────────────────────
        if player_hand.status == Hand.HandStatus.BLACKJACK:
            logger.info("Immediate blackjack for user=%s game=%s", user.username, game.id)
            return cls._resolve_game(
                game=game,
                table=table,
                player_hand=player_hand,
                dealer_hand=dealer_hand,
                bot_hands=bot_hands,
                profile=profile,
                immediate_blackjack=True,
            )

        # ── Play all bot hands (bots occupy rightmost seats → act before player) ──
        for bot_hand in bot_hands:
            if bot_hand.status == Hand.HandStatus.ACTIVE:
                cls._play_bot_hand(game, bot_hand, table)

        # ── Log GAME_START event ──────────────────────────────────────────────
        GameEvent.objects.create(
            game=game,
            event_type=GameEvent.EventType.GAME_START,
            payload=cls._build_response(
                game, table, player_hand, dealer_hand, bot_hands,
                reveal_dealer=False,
            ),
        )

        return cls._build_response(
            game, table, player_hand, dealer_hand, bot_hands,
            reveal_dealer=False,
        )

    # ──────────────────────────────────────────────────────────────────────────

    @classmethod
    @transaction.atomic
    def player_hit(cls, game_id: str, user: User) -> dict[str, Any]:
        game, table, player_hand, dealer_hand, bot_hands = cls._load_game_state(game_id, user)

        if game.status != Game.Status.IN_PROGRESS:
            raise InvalidGameActionError("This game is not currently in progress.")
        if player_hand.status != Hand.HandStatus.ACTIVE:
            raise InvalidGameActionError(
                f"Cannot hit: player hand status is '{player_hand.status}'."
            )

        card = cls._deal_card_to_hand(game, player_hand, table)
        cls._update_hand_status(player_hand)

        GameEvent.objects.create(
            game=game,
            event_type=GameEvent.EventType.PLAYER_HIT,
            payload={
                "card_drawn": card,
                "hand": player_hand.cards,
                "value": calculate_hand_value(player_hand.cards)[0],
                "status": player_hand.status,
            },
        )

        if player_hand.status == Hand.HandStatus.BUST:
            GameEvent.objects.create(
                game=game,
                event_type=GameEvent.EventType.PLAYER_BUST,
                payload={
                    "hand": player_hand.cards,
                    "value": calculate_hand_value(player_hand.cards)[0],
                },
            )
            profile = cls._get_or_create_profile(user)
            return cls._resolve_game(
                game, table, player_hand, dealer_hand, bot_hands, profile,
            )

        return cls._build_response(
            game, table, player_hand, dealer_hand, bot_hands,
            reveal_dealer=False,
        )

    # ──────────────────────────────────────────────────────────────────────────

    @classmethod
    @transaction.atomic
    def player_stand(cls, game_id: str, user: User) -> dict[str, Any]:
        game, table, player_hand, dealer_hand, bot_hands = cls._load_game_state(game_id, user)

        if game.status != Game.Status.IN_PROGRESS:
            raise InvalidGameActionError("This game is not currently in progress.")
        if player_hand.status != Hand.HandStatus.ACTIVE:
            raise InvalidGameActionError(
                f"Cannot stand: player hand status is '{player_hand.status}'."
            )

        player_hand.status = Hand.HandStatus.STAND
        player_hand.save(update_fields=["status"])

        GameEvent.objects.create(
            game=game,
            event_type=GameEvent.EventType.PLAYER_STAND,
            payload={
                "hand": player_hand.cards,
                "value": calculate_hand_value(player_hand.cards)[0],
            },
        )

        profile = cls._get_or_create_profile(user)
        return cls._resolve_game(game, table, player_hand, dealer_hand, bot_hands, profile)

    # ──────────────────────────────────────────────────────────────────────────

    @classmethod
    @transaction.atomic
    def player_double_down(cls, game_id: str, user: User) -> dict[str, Any]:
        game, table, player_hand, dealer_hand, bot_hands = cls._load_game_state(game_id, user)

        if game.status != Game.Status.IN_PROGRESS:
            raise InvalidGameActionError("This game is not currently in progress.")
        if player_hand.status != Hand.HandStatus.ACTIVE:
            raise InvalidGameActionError(
                f"Cannot double down: player hand status is '{player_hand.status}'."
            )
        if len(player_hand.cards) != 2:
            raise InvalidGameActionError(
                "Double down is only permitted on your initial 2-card hand."
            )

        profile = cls._get_or_create_profile(user)
        additional_bet: Decimal = game.player_bet

        if profile.balance < additional_bet:
            raise InvalidBetError(
                f"Insufficient balance to double down. "
                f"You need {additional_bet} but have {profile.balance}."
            )

        profile.balance -= additional_bet
        profile.save(update_fields=["balance"])

        game.player_bet = (game.player_bet * 2).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        game.save(update_fields=["player_bet"])

        card = cls._deal_card_to_hand(game, player_hand, table)
        cls._update_hand_status(player_hand)

        GameEvent.objects.create(
            game=game,
            event_type=GameEvent.EventType.PLAYER_DOUBLE,
            payload={
                "card_drawn": card,
                "hand": player_hand.cards,
                "new_bet": str(game.player_bet),
                "value": calculate_hand_value(player_hand.cards)[0],
                "status": player_hand.status,
            },
        )

        if player_hand.status == Hand.HandStatus.BUST:
            GameEvent.objects.create(
                game=game,
                event_type=GameEvent.EventType.PLAYER_BUST,
                payload={
                    "hand": player_hand.cards,
                    "value": calculate_hand_value(player_hand.cards)[0],
                },
            )
            return cls._resolve_game(
                game, table, player_hand, dealer_hand, bot_hands, profile,
            )

        player_hand.status = Hand.HandStatus.STAND
        player_hand.save(update_fields=["status"])

        return cls._resolve_game(
            game, table, player_hand, dealer_hand, bot_hands, profile,
        )

    # ──────────────────────────────────────────────────────────────────────────

    @classmethod
    @transaction.atomic
    def leave_game(cls, game_id: str, user: User) -> dict[str, Any]:
        game, table, player_hand, dealer_hand, bot_hands = cls._load_game_state(game_id, user)

        if game.status != Game.Status.IN_PROGRESS:
            raise InvalidGameActionError(
                "Only an IN_PROGRESS game can be left. This game is already completed."
            )

        net_change = (-game.player_bet).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

        game.outcome = Game.Outcome.LOSE
        game.payout = net_change
        game.status = Game.Status.COMPLETED
        game.left_at = timezone.now()

        # Move played cards to discard pile (no game buffer in CUT-card system)
        played = (
            list(player_hand.cards)
            + list(dealer_hand.cards)
            + [card for bh in bot_hands for card in bh.cards]
        )
        table.discard_state = list(table.discard_state) + played
        game.deck_state = []

        game.save(update_fields=["outcome", "payout", "status", "left_at", "deck_state"])

        cls._verify_card_count(table, 0, f"leave_game_g{str(game.id)[:8]}")

        reshuffle_occurred = False
        if table.needs_reshuffle:
            cls._do_reshuffle(table)
            reshuffle_occurred = True
            cls._mark_reshuffle_event(game.table_id)
            logger.info("End-of-round reshuffle (leave) for table_id=%s", table.id)

        table.save(update_fields=["discard_state", "shoe_state", "needs_reshuffle"])

        GameEvent.objects.create(
            game=game,
            event_type=GameEvent.EventType.GAME_RESOLVE,
            payload={
                "outcome": Game.Outcome.LOSE,
                "reason": "player_left",
                "net_change": str(net_change),
            },
        )

        cls._mark_leaderboard_dirty(game.table_id)

        return cls._build_response(
            game, table, player_hand, dealer_hand, bot_hands,
            reveal_dealer=True,
            reshuffle_occurred=reshuffle_occurred,
        )

    # ──────────────────────────────────────────────────────────────────────────

    @classmethod
    def get_leaderboard(
        cls,
        table_id: int,
        limit: int = LEADERBOARD_SIZE,
    ) -> list[dict[str, Any]]:
        player_ids = (
            Game.objects.filter(table_id=table_id, status=Game.Status.COMPLETED)
            .values_list("player_id", flat=True)
            .distinct()
        )

        profiles = (
            UserProfile.objects
            .filter(user_id__in=player_ids)
            .select_related("user")
            .order_by("-balance")[:limit]
        )

        return [
            {
                "rank": idx + 1,
                "username": p.user.username,
                "balance": str(p.balance),
            }
            for idx, p in enumerate(profiles)
        ]

    # ══════════════════════════════════════════════════════════════════════════
    # RESOLUTION ENGINE (private)
    # ══════════════════════════════════════════════════════════════════════════

    @classmethod
    def _resolve_game(
        cls,
        game: Game,
        table: Table,
        player_hand: Hand,
        dealer_hand: Hand,
        bot_hands: list[Hand],
        profile: UserProfile,
        immediate_blackjack: bool = False,
    ) -> dict[str, Any]:
        """
        Resolve the round after the player can no longer act.

        Resolution sequence
        -------------------
        1.  Play all bot hands (right-to-left, which is bot_index ascending).
        2.  Reveal the dealer's hole card and play the dealer hand.
        3.  Determine the player's outcome against the dealer.
        4.  Calculate the payout and credit the player's balance.
        5.  Finalize the Game row; move all cards to the table's discard pile.
        6.  If the CUT sentinel was drawn this round, reshuffle at end-of-round.
        7.  Signal the SSE leaderboard cache.
        """
        # ── Step 1: Bot hands ─────────────────────────────────────────────────
        for bot_hand in bot_hands:
            if bot_hand.status == Hand.HandStatus.ACTIVE:
                cls._play_bot_hand(game, bot_hand, table)

        # ── Step 2: Dealer hand ───────────────────────────────────────────────
        cls._play_dealer_hand(game, dealer_hand, table)

        GameEvent.objects.create(
            game=game,
            event_type=GameEvent.EventType.DEALER_PLAY,
            payload={
                "hand": dealer_hand.cards,
                "value": calculate_hand_value(dealer_hand.cards)[0],
                "status": dealer_hand.status,
            },
        )

        # ── Step 3: Determine outcome ─────────────────────────────────────────
        player_value, _ = calculate_hand_value(player_hand.cards)
        dealer_value, _ = calculate_hand_value(dealer_hand.cards)

        outcome, gross_payout = cls._calculate_payout(
            player_hand=player_hand,
            player_value=player_value,
            dealer_hand=dealer_hand,
            dealer_value=dealer_value,
            bet=game.player_bet,
            immediate_blackjack=immediate_blackjack,
        )

        # ── Step 4: Credit balance ────────────────────────────────────────────
        profile.balance += gross_payout
        profile.save(update_fields=["balance"])

        net_change: Decimal = (gross_payout - game.player_bet).quantize(
            TWO_PLACES, rounding=ROUND_HALF_UP
        )

        # ── Step 5: Finalize Game + accumulate discard pile ───────────────────
        played = (
            list(player_hand.cards)
            + list(dealer_hand.cards)
            + [card for bh in bot_hands for card in bh.cards]
        )
        table.discard_state = list(table.discard_state) + played
        game.deck_state = []

        game.outcome = outcome
        game.payout = net_change
        game.status = Game.Status.COMPLETED
        game.save(update_fields=["outcome", "payout", "status", "deck_state"])

        cls._verify_card_count(table, 0, f"post_resolve_g{str(game.id)[:8]}")

        # ── Step 6: End-of-round reshuffle if CUT card was drawn ──────────────
        reshuffle_occurred = False
        if table.needs_reshuffle:
            cls._do_reshuffle(table)
            reshuffle_occurred = True
            cls._mark_reshuffle_event(game.table_id)
            logger.info("End-of-round reshuffle for table_id=%s", table.id)

        table.save(update_fields=["discard_state", "shoe_state", "needs_reshuffle"])

        GameEvent.objects.create(
            game=game,
            event_type=GameEvent.EventType.GAME_RESOLVE,
            payload={
                "outcome": outcome,
                "gross_payout": str(gross_payout),
                "net_change": str(net_change),
                "new_balance": str(profile.balance),
                "player_value": player_value,
                "dealer_value": dealer_value,
                "bet": str(game.player_bet),
            },
        )

        # ── Step 7: SSE leaderboard signal ───────────────────────────────────
        cls._mark_leaderboard_dirty(game.table_id)

        return cls._build_response(
            game, table, player_hand, dealer_hand, bot_hands,
            reveal_dealer=True,
            reshuffle_occurred=reshuffle_occurred,
        )

    # ══════════════════════════════════════════════════════════════════════════
    # BOT & DEALER AI (private)
    # ══════════════════════════════════════════════════════════════════════════

    @classmethod
    def _play_bot_hand(cls, game: Game, bot_hand: Hand, table: Table) -> None:
        """Bot AI: hit until hard ≥ 17 or busted."""
        cards_drawn: list[str] = []

        while True:
            value, _ = calculate_hand_value(bot_hand.cards)

            if value > 21:
                bot_hand.status = Hand.HandStatus.BUST
                bot_hand.is_soft = False
                break

            if value >= 17:
                bot_hand.status = Hand.HandStatus.STAND
                _, is_soft = calculate_hand_value(bot_hand.cards)
                bot_hand.is_soft = is_soft
                break

            card = cls._deal_card_to_hand(game, bot_hand, table)
            cards_drawn.append(card)

        bot_hand.save(update_fields=["status", "is_soft"])

        GameEvent.objects.create(
            game=game,
            event_type=GameEvent.EventType.BOT_ACTION,
            payload={
                "bot_index": bot_hand.bot_index,
                "cards_drawn": cards_drawn,
                "final_hand": bot_hand.cards,
                "final_value": calculate_hand_value(bot_hand.cards)[0],
                "status": bot_hand.status,
            },
        )

    @classmethod
    def _play_dealer_hand(cls, game: Game, dealer_hand: Hand, table: Table) -> None:
        """Dealer AI: hit on soft 17, stand on hard 17+."""
        while True:
            value, is_soft = calculate_hand_value(dealer_hand.cards)

            if value > 21:
                dealer_hand.status = Hand.HandStatus.BUST
                dealer_hand.is_soft = False
                break

            if value > 17:
                dealer_hand.status = Hand.HandStatus.STAND
                dealer_hand.is_soft = is_soft
                break

            if value == 17 and not is_soft:
                dealer_hand.status = Hand.HandStatus.STAND
                dealer_hand.is_soft = False
                break

            cls._deal_card_to_hand(game, dealer_hand, table)

        dealer_hand.save(update_fields=["status", "is_soft"])

    # ══════════════════════════════════════════════════════════════════════════
    # PAYOUT CALCULATION (private)
    # ══════════════════════════════════════════════════════════════════════════

    @classmethod
    def _calculate_payout(
        cls,
        player_hand: Hand,
        player_value: int,
        dealer_hand: Hand,
        dealer_value: int,
        bet: Decimal,
        immediate_blackjack: bool,
    ) -> tuple[str, Decimal]:
        q = TWO_PLACES

        if player_hand.status == Hand.HandStatus.BUST:
            return Game.Outcome.LOSE, Decimal("0.00")

        dealer_is_blackjack: bool = is_blackjack(dealer_hand.cards)

        if immediate_blackjack:
            if dealer_is_blackjack:
                return Game.Outcome.PUSH, bet.quantize(q, rounding=ROUND_HALF_UP)
            else:
                profit = (bet * BLACKJACK_MULTIPLIER).quantize(q, rounding=ROUND_HALF_UP)
                return Game.Outcome.BLACKJACK, bet + profit

        if dealer_hand.status == Hand.HandStatus.BUST:
            return Game.Outcome.WIN, (bet * 2).quantize(q, rounding=ROUND_HALF_UP)

        if player_value > dealer_value:
            return Game.Outcome.WIN, (bet * 2).quantize(q, rounding=ROUND_HALF_UP)
        elif player_value == dealer_value:
            return Game.Outcome.PUSH, bet.quantize(q, rounding=ROUND_HALF_UP)
        else:
            return Game.Outcome.LOSE, Decimal("0.00")

    # ══════════════════════════════════════════════════════════════════════════
    # LOW-LEVEL HELPERS (private)
    # ══════════════════════════════════════════════════════════════════════════

    @classmethod
    def _deal_card_to_hand(cls, game: Game, hand: Hand, table: Table) -> str:
        """
        Pop the top card from the shared table shoe and append it to hand.cards.

        If the CUT sentinel is encountered it is discarded and
        table.needs_reshuffle is set True; the next real card is dealt.
        The actual reshuffle fires at end-of-round, not here.
        Returns the dealt card string.
        """
        shoe: list[str] = list(table.shoe_state)

        if not shoe:
            logger.error(
                "Shoe empty mid-hand for game=%s — emergency fresh shoe.", game.id,
            )
            shoe = build_shoe_with_cut()

        card: str = shoe.pop(0)

        if card == CUT_SENTINEL:
            table.needs_reshuffle = True
            logger.info("CUT sentinel drawn for table_id=%s — reshuffle pending.", table.id)
            if shoe:
                card = shoe.pop(0)
            else:
                logger.error(
                    "Shoe empty after CUT for game=%s — emergency card.", game.id,
                )
                emergency = build_shoe_with_cut()
                card = emergency.pop(0)
                shoe = emergency

        table.shoe_state = shoe
        table.save(update_fields=["shoe_state", "needs_reshuffle"])

        hand.cards.append(card)
        hand.save(update_fields=["cards"])

        return card

    @classmethod
    def _update_hand_status(cls, hand: Hand) -> None:
        value, is_soft = calculate_hand_value(hand.cards)
        hand.is_soft = is_soft

        if is_blackjack(hand.cards):
            hand.status = Hand.HandStatus.BLACKJACK
        elif value > 21:
            hand.status = Hand.HandStatus.BUST

        hand.save(update_fields=["status", "is_soft"])

    @classmethod
    def _load_game_state(
        cls,
        game_id: str,
        user: User,
    ) -> tuple[Game, Table, Hand, Hand, list[Hand]]:
        """
        Load a Game, its associated Table (with row-level locks on both),
        and all its Hands.

        Returns (game, table, player_hand, dealer_hand, bot_hands_sorted_by_index)

        The Table lock is required because action methods write to
        table.discard_state at resolution time.
        """
        try:
            game: Game = (
                Game.objects
                .select_for_update()
                .select_related("table", "player")
                .get(id=game_id, player=user)
            )
        except Game.DoesNotExist:
            raise InvalidGameActionError(
                "Game not found or you do not have permission to access it."
            )

        # Lock the Table row separately so we can safely write to it
        table: Table = Table.objects.select_for_update().get(id=game.table_id)

        all_hands: list[Hand] = list(game.hands.all())

        try:
            player_hand: Hand = next(
                h for h in all_hands if h.hand_type == Hand.HandType.PLAYER
            )
            dealer_hand: Hand = next(
                h for h in all_hands if h.hand_type == Hand.HandType.DEALER
            )
        except StopIteration:
            raise InvalidGameActionError(
                f"Game {game_id} has corrupted hand data. Contact support."
            )

        bot_hands: list[Hand] = sorted(
            [h for h in all_hands if h.hand_type == Hand.HandType.BOT],
            key=lambda h: h.bot_index or 0,
        )

        return game, table, player_hand, dealer_hand, bot_hands

    # ══════════════════════════════════════════════════════════════════════════
    # VALIDATION HELPERS (private)
    # ══════════════════════════════════════════════════════════════════════════

    @staticmethod
    def _get_or_create_profile(user: User) -> UserProfile:
        profile, _ = UserProfile.objects.get_or_create(
            user=user,
            defaults={"balance": DEFAULT_STARTING_BALANCE},
        )
        return profile

    @staticmethod
    def _get_table(table_id: int) -> Table:
        try:
            return Table.objects.get(id=table_id)
        except Table.DoesNotExist:
            raise InvalidGameActionError(f"Table with id={table_id} does not exist.")

    @staticmethod
    def _validate_table_access(profile: UserProfile, table: Table) -> None:
        if profile.balance < table.unlock_balance:
            raise TableAccessDeniedError(
                f"This table requires a balance of at least {table.unlock_balance}. "
                f"Your current balance is {profile.balance}. "
                f"Keep playing at lower levels to unlock this table."
            )

    @staticmethod
    def _validate_bet(profile: UserProfile, table: Table, bet: Decimal) -> None:
        if bet < table.min_bet:
            raise InvalidBetError(
                f"Your bet of {bet} is below the minimum bet of {table.min_bet} at this table."
            )
        if bet > table.max_bet:
            raise InvalidBetError(
                f"Your bet of {bet} exceeds the maximum bet of {table.max_bet} at this table."
            )
        if profile.balance < bet:
            raise InvalidBetError(
                f"Insufficient balance. You have {profile.balance} chips "
                f"but tried to bet {bet}."
            )

    @staticmethod
    def _validate_side_bets(
        profile: UserProfile,
        table: Table,
        main_bet: Decimal,
        perfect_pairs_bet: Decimal | None,
        twenty_one_three_bet: Decimal | None,
    ) -> None:
        active = [b for b in [perfect_pairs_bet, twenty_one_three_bet] if b]
        if not active:
            return
        for sb in active:
            if sb > table.max_bet:
                raise InvalidBetError(
                    f"Side bet of {sb} exceeds the table maximum of {table.max_bet}."
                )
        side_total = sum(active, Decimal("0"))
        if profile.balance < main_bet + side_total:
            raise InvalidBetError(
                f"Insufficient balance. You need {main_bet + side_total} total "
                f"(main bet + side bets) but have {profile.balance}."
            )

    @classmethod
    def _apply_side_bets(
        cls,
        game: Game,
        player_hand: Hand,
        dealer_hand: Hand,
        profile: UserProfile,
        perfect_pairs_bet: Decimal | None,
        twenty_one_three_bet: Decimal | None,
    ) -> None:
        """
        Resolve Perfect Pairs and/or 21+3 side bets immediately after the initial deal.
        Credits any winnings to the player's balance; saves results to game.side_bets.
        """
        if not perfect_pairs_bet and not twenty_one_three_bet:
            return

        side_bets: dict[str, Any] = {}
        balance_credit = Decimal("0")

        if perfect_pairs_bet and perfect_pairs_bet > 0:
            outcome, multiplier = resolve_perfect_pairs(
                player_hand.cards[0], player_hand.cards[1]
            )
            if outcome:
                gross = (perfect_pairs_bet * (multiplier + 1)).quantize(
                    TWO_PLACES, rounding=ROUND_HALF_UP
                )
                net_payout = (gross - perfect_pairs_bet).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
                balance_credit += gross
            else:
                net_payout = (-perfect_pairs_bet).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
            side_bets["perfect_pairs"] = {
                "bet":       str(perfect_pairs_bet),
                "outcome":   outcome,
                "net_payout": str(net_payout),
            }

        if twenty_one_three_bet and twenty_one_three_bet > 0:
            dealer_up_card = dealer_hand.cards[0]
            outcome, multiplier = resolve_twenty_one_three(
                player_hand.cards[0], player_hand.cards[1], dealer_up_card
            )
            if outcome:
                gross = (twenty_one_three_bet * (multiplier + 1)).quantize(
                    TWO_PLACES, rounding=ROUND_HALF_UP
                )
                net_payout = (gross - twenty_one_three_bet).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
                balance_credit += gross
            else:
                net_payout = (-twenty_one_three_bet).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
            side_bets["twenty_one_three"] = {
                "bet":       str(twenty_one_three_bet),
                "outcome":   outcome,
                "net_payout": str(net_payout),
            }

        if balance_credit > 0:
            profile.balance += balance_credit
            profile.save(update_fields=["balance"])

        if side_bets:
            game.side_bets = side_bets
            game.save(update_fields=["side_bets"])

        logger.info(
            "Side bets resolved for game=%s: %s",
            game.id,
            {k: v["outcome"] for k, v in side_bets.items()},
        )

    @staticmethod
    def _check_no_active_game(user: User) -> None:
        if Game.objects.filter(
            player=user, status=Game.Status.IN_PROGRESS
        ).exists():
            raise ActiveGameExistsError(
                "You already have an active game in progress. "
                "Please finish it before starting a new one."
            )

    # ══════════════════════════════════════════════════════════════════════════
    # SSE CACHE SIGNAL (private)
    # ══════════════════════════════════════════════════════════════════════════

    @staticmethod
    def _mark_leaderboard_dirty(table_id: int) -> None:
        cache_key: str = f"leaderboard_dirty_{table_id}"
        cache.set(cache_key, True, timeout=LEADERBOARD_CACHE_TTL)
        logger.debug("SSE leaderboard dirty flag set for table_id=%s", table_id)

    @staticmethod
    def _mark_reshuffle_event(table_id: int) -> None:
        """Signal the SSE stream that a reshuffle just occurred for this table."""
        cache.set(f"reshuffle_event_{table_id}", True, timeout=LEADERBOARD_CACHE_TTL)
        logger.debug("SSE reshuffle event set for table_id=%s", table_id)

    @classmethod
    def _do_reshuffle(cls, table: Table) -> None:
        """Merge shoe remainder + discard into a fresh shuffled shoe with a new CUT placement."""
        real_cards = [c for c in table.shoe_state if c != CUT_SENTINEL]
        all_cards = real_cards + list(table.discard_state)
        random.shuffle(all_cards)
        pos = random.randint(CUT_CARD_POS_MIN, CUT_CARD_POS_MAX)
        all_cards.insert(pos, CUT_SENTINEL)
        table.shoe_state = all_cards
        table.discard_state = []
        table.needs_reshuffle = False
        logger.info(
            "Shoe reshuffle complete for table_id=%s: %d real cards + CUT at pos %d",
            table.id, len(all_cards) - 1, pos,
        )

    @staticmethod
    def _verify_card_count(table: Table, game_deck_len: int, context: str) -> None:
        """
        Assert shoe_real + discard + game_deck_len == TOTAL_SHOE_CARDS (312).
        CUT sentinel is excluded from the count.

        Valid at clean checkpoints: before dealing, after round resolution.
        """
        shoe_real = sum(1 for c in table.shoe_state if c != CUT_SENTINEL)
        discard   = len(table.discard_state)
        total     = shoe_real + discard + game_deck_len
        if total != TOTAL_SHOE_CARDS:
            logger.error(
                "CARD COUNT INVARIANT VIOLATED [%s]: "
                "shoe=%d + discard=%d + game_deck=%d = %d  (expected %d)  table_id=%s",
                context, shoe_real, discard, game_deck_len, total, TOTAL_SHOE_CARDS,
                table.id,
            )
        else:
            logger.debug(
                "Card count OK [%s]: shoe=%d + discard=%d + game_deck=%d = %d  table_id=%s",
                context, shoe_real, discard, game_deck_len, total, table.id,
            )

    # ══════════════════════════════════════════════════════════════════════════
    # RESPONSE SERIALIZATION (private)
    # ══════════════════════════════════════════════════════════════════════════

    @classmethod
    def _build_response(
        cls,
        game: Game,
        table: Table,
        player_hand: Hand,
        dealer_hand: Hand,
        bot_hands: list[Hand],
        reveal_dealer: bool,
        reshuffle_occurred: bool = False,
    ) -> dict[str, Any]:
        """
        Build the canonical serializable game state dict returned by all action methods.

        cards_remaining  = real cards left in shoe (CUT sentinel excluded).
        discard_count    = cumulative cards played in previous hands at this table.
        reshuffle_pending  = CUT was drawn this round; reshuffle fires at round end.
        reshuffle_occurred = True when the end-of-round reshuffle fired during this call.
        """
        player_value, player_is_soft = calculate_hand_value(player_hand.cards)

        if reveal_dealer:
            visible_dealer_cards: list[str] = dealer_hand.cards
        else:
            visible_dealer_cards = dealer_hand.cards[:1]

        visible_dealer_value: int = (
            calculate_hand_value(visible_dealer_cards)[0] if visible_dealer_cards else 0
        )

        bot_data: list[dict[str, Any]] = []
        for bot_hand in bot_hands:
            bot_value, _ = calculate_hand_value(bot_hand.cards)
            bot_data.append(
                {
                    "bot_index": bot_hand.bot_index,
                    "cards": bot_hand.cards,
                    "value": bot_value,
                    "status": bot_hand.status,
                }
            )

        shoe_real = sum(1 for c in table.shoe_state if c != CUT_SENTINEL)

        response: dict[str, Any] = {
            "game_id": str(game.id),
            "status": game.status,
            "table": {
                "id": game.table_id,
                "level": table.level,
                "bot_count": table.bot_count,
                "min_bet": str(table.min_bet),
                "max_bet": str(table.max_bet),
            },
            "player_bet": str(game.player_bet),
            "player_hand": {
                "cards": player_hand.cards,
                "value": player_value,
                "is_soft": player_is_soft,
                "status": player_hand.status,
            },
            "dealer_hand": {
                "cards": visible_dealer_cards,
                "value": visible_dealer_value,
                "status": dealer_hand.status if reveal_dealer else Hand.HandStatus.ACTIVE,
                "hole_card_hidden": not reveal_dealer,
            },
            "bot_hands": bot_data,
            # Real cards remaining in shoe (CUT sentinel excluded)
            "cards_remaining": shoe_real,
            # Persistent discard pile — grows hand-over-hand, resets only on reshuffle
            "discard_count": len(table.discard_state),
            # True while CUT was drawn this round but reshuffle hasn't fired yet
            "reshuffle_pending": table.needs_reshuffle,
            # True when the end-of-round reshuffle fired during this call
            "reshuffle_occurred": reshuffle_occurred,
            # Side bets resolved at deal time (empty dict if none placed)
            "side_bets": game.side_bets,
            "outcome": game.outcome,
            "net_payout": str(game.payout) if game.payout is not None else None,
        }

        if game.status == Game.Status.COMPLETED:
            try:
                response["new_balance"] = str(game.player.profile.balance)
            except UserProfile.DoesNotExist:
                response["new_balance"] = None

        return response
