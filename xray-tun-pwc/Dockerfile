# syntax=docker/dockerfile:1
# Build with the xray-tun-connect sources as a named context:
#   docker buildx build --build-context xray-tun-connect=<path> -t xray-tun-pwc .

FROM --platform=$BUILDPLATFORM golang:1.27.2-trixie AS tunnel-binary
ARG TARGETOS TARGETARCH
WORKDIR /src
COPY --from=xray-tun-connect . .
RUN --mount=type=cache,target=/go/pkg/mod --mount=type=cache,target=/root/.cache/go-build \
    CGO_ENABLED=0 GOOS=$TARGETOS GOARCH=$TARGETARCH \
    go build -trimpath -ldflags "-s -w" -o /out/xray-tun-connect ./cmd/xray-tun-connect

FROM python:3.14.8-slim-trixie
COPY --from=astral/uv:0.12.24 /uv /usr/local/bin/uv
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/playwright \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1
WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project
RUN playwright install --with-deps --only-shell chromium && rm -rf /var/lib/apt/lists/*

COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev
COPY --from=tunnel-binary /out/xray-tun-connect /usr/local/bin/xray-tun-connect

RUN useradd --create-home --uid 1000 checker
USER checker
EXPOSE 8000
ENTRYPOINT ["xray-tun-pwc"]
