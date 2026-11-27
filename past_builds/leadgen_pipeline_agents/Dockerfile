# One image for the whole pipeline: the Python package from the lockfile plus the Claude Code CLI
# that the Claude Agent SDK drives. Same image on a laptop, in CI and in the cloud.
#
#   docker build -t leadgen .
#   docker run --rm leadgen --version
#   docker run --rm --env-file .env --network host leadgen db migrate
#
# Versions are pinned so a rebuild months later produces the same image. Keep UV_VERSION equal to the
# setup-uv pin in .github/workflows/ci.yml and PYTHON_VERSION equal to .python-version.
ARG PYTHON_VERSION=3.12
ARG UV_VERSION=0.12.1
ARG NODE_VERSION=22.23.3
ARG CLAUDE_CODE_VERSION=2.1.296

# ---------------------------------------------------------------------------------------------
# Stage 1: install the locked dependencies and the package into /app/.venv.
# ---------------------------------------------------------------------------------------------
FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv_binary
FROM python:${PYTHON_VERSION}-slim-bookworm AS builder

COPY --from=uv_binary /uv /uvx /usr/local/bin/

# Use the image's Python instead of downloading one; compile bytecode once at build time.
ENV UV_PYTHON=/usr/local/bin/python3 \
    UV_PYTHON_DOWNLOADS=never \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Dependencies first, so this layer is reused until uv.lock changes.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project

# Then the package itself (README.md is the package's declared readme).
COPY README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

# ---------------------------------------------------------------------------------------------
# Stage 2: the runtime image. Node is copied from the official image (same Debian release), which is
# the only way to install the Claude Code CLI without pulling a toolchain into the final image.
# ---------------------------------------------------------------------------------------------
FROM node:${NODE_VERSION}-bookworm-slim AS node_binary
FROM python:${PYTHON_VERSION}-slim-bookworm AS runtime
ARG CLAUDE_CODE_VERSION

LABEL org.opencontainers.image.title="leadgen" \
      org.opencontainers.image.description="Lead-gen pipeline on the Claude Agent SDK" \
      org.opencontainers.image.source="https://github.com/austinsolomon/applied_AI_portfolio"

# Node runtime for the Claude Code CLI. ripgrep ships inside the CLI package; git is for the CLI's
# repository awareness when it runs inside a checkout.
COPY --from=node_binary /usr/local/bin/node /usr/local/bin/node
COPY --from=node_binary /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/npm
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && npm install -g "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" \
    && npm cache clean --force

# Never run as root: the pipeline only needs to read its own code and talk to the network.
RUN groupadd --system leadgen && useradd --system --gid leadgen --create-home leadgen

WORKDIR /app
COPY --from=builder --chown=leadgen:leadgen /app/.venv /app/.venv
# The category file is read at runtime relative to the working directory (CATEGORY_CONFIG_PATH).
COPY --chown=leadgen:leadgen config ./config

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # The CLI's own telemetry and update checks are pointless inside an immutable image.
    DISABLE_AUTOUPDATER=1 \
    DISABLE_TELEMETRY=1 \
    DISABLE_ERROR_REPORTING=1

USER leadgen

# Streamlit (leadgen dashboard) listens here when that subcommand is used.
EXPOSE 8501

ENTRYPOINT ["leadgen"]
CMD ["--help"]
