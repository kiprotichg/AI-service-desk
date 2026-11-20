# Gunicorn reads this file automatically when started as `gunicorn run:app`
# from the backend folder (as on Render), so no Start Command change is needed.
#
# The defaults (one "sync" worker, killed after 30 s) served one person at a time
# and killed answers that took longer than 30 s, which the chat showed as
# "The answer was cut off" (Render logs: "WORKER TIMEOUT").
import os

worker_class = "gthread"   # threads: several people can be answered at once
workers = int(os.getenv("WEB_CONCURRENCY", "1"))
threads = int(os.getenv("GUNICORN_THREADS", "8"))
timeout = 120              # an ERP answer (lookups + AI) can take a while on a slow link
graceful_timeout = 30      # on redeploy, let answers in progress finish
keepalive = 5
