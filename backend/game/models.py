"""
game/models.py
==============
Database models for the Blackjack application.

Model hierarchy:
    UserProfile  ──(1:1)──►  User
    Table        ──(1:N)──►  Game
    Game         ──(1:N)──►  Hand
    Game         ──(1:N)──►  GameEvent
"""

import uuid
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver


# ─── UserProfile ──────────────────────────────────────────────────────────────


class UserProfile(models.Model):
    """
    Extends Django's built-in User with game-specific data.

    The ``balance`` field is the single authoritative scoring metric for the
    leaderboard. It is updated atomically via ``GameService._resolve_game()``
    inside a database transaction, so it can never drift out of sync with
    completed game payouts.

    Starting balance is 1 000.00 chips. The balance CAN go negative (a player
    who loses everything still has a record).
    """

    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="profile",
    )
    balance = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("1000.00"),
        help_text="Current chip balance. Can be negative.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "User Profile"
        verbose_name_plural = "User Profiles"
        indexes = [
            # Leaderboard queries sort by balance; partial index on positive balances
            # is omitted here to keep migrations simple — add in production if needed.
            models.Index(fields=["-balance"], name="userprofile_balance_desc_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.user.username} (Balance: {self.balance})"


# Auto-create a UserProfile whenever a new User is saved.
@receiver(post_save, sender=User)
def create_user_profile(sender: type, instance: User, created: bool, **kwargs) -> None:  # type: ignore[override]
    if created:
        UserProfile.objects.get_or_create(user=instance)


# ─── Table ────────────────────────────────────────────────────────────────────


class Table(models.Model):
    """
    Represents a playable table configuration (a "Level").

    Tables are seed data — they are created once by the admin or a management
    command and are not user-generated. Each level maps 1:1 to a bot count:
    Level 0 → 0 bots, Level 3 → 3 bots, etc.

    ``unlock_balance`` enforces the economy progression: a player must have
    accumulated at least this much to sit at this table. Level 0 is always
    accessible (unlock_balance = 0).

    ``shoe_state`` is the shared 4-deck shoe (208 cards) that persists across
    games at this table. Cards are consumed sequentially; the shoe is reshuffled
    when fewer than SHOE_RESHUFFLE_THRESHOLD cards remain.
    """

    level = models.IntegerField(
        unique=True,
        help_text="Level number (0-based). Also determines bot_count.",
    )
    bot_count = models.IntegerField(
        validators=[MinValueValidator(0)],
        help_text="Number of AI bots sharing the deck at this table.",
    )
    min_bet = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("1.00"))],
    )
    max_bet = models.DecimalField(
        max_digits=10,
        decimal_places=2,
    )
    unlock_balance = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Minimum player balance required to access this table.",
    )
    shoe_state = models.JSONField(
        default=list,
        help_text="Remaining cards in the shared 6-deck shoe (includes 'CUT' sentinel). Populated by seed_tables.",
    )
    discard_state = models.JSONField(
        default=list,
        help_text="Cards played in previous hands, waiting to be reshuffled into the shoe.",
    )
    needs_reshuffle = models.BooleanField(
        default=False,
        help_text="Set True when the CUT sentinel is drawn. Triggers reshuffle before the next hand.",
    )

    class Meta:
        verbose_name = "Table"
        verbose_name_plural = "Tables"
        ordering = ["level"]

    def __str__(self) -> str:
        return (
            f"Level {self.level} — {self.bot_count} bot(s) | "
            f"Bet: {self.min_bet}–{self.max_bet} | "
            f"Unlock: {self.unlock_balance}"
        )


# ─── Game ─────────────────────────────────────────────────────────────────────


class Game(models.Model):
    """
    Represents one complete round of Blackjack.

    A game progresses through these statuses:
        IN_PROGRESS → COMPLETED

    The BETTING status is reserved for future multi-step UX flows where the
    player sets a bet before cards are dealt. In the current implementation,
    the bet is submitted at game creation and cards are dealt immediately
    (status starts at IN_PROGRESS).

    ``deck_state`` is the authoritative, server-side deck. It is a JSON list
    of card strings (e.g. ``["AH", "KC", "7D", ...]``). Every card draw pops
    from index 0 of this list and immediately persists the updated list, making
    the backend the single source of truth — the frontend never manages cards.

    ``payout`` stores the *net* change to the player's balance (positive = win,
    negative = loss, zero = push). The raw amount returned is always
    ``bet + payout`` (or just ``payout`` if it already represents the total
    depending on your accounting). See ``GameService._resolve_game`` for the
    exact calculation.
    """

    class Status(models.TextChoices):
        BETTING = "BETTING", "Betting"
        IN_PROGRESS = "IN_PROGRESS", "In Progress"
        COMPLETED = "COMPLETED", "Completed"

    class Outcome(models.TextChoices):
        WIN = "WIN", "Win"
        LOSE = "LOSE", "Lose"
        PUSH = "PUSH", "Push"
        BLACKJACK = "BLACKJACK", "Blackjack (Natural)"

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        help_text="UUID primary key prevents enumeration attacks on the game endpoint.",
    )
    table = models.ForeignKey(
        Table,
        on_delete=models.PROTECT,  # PROTECT: never delete a table that has games
        related_name="games",
    )
    player = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="games",
    )
    deck_state = models.JSONField(
        default=list,
        help_text="Ordered list of remaining cards. Mutated on every draw.",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.IN_PROGRESS,
        db_index=True,
    )
    player_bet = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    outcome = models.CharField(
        max_length=20,
        choices=Outcome.choices,
        null=True,
        blank=True,
    )
    payout = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Net P&L applied to player balance on completion. Negative means a loss.",
    )
    side_bets = models.JSONField(
        default=dict,
        blank=True,
        help_text=(
            "Side bets resolved immediately after the initial deal. "
            "Keys: 'perfect_pairs', 'twenty_one_three'. "
            "Each value: {bet, outcome, net_payout}."
        ),
    )
    left_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Set when the player explicitly leaves a mid-hand game. Implies LOSE outcome.",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Game"
        verbose_name_plural = "Games"
        ordering = ["-created_at"]
        indexes = [
            # Used by _check_no_active_game() on every game creation
            models.Index(
                fields=["player", "status"],
                name="game_player_status_idx",
            ),
        ]

    def __str__(self) -> str:
        return (
            f"Game {str(self.id)[:8]}… — {self.player.username} "
            f"@ {self.table} [{self.status}]"
        )


