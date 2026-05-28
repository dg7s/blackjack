"""
config/urls.py
==============
Root URL dispatcher for the Blackjack project.

Mount strategy
--------------
    /api/v1/   ← all REST endpoints  (game.urls.api_urlpatterns)
    /sse/      ← SSE leaderboard     (game.urls.sse_urlpatterns)
    /admin/    ← Django admin panel

The separation of /api/v1/ and /sse/ prefixes makes it trivial to put
the SSE endpoint on a different Gunicorn worker class or process in
production if needed (e.g. gevent workers only for /sse/).
"""

from django.contrib import admin
from django.urls import include, path

from game.urls import api_urlpatterns, sse_urlpatterns

urlpatterns = [
    # Django admin
    path("admin/", admin.site.urls),

    # REST API  →  /api/v1/auth/..., /api/v1/tables/, /api/v1/games/..., etc.
    path("api/v1/", include((api_urlpatterns, "game"), namespace="game")),

    # SSE stream  →  /sse/leaderboard/{table_id}/?token=...
    path("sse/", include((sse_urlpatterns, "sse"), namespace="sse")),
]