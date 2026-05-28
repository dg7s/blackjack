# Procfile
# ─────────────────────────────────────────────────────────────────────────────
# Gunicorn with gthread workers — no gevent/C-extension needed.
#
# --worker-class gthread
#     Each worker uses OS threads. One thread per concurrent request,
#     including long-lived SSE connections. Fully compatible with Python 3.13.
#
# --workers 2
#     2 worker processes. Rule of thumb: 2 × CPU cores.
#
# --threads 8
#     8 threads per worker → 16 total concurrent connections (REST + SSE).
#     Tune upward if you expect many simultaneous leaderboard subscribers.
#
# --timeout 0
#     Disable worker kill-timeout. Required for SSE: without this, Gunicorn
#     kills any thread that hasn't returned a response within 30 s, which
#     would terminate every SSE stream after 30 seconds.
#
# --bind 0.0.0.0:$PORT
#     Render injects $PORT dynamically.

web: cd backend && gunicorn config.wsgi:application --worker-class gthread --workers 2 --threads 8 --timeout 0 --bind 0.0.0.0:$PORT
