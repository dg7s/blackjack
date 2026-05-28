"""
game/admin.py
=============
Django Admin configuration for the Blackjack application.

Design goals:
    • Inline UserProfile directly inside the User admin so operators
      can see and adjust balances without navigating to a separate page.
    • Table admin is deliberately simple and editable — operators need
      to easily create/tune levels and their bet limits.
    • Game admin is read-heavy with rich inline displays of Hands and
      Events for debugging sessions and marking disputes.
    • Color-coded balance column gives instant visual feedback on
      player financial health.
"""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User
from django.utils.html import format_html

from .models import Game, GameEvent, Hand, Table, UserProfile


# ─── UserProfile inline ───────────────────────────────────────────────────────


class UserProfileInline(admin.StackedInline):
    """
    Embeds the UserProfile fields directly inside the built-in User change page.
    Operators can adjust a player's balance from a single screen.
    """

    model = UserProfile
    can_delete = False  # Profile should always exist if the User exists
    verbose_name_plural = "Game Profile"
    fields = ("balance", "created_at")
    readonly_fields = ("created_at",)


# ─── Extended User Admin ──────────────────────────────────────────────────────


class ExtendedUserAdmin(BaseUserAdmin):
    """
    Replaces the default User admin with the Profile inline attached.
    Adds a color-coded balance column to the user list view.
    """

    inlines = (UserProfileInline,)
    list_display = (
        "username",
        "email",
        "get_balance_display",
        "is_active",
        "is_staff",
        "date_joined",
    )
    list_filter = BaseUserAdmin.list_filter + ("profile__balance",)  # type: ignore[operator]

    @admin.display(description="Balance", ordering="profile__balance")
    def get_balance_display(self, obj: User) -> str:  # type: ignore[override]
        try:
            balance = obj.profile.balance
        except UserProfile.DoesNotExist:
            return format_html('<span style="color: grey;">No profile</span>')

        color = "green" if balance >= 0 else "crimson"
        return format_html(
            '<strong style="color: {};">{}</strong>',
            color,
            f"{balance:,.2f}",
        )


# Unregister the default User admin and replace with our extended version.
admin.site.unregister(User)
admin.site.register(User, ExtendedUserAdmin)


# ─── UserProfile Admin ────────────────────────────────────────────────────────


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "get_balance_colored", "created_at")
    list_filter = ("created_at",)
    search_fields = ("user__username", "user__email")
    readonly_fields = ("created_at",)
    ordering = ("-balance",)
    list_per_page = 50

    @admin.display(description="Balance", ordering="balance")
    def get_balance_colored(self, obj: UserProfile) -> str:
        color = "green" if obj.balance >= 0 else "crimson"
        return format_html(
            '<strong style="color: {};">{}</strong>',
            color,
            f"{obj.balance:,.2f}",
        )


# ─── Table Admin ──────────────────────────────────────────────────────────────


@admin.register(Table)
class TableAdmin(admin.ModelAdmin):
    """
    Tables are the core configuration object that operators tune to adjust
    game economy. All fields are editable directly in the list view for
    fast adjustments without opening a detail page.
    """

    list_display = (
        "level",
        "bot_count",
        "min_bet",
        "max_bet",
        "unlock_balance",
        "get_game_count",
    )
    list_editable = ("min_bet", "max_bet", "unlock_balance")
    ordering = ("level",)
    list_per_page = 20

    @admin.display(description="Total Games Played")
    def get_game_count(self, obj: Table) -> int:
        return obj.games.filter(status=Game.Status.COMPLETED).count()


# ─── Inlines for Game Admin ───────────────────────────────────────────────────


class HandInline(admin.TabularInline):
    """
    Shows all hands for a game in a compact table. Read-only to
    preserve the integrity of historical game data.
    """

    model = Hand
    extra = 0
    can_delete = False
    show_change_link = False
    readonly_fields = ("id", "hand_type", "bot_index", "cards", "status", "is_soft")
    fields = readonly_fields
    ordering = ("hand_type", "bot_index")


