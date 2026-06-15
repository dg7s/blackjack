"""
game/views.py
=============
DRF API views and the SSE leaderboard streaming view.

Sections
--------
1.  Helpers           — shared error handler, game-state helper
2.  Auth views        — register, login, logout, me
3.  Lobby views       — table list
4.  Game action views — create, detail, hit, stand, double-down
5.  Leaderboard REST  — one-shot GET for initial load
6.  SSE view          — long-lived streaming leaderboard

SSE architecture notes
----------------------
The EventSource API in browsers cannot send custom headers, so the auth
token is accepted as a ``?token=<key>`` query parameter on the SSE
endpoint.  The view validates it manually against DRF's Token model before
opening the stream.

The streaming generator polls a short-lived cache key
``leaderboard_dirty_{table_id}`` every POLL_INTERVAL seconds.
GameService._mark_leaderboard_dirty() sets this key whenever a balance
changes.  The generator re-queries the DB only when the flag is set,
then immediately clears it — so a burst of concurrent game resolutions
produces at most one extra DB query per poll cycle.

Heartbeat comments (lines starting with `:`) keep the TCP connection alive
through proxies and load-balancers when there is no data to push.

Gunicorn worker requirement
---------------------------
time.sleep() inside a generator blocks a sync worker.  Configure Gunicorn
with either:
    --worker-class gevent      (async, best for many SSE clients)
    --worker-class gthread     (threaded, simpler, fine for small deployments)
See Procfile / render.yaml in the project root.
"""

import json
import logging
import time
from typing import Generator

from django.contrib.auth import authenticate
from django.contrib.auth.models import User
from django.core.cache import cache
from django.http import HttpRequest, HttpResponse, StreamingHttpResponse
from django.views import View
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.exceptions import NotFound
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Game, Table, UserProfile
from .serializers import (
    CreateGameSerializer,
    GameDetailSerializer,
    LeaderboardEntrySerializer,
    RegisterSerializer,
    TableSerializer,
    UserProfileSerializer,
)
from .services import (
    ActiveGameExistsError,
    GameService,
    GameServiceError,
    InvalidBetError,
    InvalidGameActionError,
    TableAccessDeniedError,
)

logger = logging.getLogger(__name__)

# How often (seconds) the SSE generator wakes up to check the dirty flag.
SSE_POLL_INTERVAL: int = 5

# SSE heartbeat — a comment line keeps TCP alive through proxies.
SSE_HEARTBEAT: str = ": heartbeat\n\n"


# ══════════════════════════════════════════════════════════════════════════════
# 1. HELPERS
# ══════════════════════════════════════════════════════════════════════════════


def _service_error_response(exc: GameServiceError) -> Response:
    """
    Map GameService domain exceptions to the appropriate HTTP status code.

    All GameService errors carry a human-readable message that is safe to
    forward directly to the client — they never leak stack traces or internal
    implementation details.
    """
    STATUS_MAP: dict[type, int] = {
        TableAccessDeniedError: status.HTTP_403_FORBIDDEN,
        ActiveGameExistsError: status.HTTP_409_CONFLICT,
        InvalidBetError: status.HTTP_400_BAD_REQUEST,
        InvalidGameActionError: status.HTTP_400_BAD_REQUEST,
        GameServiceError: status.HTTP_400_BAD_REQUEST,
    }
    # Walk the MRO so subclasses match before the base class
    http_status = next(
        (s for exc_type, s in STATUS_MAP.items() if isinstance(exc, exc_type)),
        status.HTTP_400_BAD_REQUEST,
    )
    return Response({"error": str(exc)}, status=http_status)


def _get_active_game_or_404(game_id: str, user: User) -> Game:
    """
    Fetch a Game belonging to ``user`` or raise DRF's NotFound.
    Used by the detail + action views to DRY up the lookup.
    """
    try:
        return (
            Game.objects
            .select_related("table", "player__profile")
            .prefetch_related("hands")
            .get(id=game_id, player=user)
        )
    except (Game.DoesNotExist, ValueError):
        raise NotFound("Game not found or you do not have access to it.")


