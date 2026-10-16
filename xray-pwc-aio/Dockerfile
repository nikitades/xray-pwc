# syntax=docker/dockerfile:1
# The probe image and the release bundle, from one file; the root Makefile
# builds every target. The named context dist is dist/<version>/ of the
# Makefile: the probe's wheel, its locked requirements and xray-pwc-connect.
#   image     (default) the probe container: the runtime, and the bundle of
#             its own version in /app, so a new probe starts with it
#   bundle    one release, as files (--output type=tar): the probe's venv,
#             xray-pwc-connect, the service scripts, release.env
#   alloy, caddy, chromium   one tool each, as files, named by its version,
#             for a release that needs one the probe's image lacks
ARG BASE_IMAGE=ghcr.io/linuxserver/baseimage-ubuntu:noble-e8a527a2-ls87
ARG GO_IMAGE=golang:1.27.2-trixie
ARG UV_IMAGE=astral/uv:0.12.24
ARG ALLOY_VERSION=v1.20.0
ARG CADDY_VERSION=2.11.7

FROM ${UV_IMAGE} AS uv

FROM --platform=$BUILDPLATFORM ${GO_IMAGE} AS go
ARG TARGETOS TARGETARCH
ARG WIREGUARD_GO_VERSION=0.0.20250522
ENV CGO_ENABLED=0 GOOS=$TARGETOS GOARCH=$TARGETARCH GOFLAGS=-trimpath
RUN --mount=type=cache,target=/go/pkg/mod --mount=type=cache,target=/root/.cache/go-build \
    git -c advice.detachedHead=false clone -q --depth 1 -b ${WIREGUARD_GO_VERSION} https://git.zx2c4.com/wireguard-go /src/wireguard-go \
    && cd /src/wireguard-go && go build -ldflags "-s -w" -o /out/wireguard-go .
COPY aio-log /src/aio-log
COPY aio-net /src/aio-net
RUN --mount=type=cache,target=/go/pkg/mod --mount=type=cache,target=/root/.cache/go-build \
    cd /src/aio-log && go build -ldflags "-s -w" -o /out/aio-log . \
    && cd /src/aio-net && go build -ldflags "-s -w" -o /out/aio-net .

# The interpreter at its final path: the image and the bundle share it.
FROM ${BASE_IMAGE} AS python
ARG PYTHON_VERSION=3.14.8
ENV UV_PYTHON_INSTALL_DIR=/opt/python UV_PYTHON_PREFERENCE=only-managed
COPY --from=uv /uv /usr/local/bin/uv
RUN uv python install ${PYTHON_VERSION} \
    && ln -s "$(uv python find ${PYTHON_VERSION})" /usr/local/bin/python3

# The release lands at its final path, so the venv needs no relocation. The
# wheel goes in the standard way, with its locked requirements.
FROM python AS bundle-build
ARG VERSION MIN_IMAGE ALLOY_VERSION CADDY_VERSION TARGETARCH
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
RUN --mount=type=bind,from=dist,target=/dist --mount=type=cache,target=/root/.cache/uv \
    set -eu; release=/app/releases/${VERSION}; \
    uv venv -q "$release/venv" \
    && uv pip install -q --python "$release/venv" --require-hashes -r /dist/requirements.txt \
    && uv pip install -q --python "$release/venv" --no-deps /dist/xray_pwc_probe-*.whl \
    && install -D -m 0755 /dist/xray-pwc-connect-linux-${TARGETARCH} "$release/bin/xray-pwc-connect" \
    && playwright=$("$release/venv/bin/python" -c 'import importlib.metadata as m; print(m.version("playwright"))') \
    && printf '%s\n' "VERSION=${VERSION}" "MIN_IMAGE=${MIN_IMAGE}" "ALLOY_VERSION=${ALLOY_VERSION}" \
       "CADDY_VERSION=${CADDY_VERSION}" "PLAYWRIGHT_VERSION=${playwright}" > "$release/release.env"
COPY --chmod=0755 services/ /app/releases/${VERSION}/services/

FROM scratch AS bundle
ARG VERSION
COPY --from=bundle-build /app/releases/${VERSION} /

FROM grafana/alloy:${ALLOY_VERSION} AS alloy-image
FROM scratch AS alloy
COPY --from=alloy-image /bin/alloy /alloy

FROM caddy:${CADDY_VERSION}-alpine AS caddy-image
FROM scratch AS caddy
COPY --from=caddy-image /usr/bin/caddy /caddy

# Chromium of one Playwright version, for a release whose Playwright the image
# does not have. Its system libraries must already be in the image.
FROM python AS chromium-build
ARG PLAYWRIGHT_VERSION
RUN uv venv -q /tmp/pw && uv pip install -q --python /tmp/pw "playwright==${PLAYWRIGHT_VERSION}" \
    && PLAYWRIGHT_BROWSERS_PATH=/ms-playwright /tmp/pw/bin/playwright install --only-shell chromium \
    && chmod -R a+rX /ms-playwright
FROM scratch AS chromium
COPY --from=chromium-build /ms-playwright /

FROM python AS image
ARG VERSION ALLOY_VERSION CADDY_VERSION
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    S6_BEHAVIOUR_IF_STAGE2_FAILS=2 S6_CMD_WAIT_FOR_SERVICES_MAXTIME=0
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
       wireguard-tools iproute2 libcap2-bin openssh-server cron logrotate zstd procps ca-certificates cowsay \
    && ln -s /usr/games/cowsay /usr/local/bin/cowsay \
    && rm -rf /var/lib/apt/lists/* /etc/ssh/ssh_host_*
# Chromium and its system libraries for the Playwright of the bundle inside,
# so the first start downloads nothing.
RUN --mount=type=bind,from=bundle-build,source=/app/releases/${VERSION}/release.env,target=/tmp/release.env \
    . /tmp/release.env \
    && uv venv -q /tmp/pw && uv pip install -q --python /tmp/pw "playwright==${PLAYWRIGHT_VERSION}" \
    && /tmp/pw/bin/playwright install --with-deps --only-shell chromium \
    && echo "${PLAYWRIGHT_VERSION}" > /etc/aio-playwright-version \
    && rm -rf /tmp/pw /var/lib/apt/lists/* && chmod -R a+rX /ms-playwright
COPY --from=go /out/wireguard-go /out/aio-log /out/aio-net /usr/local/bin/
COPY --from=alloy-image /bin/alloy /opt/alloy-${ALLOY_VERSION}/alloy
COPY --from=caddy-image /usr/bin/caddy /opt/caddy-${CADDY_VERSION}/caddy
COPY rootfs/ /
# Every service runs as abc. The tunnel needs one capability, on the binaries
# that set it up (not ip: it drops capabilities for a user who is not root,
# so aio-net does its part); NET_ADMIN in compose.yml lets them have it. abc
# gets the group id the linuxserver init gives it at start, and a shell for SSH.
RUN setcap cap_net_admin+ep "$(readlink -f "$(command -v wg)")" \
    && setcap cap_net_admin+ep /usr/local/bin/wireguard-go \
    && setcap cap_net_admin+ep /usr/local/bin/aio-net \
    && groupmod -o -g 911 abc && usermod -s /bin/bash abc
COPY --from=bundle-build --chown=911:911 /app/releases/${VERSION} /app/releases/${VERSION}
RUN ln -s releases/${VERSION} /app/current \
    && mkdir -p /app/state /app/logs /app/opt && chown -hR 911:911 /app \
    && echo "${VERSION}" > /etc/aio-version
VOLUME /app