class GameEventInline(admin.TabularInline):
    """
    Chronological audit log displayed below the game detail.
    Useful for replaying a hand step-by-step when investigating disputes.
    """

    model = GameEvent
    extra = 0
    can_delete = False
    show_change_link = False
    readonly_fields = ("event_type", "payload", "timestamp")
    fields = readonly_fields
    ordering = ("timestamp",)
    max_num = 50  # Safety cap to avoid enormous inlines on long games


# ─── Game Admin ───────────────────────────────────────────────────────────────


@admin.register(Game)
class GameAdmin(admin.ModelAdmin):
    """
    Primary debugging surface for game integrity issues.

    The Hand and GameEvent inlines let operators inspect the full
    card history of any game without touching the database directly.
    """

    list_display = (
        "short_id",
        "player",
        "table",
        "status",
        "player_bet",
        "get_outcome_badge",
        "payout",
        "created_at",
    )
    list_filter = ("status", "outcome", "table", "created_at")
    search_fields = ("player__username", "id")
    readonly_fields = (
        "id",
        "player",
        "table",
        "deck_state",
        "player_bet",
        "outcome",
        "payout",
        "created_at",
        "updated_at",
    )
    inlines = [HandInline, GameEventInline]
    ordering = ("-created_at",)
    list_per_page = 30
    date_hierarchy = "created_at"

    # Allow changing status and payout only (for dispute resolution)
    fields = (
        "id",
        "player",
        "table",
        "status",
        "player_bet",
        "outcome",
        "payout",
        "deck_state",
        "created_at",
        "updated_at",
    )

    def get_queryset(self, request):  # type: ignore[override]
        return (
            super()
            .get_queryset(request)
            .select_related("player", "table")
        )

    @admin.display(description="Game ID")
    def short_id(self, obj: Game) -> str:
        return str(obj.id)[:8] + "…"

    @admin.display(description="Outcome")
    def get_outcome_badge(self, obj: Game) -> str:
        if not obj.outcome:
            return format_html('<span style="color: grey;">—</span>')

        COLOR_MAP = {
            Game.Outcome.WIN: "green",
            Game.Outcome.BLACKJACK: "goldenrod",
            Game.Outcome.PUSH: "steelblue",
            Game.Outcome.LOSE: "crimson",
        }
        color = COLOR_MAP.get(obj.outcome, "grey")
        return format_html(
            '<strong style="color: {};">{}</strong>',
            color,
            obj.get_outcome_display(),
        )


# ─── Hand Admin (standalone, for filtering by type or status) ─────────────────


@admin.register(Hand)
class HandAdmin(admin.ModelAdmin):
    list_display = ("id", "get_game_link", "hand_type", "bot_index", "cards", "status", "is_soft")
    list_filter = ("hand_type", "status")
    search_fields = ("game__player__username", "game__id")
    readonly_fields = ("id",)
    list_per_page = 50

    def get_queryset(self, request):  # type: ignore[override]
        return super().get_queryset(request).select_related("game__player")

    @admin.display(description="Game")
    def get_game_link(self, obj: Hand) -> str:
        return str(obj.game_id)[:8] + "…"


# ─── GameEvent Admin (standalone, for audit searches) ─────────────────────────


@admin.register(GameEvent)
class GameEventAdmin(admin.ModelAdmin):
    list_display = ("id", "get_game_link", "event_type", "timestamp")
    list_filter = ("event_type", "timestamp")
    search_fields = ("game__player__username", "game__id")
    readonly_fields = ("timestamp",)
    ordering = ("-timestamp",)
    list_per_page = 50
    date_hierarchy = "timestamp"

    def get_queryset(self, request):  # type: ignore[override]
        return super().get_queryset(request).select_related("game__player")

    @admin.display(description="Game")
    def get_game_link(self, obj: GameEvent) -> str:
        return str(obj.game_id)[:8] + "…"