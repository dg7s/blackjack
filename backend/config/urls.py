"""
config/urls.py
==============
Root URL dispatcher for the Blackjack project.

Mount strategy
--------------
    /api/v1/   ← all REST endpoints  (game.urls.api_urlpatterns)
    /sse/      ← SSE leaderboard     (game.urls.sse_urlpatterns)
    /admin/    ← Django admin panel
    /*         ← catch-all → serves React SPA index.html
"""

from pathlib import Path

from django.conf import settings
from django.contrib import admin
from django.http import FileResponse, HttpResponse
from django.urls import include, path, re_path

from game.urls import api_urlpatterns, sse_urlpatterns


def spa_index(request, **kwargs):
    """Serve the React SPA for any route not matched by the API or admin."""
    index_path = Path(settings.STATIC_ROOT) / "index.html"
    if index_path.exists():
        return FileResponse(index_path.open("rb"), content_type="text/html")
    return HttpResponse(
        "<h1>Frontend not built.</h1>"
        "<p>Run <code>cd frontend && npm run build</code> then "
        "<code>python manage.py collectstatic</code>.</p>",
        status=404,
    )


urlpatterns = [
    # Django admin
    path("admin/", admin.site.urls),

    # REST API  →  /api/v1/auth/..., /api/v1/tables/, /api/v1/games/..., etc.
    path("api/v1/", include((api_urlpatterns, "game"), namespace="game")),

    # SSE stream  →  /sse/leaderboard/{table_id}/?token=...
    path("sse/", include((sse_urlpatterns, "sse"), namespace="sse")),

    # Catch-all: serve the React SPA index.html for all other routes.
    re_path(r"^.*$", spa_index),
]