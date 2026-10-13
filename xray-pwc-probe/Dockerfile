# syntax=docker/dockerfile:1
# The checker alone, for a try on your own machine (the probes run it inside
# xray-pwc-aio). With the xray-pwc-connect sources as a named context, and the
# repository's version, which the checker and xray-pwc-connect must share:
#   docker buildx build --build-context xray-pwc-connect=../xray-pwc-connect \
#     --build-arg VERSION=$(cat ../VERSION) -t xray-pwc-probe --load .

FROM --platform=$BUILDPLATFORM golang:1.27.2-trixie AS tunnel-binary
ARG TARGETOS TARGETARCH VERSION
WORKDIR /src
COPY --from=xray-pwc-connect . .
RUN --mount=type=cache,target=/go/pkg/mod --mount=type=cache,target=/root/.cache/go-build \
    CGO_ENABLED=0 GOOS=$TARGETOS GOARCH=$TARGETARCH \
    go build -trimpath -ldflags "-s -w -X main.version=${VERSION:?--build-arg VERSION=...}" -o /out/xray-pwc-connect ./cmd/xray-pwc-connect

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
COPY --from=tunnel-binary /out/xray-pwc-connect /usr/local/bin/xray-pwc-connect

RUN useradd --create-home --uid 1000 checker
USER checker
EXPOSE 8000
ENTRYPOINT ["xray-pwc-probe"]
