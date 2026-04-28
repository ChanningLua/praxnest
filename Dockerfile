# praxnest container — Python 3.12 slim base + pip-installed praxnest package.
#
# This builds a deploy-ready image: no dev dependencies, no ai-news /
# WeChat / praxagent integration (those land in v0.2). For now we just
# want a self-contained app server you can `docker-compose up`.

FROM python:3.12-slim AS builder

WORKDIR /build
COPY pyproject.toml ./
COPY README.md ./
COPY src/ ./src/

RUN pip install --no-cache-dir build && \
    python -m build --wheel --outdir /wheels


FROM python:3.12-slim

WORKDIR /app

# Runtime deps only.
COPY --from=builder /wheels/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl && rm /tmp/*.whl

# Data dir is volume-mounted at runtime; create the mount point so
# permission errors don't bite on first start.
RUN mkdir -p /app/data

# 7878 = the default port; can be overridden via command flags.
EXPOSE 7878

ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "praxnest", "serve", "--host", "0.0.0.0", "--port", "7878", "--no-open", "--data-dir", "/app/data"]
