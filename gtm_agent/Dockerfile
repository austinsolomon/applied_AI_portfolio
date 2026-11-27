# The harness as a Cloud Run service: FastAPI (server.py) with the Slack route.
#
# The Claude Agent SDK's Python wheel includes the `claude` program it runs as a subprocess, so
# nothing else needs installing. git is added because the harness asks the sandbox for the
# current branch when it builds its prompt.
FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Run as a normal user, not root. The `claude` program writes its settings under $HOME.
RUN useradd --create-home --uid 10001 agent
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=agent:agent . .
USER agent

# HOST: listen on all interfaces so Cloud Run can reach the server.
# SANDBOX=temp-copy: the agent works on a throwaway copy of /app and never changes the image.
# DISABLE_WEB_UI=1: the public URL serves only /slack/events and /healthz.
# PORT is set by Cloud Run (8080 by default).
ENV HOST=0.0.0.0 \
    PORT=8080 \
    SANDBOX=temp-copy \
    DISABLE_WEB_UI=1 \
    PYTHONUNBUFFERED=1

# The folder argument is the project the agent works on: its own code.
CMD ["python", "server.py", "/app"]
