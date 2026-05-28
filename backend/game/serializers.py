"""
game/serializers.py
===================
DRF serializers for the Blackjack application.

Responsibilities are split into two clear categories:

INPUT serializers  — validate & clean data coming IN from the client.
    RegisterSerializer      POST /api/v1/auth/register/
    CreateGameSerializer    POST /api/v1/games/

OUTPUT serializers — shape data going OUT to the client.
    UserProfileSerializer   GET  /api/v1/auth/me/
    TableSerializer         GET  /api/v1/tables/
    LeaderboardEntrySerializer  (used by both REST and SSE)

Game state responses (hit, stand, double-down, game detail) are returned
as raw dicts produced by GameService._build_response(). Those dicts have
a stable, documented structure (see services.py) and don't need a Model
serializer — passing them directly to DRF's Response() avoids a redundant
re-serialization pass and keeps GameService as the single source of truth
for the response shape.
"""

from decimal import Decimal
from typing import Any

from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

from .models import Game, Hand, Table, UserProfile


# ─── INPUT SERIALIZERS ─────────────────────────────────────────────────────────


class RegisterSerializer(serializers.Serializer):
    """
    Validates new-user registration input and creates the User + UserProfile.

    ``validate_password`` runs Django's built-in password validators
    (min length, common password check, etc.) so the strength rules are
    configured once in settings.AUTH_PASSWORD_VALIDATORS.
    """

    username = serializers.CharField(
        min_length=3,
        max_length=150,
        help_text="3–150 characters. Letters, digits, and @/./+/-/_ only.",
    )
    email = serializers.EmailField(required=False, allow_blank=True, default="")
    password = serializers.CharField(
        write_only=True,
        min_length=8,
        style={"input_type": "password"},
    )
    password_confirm = serializers.CharField(
        write_only=True,
        style={"input_type": "password"},
    )

    def validate_username(self, value: str) -> str:
        if User.objects.filter(username__iexact=value).exists():
            raise serializers.ValidationError(
                "This username is already taken. Please choose another."
            )
        return value

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if attrs["password"] != attrs["password_confirm"]:
            raise serializers.ValidationError(
                {"password_confirm": "Passwords do not match."}
            )
        # Run Django's AUTH_PASSWORD_VALIDATORS
        try:
            validate_password(attrs["password"])
        except Exception as exc:
            raise serializers.ValidationError({"password": list(exc.messages)})
        return attrs

    def create(self, validated_data: dict[str, Any]) -> User:
        """
        Create User + UserProfile atomically.
        The UserProfile is also auto-created by the post_save signal in models.py,
        but get_or_create in GameService._get_or_create_profile() is the safety net.
        """
        user = User.objects.create_user(
            username=validated_data["username"],
            email=validated_data.get("email", ""),
            password=validated_data["password"],
        )
        return user


class CreateGameSerializer(serializers.Serializer):
    """
    Validates the payload for starting a new game.

    The bet is validated for type/range here. Domain-level constraints
    (min_bet, max_bet, unlock_balance) are enforced by GameService, which
    has access to the Table record and the player's current balance.

    ``bot_count`` is optional — if omitted the table's default is used.
    Allows the frontend seat-selection UI to override how many bots
    actually join (up to table.bot_count maximum).
    """

    table_id = serializers.IntegerField(min_value=1)
    bet = serializers.DecimalField(
        max_digits=10,
        decimal_places=2,
        min_value=Decimal("0.01"),
        help_text="Bet amount in chips. Must be within the table's min/max bet.",
    )
    bot_count = serializers.IntegerField(
        min_value=0,
        required=False,
        allow_null=True,
        help_text="Override bot count (0 ≤ n ≤ table.bot_count). Omit to use table default.",
    )
    perfect_pairs_bet = serializers.DecimalField(
        max_digits=10,
        decimal_places=2,
        min_value=Decimal("1.00"),
        required=False,
        allow_null=True,
        help_text="Optional Perfect Pairs side bet amount.",
    )
    twenty_one_three_bet = serializers.DecimalField(
        max_digits=10,
        decimal_places=2,
        min_value=Decimal("1.00"),
        required=False,
        allow_null=True,
        help_text="Optional 21+3 side bet amount.",
    )
    fresh_shoe = serializers.BooleanField(
        required=False,
        default=False,
        help_text="If True, reset the shoe to a fresh 312-card deck before dealing.",
    )


