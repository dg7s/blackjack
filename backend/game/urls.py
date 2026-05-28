"""
game/urls.py
============
URL routing for the ``game`` Django app.

This file is included by the project-level ``config/urls.py``.

Wiring (add to config/urls.py)
-------------------------------
    from django.urls import path, include

    urlpatterns = [
        path("admin/",          admin.site.urls),
        path("api/v1/",         include("game.urls")),   # REST endpoints
        path("sse/",            include("game.urls")),   # SSE endpoint (same file)
    ]

    # ↑ Both prefixes land in this file. The SSE endpoint is defined with its
    #   own full path below so it sits at /sse/leaderboard/{id}/ regardless.
    #   Alternatively you can split into two include() calls pointing to two
    #   separate url files — either approach works fine.

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

# ── Combined urlpatterns ───────────────────────────────────────────────────────
# config/urls.py does:
#     path("api/v1/", include(("game.urls", "game"), namespace="game"))
#     path("sse/",    include(("game.sse_urls", ...)))
#
# For simplicity with a single app, we expose both sets and let config/urls.py
# pick them:
#
#     from game.urls import api_urlpatterns, sse_urlpatterns
#     urlpatterns = [
#         path("api/v1/", include(api_urlpatterns)),
#         path("sse/",    include(sse_urlpatterns)),
#     ]

api_urlpatterns = auth_patterns + lobby_patterns + game_patterns + leaderboard_patterns
sse_urlpatterns = sse_patterns

# Default urlpatterns (used if config/urls.py does a plain include("game.urls"))
urlpatterns = api_urlpatterns + [
    # SSE sits at /sse/leaderboard/... even when included via api/v1/ prefix.
    # Override in config/urls.py if you want a cleaner mount point.
    path("sse/leaderboard/<int:table_id>/", SSELeaderboardView.as_view(), name="sse-leaderboard"),
]