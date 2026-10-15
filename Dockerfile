# Pin the official multi-platform index; update alongside CI and dependency locks.
FROM python:3.12.12-slim-bookworm@sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/tmp
WORKDIR /app
LABEL org.opencontainers.image.source="https://github.com/sn41lz/agent-systems-tool-loop"
COPY requirements.lock requirements-build.lock ./
RUN python -m pip install --only-binary=:all: --require-hashes -r requirements.lock -r requirements-build.lock
COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m pip install --no-deps --no-build-isolation . \
    && mkdir -p /app/artifacts \
    && chmod 1777 /app/artifacts

FROM base AS runtime
USER 65532:65532
CMD ["python", "-m", "tool_loop", "demo", "--output", "artifacts/demo"]

FROM base AS dev
COPY requirements-dev.lock ./
RUN python -m pip install --only-binary=:all: --require-hashes -r requirements-dev.lock
COPY tests ./tests
USER 65532:65532
CMD ["python", "-m", "pytest", "-p", "no:cacheprovider"]