# ─── OUTPUT SERIALIZERS ────────────────────────────────────────────────────────


class UserProfileSerializer(serializers.ModelSerializer):
    """
    Full profile response for the /auth/me/ endpoint.
    Flattens the User + UserProfile into a single object for the client.
    """

    username = serializers.CharField(source="user.username", read_only=True)
    email = serializers.EmailField(source="user.email", read_only=True)
    user_id = serializers.IntegerField(source="user.id", read_only=True)

    class Meta:
        model = UserProfile
        fields = ["user_id", "username", "email", "balance", "created_at"]
        read_only_fields = fields


class TableSerializer(serializers.ModelSerializer):
    """
    Lobby table list entry.

    Adds an ``is_locked`` computed field so the frontend can render a
    lock badge without implementing economy logic itself. The request
    context is required to check the current player's balance.
    """

    is_locked = serializers.SerializerMethodField(
        help_text="True when the current player's balance is below unlock_balance.",
    )

    class Meta:
        model = Table
        fields = [
            "id",
            "level",
            "bot_count",
            "min_bet",
            "max_bet",
            "unlock_balance",
            "is_locked",
        ]
        read_only_fields = fields

    def get_is_locked(self, obj: Table) -> bool:
        request = self.context.get("request")
        if not request or not request.user or not request.user.is_authenticated:
            return True
        try:
            return request.user.profile.balance < obj.unlock_balance
        except UserProfile.DoesNotExist:
            # Profile missing — treat as locked until it's created
            return True


class LeaderboardEntrySerializer(serializers.Serializer):
    """
    Single entry in the Top-5 leaderboard.
    Used by both the REST GET endpoint (initial load) and the SSE stream.

    Note: ``balance`` is a string (Decimal serialized by GameService) —
    we keep it as a string here for consistency with the SSE JSON payload
    so the frontend has a single parsing path.
    """

    rank = serializers.IntegerField(read_only=True)
    username = serializers.CharField(read_only=True)
    balance = serializers.CharField(
        read_only=True,
        help_text="Decimal string, e.g. '12345.00'",
    )


# ─── INLINE MODEL SERIALIZERS (used inside GameSerializer) ────────────────────


class HandSerializer(serializers.ModelSerializer):
    """
    Read-only hand snapshot.  Used in GameDetailSerializer for the
    GET /api/v1/games/{id}/ endpoint.
    Note: The game action responses (hit/stand/double-down) return the
    raw dict from GameService directly and do NOT use this serializer.
    """

    value = serializers.SerializerMethodField()

    class Meta:
        model = Hand
        fields = [
            "id",
            "hand_type",
            "bot_index",
            "cards",
            "value",
            "status",
            "is_soft",
        ]
        read_only_fields = fields

    def get_value(self, obj: Hand) -> int:
        from .services import calculate_hand_value
        total, _ = calculate_hand_value(obj.cards)
        return total


class GameDetailSerializer(serializers.ModelSerializer):
    """
    Full game snapshot for GET /api/v1/games/{id}/.

    This serializer reconstructs the same shape as GameService._build_response()
    so the frontend has a single, consistent response schema whether it polls
    the detail endpoint or receives a response from an action endpoint.

    The dealer's hole card IS included here (this endpoint is for reviewing
    completed games or reconnecting to an in-progress game where the client
    needs to restore state). The view layer controls access via permissions.
    """

    table_level = serializers.IntegerField(source="table.level", read_only=True)
    table_bot_count = serializers.IntegerField(source="table.bot_count", read_only=True)
    table_min_bet = serializers.DecimalField(
        source="table.min_bet", max_digits=10, decimal_places=2, read_only=True
    )
    table_max_bet = serializers.DecimalField(
        source="table.max_bet", max_digits=10, decimal_places=2, read_only=True
    )
    hands = HandSerializer(many=True, read_only=True)

    class Meta:
        model = Game
        fields = [
            "id",
            "status",
            "player_bet",
            "outcome",
            "payout",
            "created_at",
            "updated_at",
            # Flattened table fields
            "table_level",
            "table_bot_count",
            "table_min_bet",
            "table_max_bet",
            # Nested hands
            "hands",
        ]
        read_only_fields = fields