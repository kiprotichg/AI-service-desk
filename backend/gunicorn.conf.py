"""Gunicorn settings, read automatically when the backend is started with
`gunicorn run:app` from this folder (as on Render).

Gunicorn's defaults are one "sync" worker that answers one request at a time and is
killed after 30 seconds. Answers stream for several seconds, and an answer that looks
up ERP data needs two AI calls plus the lookup, so a slow moment could cut it off
("The answer was cut off"). Threads let several people be answered at once, and with
threads the timeout only restarts a worker that has stopped responding, not a long answer.
"""
import os

worker_class = "gthread"
threads = int(os.getenv("GUNICORN_THREADS", "8"))
timeout = int(os.getenv("GUNICORN_TIMEOUT", "120"))
graceful_timeout = 30
keepalive = 5
