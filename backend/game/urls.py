"""
game/urls.py
============
URL routing for the ``game`` Django app.

This file is included by the project-level ``config/urls.py``.

URL map
-------
    POST   /api/v1/auth/register/               RegisterView
    POST   /api/v1/auth/login/                  LoginView
    POST   /api/v1/auth/logout/                 LogoutView
    GET    /api/v1/auth/me/                     MeView

    GET    /api/v1/tables/                      TableListView

    POST   /api/v1/games/                       GameCreateView
    GET    /api/v1/games/<uuid:game_id>/        GameDetailView
    POST   /api/v1/games/<uuid:game_id>/hit/    PlayerHitView
    POST   /api/v1/games/<uuid:game_id>/stand/  PlayerStandView
    POST   /api/v1/games/<uuid:game_id>/double/ PlayerDoubleDownView

    GET    /api/v1/leaderboard/<int:table_id>/  LeaderboardView

    GET    /sse/leaderboard/<int:table_id>/     SSELeaderboardView
           └── ?token=<auth_token>              (EventSource auth)
"""

from django.urls import path

from .views import (
    GameCreateView,
    GameDetailView,
    LeaderboardView,
    LoginView,
    LogoutView,
    MeView,
    PlayerDoubleDownView,
    PlayerHitView,
    PlayerLeaveView,
    PlayerStandView,
    RegisterView,
    SSELeaderboardView,
    TableListView,
)

# ── Auth ───────────────────────────────────────────────────────────────────────
auth_patterns = [
    path("auth/register/", RegisterView.as_view(),  name="auth-register"),
    path("auth/login/",    LoginView.as_view(),     name="auth-login"),
    path("auth/logout/",   LogoutView.as_view(),    name="auth-logout"),
    path("auth/me/",       MeView.as_view(),        name="auth-me"),
]

# ── Lobby ──────────────────────────────────────────────────────────────────────
lobby_patterns = [
    path("tables/", TableListView.as_view(), name="table-list"),
]

# ── Game actions ───────────────────────────────────────────────────────────────
# <uuid:game_id> validates the UUID format at the routing layer, returning 404
# automatically for malformed IDs — no try/except needed in the views.
game_patterns = [
    path(
        "games/",
        GameCreateView.as_view(),
        name="game-create",
    ),
    path(
        "games/<uuid:game_id>/",
        GameDetailView.as_view(),
        name="game-detail",
    ),
    path(
        "games/<uuid:game_id>/hit/",
        PlayerHitView.as_view(),
        name="game-hit",
    ),
    path(
        "games/<uuid:game_id>/stand/",
        PlayerStandView.as_view(),
        name="game-stand",
    ),
    path(
        "games/<uuid:game_id>/double/",
        PlayerDoubleDownView.as_view(),
        name="game-double",
    ),
    path(
        "games/<uuid:game_id>/leave/",
        PlayerLeaveView.as_view(),
        name="game-leave",
    ),
]

# ── Leaderboard (REST — initial load) ─────────────────────────────────────────
leaderboard_patterns = [
    path(
        "leaderboard/<int:table_id>/",
        LeaderboardView.as_view(),
        name="leaderboard",
    ),
]

# ── SSE (long-lived stream) ────────────────────────────────────────────────────
# Mounted separately under /sse/ in config/urls.py but defined here to keep all
# game-related routing in one file.
sse_patterns = [
    path(
        "leaderboard/<int:table_id>/",
        SSELeaderboardView.as_view(),
        name="sse-leaderboard",
    ),
]

api_urlpatterns = auth_patterns + lobby_patterns + game_patterns + leaderboard_patterns
sse_urlpatterns = sse_patterns