# ─── Hand ─────────────────────────────────────────────────────────────────────


class Hand(models.Model):
    """
    Represents one hand being played at the table within a Game.

    Each Game has exactly:
        • 1 PLAYER hand
        • 1 DEALER hand
        • N BOT hands  (where N = table.bot_count)

    ``cards`` is a JSON list of card strings appended to on each draw.
    Example: ``["AH", "KC"]`` — Ace of Hearts, King of Clubs.

    Card string format: ``{rank}{suit}``
        Ranks: A 2 3 4 5 6 7 8 9 T J Q K
        Suits: H D C S (Hearts Diamonds Clubs Spades)

    ``is_soft`` tracks whether an Ace in the hand is currently counted as 11.
    This is required to implement the dealer's "stand on hard 17, hit on soft 17"
    rule correctly.
    """

    class HandType(models.TextChoices):
        PLAYER = "PLAYER", "Player"
        DEALER = "DEALER", "Dealer"
        BOT = "BOT", "Bot"

    class HandStatus(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        STAND = "STAND", "Stand"
        BUST = "BUST", "Bust"
        BLACKJACK = "BLACKJACK", "Blackjack"

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    game = models.ForeignKey(
        Game,
        on_delete=models.CASCADE,
        related_name="hands",
    )
    hand_type = models.CharField(
        max_length=10,
        choices=HandType.choices,
        db_index=True,
    )
    bot_index = models.IntegerField(
        null=True,
        blank=True,
        help_text="0-indexed seat position. Set only for BOT hands; null for PLAYER and DEALER.",
    )
    cards = models.JSONField(
        default=list,
        help_text='List of card strings, e.g. ["AH", "KC", "7D"].',
    )
    status = models.CharField(
        max_length=20,
        choices=HandStatus.choices,
        default=HandStatus.ACTIVE,
    )
    is_soft = models.BooleanField(
        default=False,
        help_text="True when an Ace in the hand is counted as 11.",
    )

    class Meta:
        verbose_name = "Hand"
        verbose_name_plural = "Hands"
        ordering = ["hand_type", "bot_index"]

    def __str__(self) -> str:
        label = (
            self.hand_type
            if self.hand_type != Hand.HandType.BOT
            else f"BOT-{self.bot_index}"
        )
        return f"{label} in Game {str(self.game_id)[:8]}…: {self.cards} [{self.status}]"


# ─── GameEvent ────────────────────────────────────────────────────────────────


class GameEvent(models.Model):
    """
    Immutable audit log for every action taken within a Game.

    GameEvents serve two purposes:
        1. Debugging & integrity checks during development.
        2. A replay-able history you can present in your university demo to
           prove the game logic executed correctly step by step.

    ``payload`` is a free-form JSON snapshot captured at the moment of the
    event. Its structure varies by ``event_type`` but always includes the
    relevant hand state.
    """

    class EventType(models.TextChoices):
        GAME_START = "GAME_START", "Game Start"
        DEAL = "DEAL", "Deal"
        PLAYER_HIT = "PLAYER_HIT", "Player Hit"
        PLAYER_STAND = "PLAYER_STAND", "Player Stand"
        PLAYER_DOUBLE = "PLAYER_DOUBLE", "Player Double Down"
        PLAYER_BUST = "PLAYER_BUST", "Player Bust"
        BOT_ACTION = "BOT_ACTION", "Bot Action"
        DEALER_PLAY = "DEALER_PLAY", "Dealer Play"
        GAME_RESOLVE = "GAME_RESOLVE", "Game Resolve"

    # BigAutoField for high-volume append-only writes
    id = models.BigAutoField(primary_key=True)
    game = models.ForeignKey(
        Game,
        on_delete=models.CASCADE,
        related_name="events",
    )
    event_type = models.CharField(
        max_length=20,
        choices=EventType.choices,
        db_index=True,
    )
    payload = models.JSONField(default=dict)
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "Game Event"
        verbose_name_plural = "Game Events"
        ordering = ["timestamp"]

    def __str__(self) -> str:
        return f"[{self.event_type}] Game {str(self.game_id)[:8]}… @ {self.timestamp:%Y-%m-%d %H:%M:%S}"