# ══════════════════════════════════════════════════════════════════════════════
# 2. AUTH VIEWS
# ══════════════════════════════════════════════════════════════════════════════


class RegisterView(APIView):
    """
    POST /api/v1/auth/register/

    Create a new user account and return the auth token immediately so the
    client is logged in straight after registration.

    Request body:
        {
            "username": "alice",
            "email":    "alice@example.com",  // optional
            "password": "StrongPass1!",
            "password_confirm": "StrongPass1!"
        }

    Response 201:
        {
            "token": "9944b09199c62bcf9418ad846...",
            "user": { "user_id": 1, "username": "alice", "balance": "1000.00", ... }
        }
    """

    permission_classes = [AllowAny]

    def post(self, request: Request) -> Response:
        serializer = RegisterSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        user: User = serializer.save()
        token, _ = Token.objects.get_or_create(user=user)

        profile_serializer = UserProfileSerializer(user.profile)
        return Response(
            {"token": token.key, "user": profile_serializer.data},
            status=status.HTTP_201_CREATED,
        )


class LoginView(APIView):
    """
    POST /api/v1/auth/login/

    Authenticate with username + password and receive a DRF auth token.
    The token must be included in subsequent requests as:
        Authorization: Token <key>

    Request body:
        { "username": "alice", "password": "StrongPass1!" }

    Response 200:
        {
            "token": "9944b09199c62bcf9418ad846...",
            "user": { ... }
        }
    """

    permission_classes = [AllowAny]

    def post(self, request: Request) -> Response:
        username: str = request.data.get("username", "")
        password: str = request.data.get("password", "")

        if not username or not password:
            return Response(
                {"error": "Both 'username' and 'password' are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user = authenticate(request=request._request, username=username, password=password)
        if user is None:
            return Response(
                {"error": "Invalid username or password."},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        if not user.is_active:
            return Response(
                {"error": "This account has been disabled."},
                status=status.HTTP_403_FORBIDDEN,
            )

        token, _ = Token.objects.get_or_create(user=user)
        profile = GameService._get_or_create_profile(user)
        profile_serializer = UserProfileSerializer(profile)

        return Response(
            {"token": token.key, "user": profile_serializer.data},
            status=status.HTTP_200_OK,
        )


class LogoutView(APIView):
    """
    POST /api/v1/auth/logout/

    Invalidates the current auth token server-side. The client should
    discard the token from its local storage on receiving a 204 response.

    No request body required — the token is identified from the
    Authorization header.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request: Request) -> Response:
        try:
            request.user.auth_token.delete()
        except Token.DoesNotExist:
            pass  # Already logged out — idempotent
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeView(RetrieveAPIView):
    """
    GET /api/v1/auth/me/

    Returns the current user's profile including their live balance.
    The frontend calls this on app mount and after each game resolution
    to keep the displayed balance in sync.

    Response 200:
        {
            "user_id": 1,
            "username": "alice",
            "email":    "alice@example.com",
            "balance":  "1250.00",
            "created_at": "2024-01-15T10:30:00Z"
        }
    """

    permission_classes = [IsAuthenticated]
    serializer_class = UserProfileSerializer

    def get_object(self) -> UserProfile:
        return GameService._get_or_create_profile(self.request.user)


# ══════════════════════════════════════════════════════════════════════════════
# 3. LOBBY VIEWS
# ══════════════════════════════════════════════════════════════════════════════


class TableListView(ListAPIView):
    """
    GET /api/v1/tables/

    Returns all available tables ordered by level. Each entry includes an
    ``is_locked`` field computed against the requesting player's balance,
    so the lobby can render lock badges without any client-side logic.

    Response 200:
        [
            {
                "id": 1, "level": 0, "bot_count": 0,
                "min_bet": "10.00", "max_bet": "100.00",
                "unlock_balance": "0.00", "is_locked": false
            },
            { "id": 2, "level": 1, ..., "is_locked": true },
            ...
        ]
    """

    permission_classes = [IsAuthenticated]
    serializer_class = TableSerializer
    queryset = Table.objects.all().order_by("level")

    def get_serializer_context(self) -> dict:
        # Pass request so TableSerializer.get_is_locked() can read the balance.
        context = super().get_serializer_context()
        context["request"] = self.request
        return context


# ══════════════════════════════════════════════════════════════════════════════
# 4. GAME ACTION VIEWS
# ══════════════════════════════════════════════════════════════════════════════


class GameCreateView(APIView):
    """
    POST /api/v1/games/

    Start a new Blackjack game. Delegates entirely to GameService.create_game()
    which handles validation, deck init, dealing, and balance escrow.

    Request body:
        { "table_id": 1, "bet": "50.00" }

    Response 201:  Full game state dict (see GameService._build_response())
    Response 400:  Invalid bet / insufficient balance
    Response 403:  Table locked (balance below unlock_balance)
    Response 409:  Active game already in progress
    """

    permission_classes = [IsAuthenticated]

    def post(self, request: Request) -> Response:
        serializer = CreateGameSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        try:
            game_state = GameService.create_game(
                user=request.user,
                table_id=serializer.validated_data["table_id"],
                bet=serializer.validated_data["bet"],
                bot_count=serializer.validated_data.get("bot_count"),
                perfect_pairs_bet=serializer.validated_data.get("perfect_pairs_bet"),
                twenty_one_three_bet=serializer.validated_data.get("twenty_one_three_bet"),
                fresh_shoe=serializer.validated_data.get("fresh_shoe", False),
            )
        except GameServiceError as exc:
            return _service_error_response(exc)

        return Response(game_state, status=status.HTTP_201_CREATED)


class GameDetailView(APIView):
    """
    GET /api/v1/games/{game_id}/

    Returns the full snapshot of a game.  Used to:
        • Restore an in-progress game on page refresh.
        • Review a completed game's outcome.

    The dealer's hole card IS revealed for completed games (the serializer
    returns all cards).  For in-progress games, the response shape mirrors
    the action endpoints — only the dealer's first card is visible (hole
    card index 1 is omitted).

    Response 200: GameDetailSerializer output
    Response 404: Game not found / not owned by requesting user
    """

    permission_classes = [IsAuthenticated]

    def get(self, request: Request, game_id: str) -> Response:
        game = _get_active_game_or_404(game_id, request.user)
        # For in-progress games, re-use GameService to get the masked response;
        # for completed games, the serializer exposes everything.
        if game.status == Game.Status.IN_PROGRESS:
            # Reconstruct the live masked state via the service
            try:
                game, table, player_hand, dealer_hand, bot_hands = (
                    GameService._load_game_state(game_id, request.user)
                )
            except InvalidGameActionError as exc:
                return _service_error_response(exc)
            state = GameService._build_response(
                game, table, player_hand, dealer_hand, bot_hands, reveal_dealer=False
            )
            return Response(state)

        serializer = GameDetailSerializer(game)
        return Response(serializer.data)


class PlayerHitView(APIView):
    """
    POST /api/v1/games/{game_id}/hit/

    Draw one card for the player.  If the player busts, the game is
    resolved immediately and the response will have status=COMPLETED.

    No request body required.

    Response 200: Updated game state dict
    Response 400: Action not valid in current state
    Response 404: Game not found
    """

    permission_classes = [IsAuthenticated]

    def post(self, request: Request, game_id: str) -> Response:
        try:
            game_state = GameService.player_hit(
                game_id=game_id,
                user=request.user,
            )
        except GameServiceError as exc:
            return _service_error_response(exc)

        return Response(game_state, status=status.HTTP_200_OK)


class PlayerStandView(APIView):
    """
    POST /api/v1/games/{game_id}/stand/

    Player elects to stand. Triggers full round resolution:
        bots play → dealer plays → outcomes compared → balances updated.

    The response is the COMPLETE final game state, including all bot hands,
    the fully revealed dealer hand, the outcome, and the net payout.
    The frontend should animate the resolution sequence using the card
    arrays returned in this single response.

    No request body required.

    Response 200: Fully resolved game state dict (status=COMPLETED)
    Response 400: Action not valid in current state
    Response 404: Game not found
    """

    permission_classes = [IsAuthenticated]

    def post(self, request: Request, game_id: str) -> Response:
        try:
            game_state = GameService.player_stand(
                game_id=game_id,
                user=request.user,
            )
        except GameServiceError as exc:
            return _service_error_response(exc)

        return Response(game_state, status=status.HTTP_200_OK)


class PlayerDoubleDownView(APIView):
    """
    POST /api/v1/games/{game_id}/double-down/

    Double the bet, receive exactly one more card, then stand automatically.
    Only valid on the initial 2-card hand.

    No request body required.

    Response 200: Fully resolved game state dict (status=COMPLETED)
    Response 400: Not a 2-card hand, insufficient balance, or invalid state
    Response 404: Game not found
    """

    permission_classes = [IsAuthenticated]

    def post(self, request: Request, game_id: str) -> Response:
        try:
            game_state = GameService.player_double_down(
                game_id=game_id,
                user=request.user,
            )
        except GameServiceError as exc:
            return _service_error_response(exc)

        return Response(game_state, status=status.HTTP_200_OK)


class PlayerLeaveView(APIView):
    """
    POST /api/v1/games/{game_id}/leave/

    Force-complete the player's active game as a LOSE when they leave
    mid-hand. The bet was already escrowed at game creation, so no
    additional balance deduction occurs.

    No request body required.

    Response 200: Completed game state dict (status=COMPLETED, outcome=LOSE)
    Response 400: Game is not IN_PROGRESS
    Response 404: Game not found
    """

    permission_classes = [IsAuthenticated]

    def post(self, request: Request, game_id: str) -> Response:
        try:
            game_state = GameService.leave_game(
                game_id=str(game_id),
                user=request.user,
            )
        except GameServiceError as exc:
            return _service_error_response(exc)

        return Response(game_state, status=status.HTTP_200_OK)


# ══════════════════════════════════════════════════════════════════════════════
# 5. LEADERBOARD REST VIEW  (initial load / fallback)
# ══════════════════════════════════════════════════════════════════════════════


class LeaderboardView(APIView):
    """
    GET /api/v1/leaderboard/{table_id}/

    Returns the current Top-5 players by balance who have played at least
    one completed game at the specified table.

    This endpoint is called ONCE when the React component mounts to populate
    the leaderboard before the SSE stream connects.  Subsequent updates come
    from the SSE stream.

    Response 200:
        {
            "table_id": 1,
            "leaderboard": [
                { "rank": 1, "username": "alice", "balance": "3500.00" },
                { "rank": 2, "username": "bob",   "balance": "2100.00" },
                ...
            ]
        }
    Response 404: Table does not exist
    """

    permission_classes = [IsAuthenticated]

    def get(self, request: Request, table_id: int) -> Response:
        if not Table.objects.filter(id=table_id).exists():
            return Response(
                {"error": f"Table with id={table_id} does not exist."},
                status=status.HTTP_404_NOT_FOUND,
            )

        leaderboard = GameService.get_leaderboard(table_id)
        serializer = LeaderboardEntrySerializer(leaderboard, many=True)

        return Response(
            {"table_id": table_id, "leaderboard": serializer.data},
            status=status.HTTP_200_OK,
        )


# ══════════════════════════════════════════════════════════════════════════════
# 6. SSE LEADERBOARD VIEW
# ══════════════════════════════════════════════════════════════════════════════


def _leaderboard_event_stream(table_id: int) -> Generator[str, None, None]:
    """
    Server-Sent Events generator for the leaderboard of ``table_id``.

    Protocol
    --------
    SSE uses plain text over HTTP with this wire format:

        data: <json string>\\n\\n       ← data event (client's onmessage fires)
        : <any text>\\n\\n              ← comment / heartbeat (client ignores)

    Each chunk MUST end with two newlines (``\\n\\n``).

    Flow
    ----
    1. Yield the current leaderboard immediately (avoids blank screen on connect).
    2. Enter a polling loop sleeping SSE_POLL_INTERVAL seconds per iteration.
    3. On each wake-up, check two cache flags set by GameService:
       • ``reshuffle_event_{table_id}`` — emit a reshuffle notification first.
       • ``leaderboard_dirty_{table_id}`` — re-query DB and push updated rankings.
       • Neither set → yield a heartbeat comment to keep the connection alive.
    4. GeneratorExit is raised when the client disconnects (browser closes tab,
       navigates away, etc.). We catch it to log the disconnect cleanly.

    Cache keys (TTL = 30 s, set in services.py):
        ``leaderboard_dirty_{table_id}``
        ``reshuffle_event_{table_id}``
    """
    leaderboard_key: str = f"leaderboard_dirty_{table_id}"
    reshuffle_key: str   = f"reshuffle_event_{table_id}"

    def _build_data_event(leaderboard: list[dict]) -> str:
        payload = json.dumps({"type": "leaderboard", "table_id": table_id, "data": leaderboard})
        return f"data: {payload}\n\n"

    try:
        # ── Initial push ──────────────────────────────────────────────────────
        leaderboard = GameService.get_leaderboard(table_id)
        yield _build_data_event(leaderboard)
        logger.debug("SSE: initial push for table_id=%s (%d entries)", table_id, len(leaderboard))

        # ── Polling loop ──────────────────────────────────────────────────────
        while True:
            time.sleep(SSE_POLL_INTERVAL)

            yielded = False

            # Reshuffle event takes priority — emit before any leaderboard push
            if cache.get(reshuffle_key):
                cache.delete(reshuffle_key)
                payload = json.dumps({"type": "reshuffle", "table_id": table_id})
                yield f"data: {payload}\n\n"
                logger.debug("SSE: reshuffle event push for table_id=%s", table_id)
                yielded = True

            if cache.get(leaderboard_key):
                cache.delete(leaderboard_key)
                leaderboard = GameService.get_leaderboard(table_id)
                yield _build_data_event(leaderboard)
                logger.debug("SSE: leaderboard push (dirty flag) for table_id=%s", table_id)
                yielded = True

            if not yielded:
                yield SSE_HEARTBEAT

    except GeneratorExit:
        logger.info("SSE: client disconnected from table_id=%s stream", table_id)


class SSELeaderboardView(View):
    """
    GET /sse/leaderboard/{table_id}/?token=<auth_token>

    Long-lived Server-Sent Events endpoint that streams leaderboard updates
    for a specific table.

    Authentication
    --------------
    The browser's EventSource API does NOT support custom request headers,
    so the DRF token is passed as a query parameter:
        GET /sse/leaderboard/1/?token=9944b09199c62bcf...

    Required response headers
    -------------------------
    Content-Type:      text/event-stream
    Cache-Control:     no-cache
    X-Accel-Buffering: no    ← disables Nginx response buffering
    Connection:        keep-alive
    """

    def get(self, request: HttpRequest, table_id: int) -> HttpResponse:
        # ── Authenticate ──────────────────────────────────────────────────────
        token_key: str = request.GET.get("token", "")
        if not token_key:
            return HttpResponse(
                "Authentication token required as ?token= query parameter.",
                status=401,
                content_type="text/plain",
            )

        try:
            token = Token.objects.select_related("user").get(key=token_key)
        except Token.DoesNotExist:
            return HttpResponse(
                "Invalid or expired authentication token.",
                status=401,
                content_type="text/plain",
            )

        user: User = token.user
        if not user.is_active:
            return HttpResponse(
                "This user account is disabled.",
                status=403,
                content_type="text/plain",
            )

        # ── Validate table ────────────────────────────────────────────────────
        if not Table.objects.filter(id=table_id).exists():
            return HttpResponse(
                f"Table with id={table_id} does not exist.",
                status=404,
                content_type="text/plain",
            )

        logger.info(
            "SSE: user=%s connected to leaderboard stream for table_id=%s",
            user.username,
            table_id,
        )

        # ── Build the streaming response ──────────────────────────────────────
        response = StreamingHttpResponse(
            streaming_content=_leaderboard_event_stream(table_id),
            content_type="text/event-stream",
        )
        response["Cache-Control"] = "no-cache"
        response["X-Accel-Buffering"] = "no"      # Critical for Nginx on Render.com
        response["Connection"] = "keep-alive"

        return response