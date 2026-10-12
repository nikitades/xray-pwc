# syntax=docker/dockerfile:1
# The probe container and what Ansible delivers into it, from one file.
# Targets (named contexts: --build-context pwc=../xray-tun-pwc --build-context xtc=../xray-tun-connect):
#   base     (default) the image, rebuilt rarely: ghcr.io/nikitades/xray-pwc-aio
#   release  the code: the checker's venv and xray-tun-connect, as files (--output type=tar)
#   alloy, caddy   one binary each, as files, versioned apart from the code
ARG BASE_IMAGE=ghcr.io/linuxserver/baseimage-ubuntu:noble-e8a527a2-ls87
ARG GO_IMAGE=golang:1.27.2-trixie
ARG UV_IMAGE=astral/uv:0.12.24
ARG ALLOY_IMAGE=grafana/alloy:v1.20.0
ARG CADDY_IMAGE=caddy:2.11.7-alpine

FROM ${UV_IMAGE} AS uv

FROM --platform=$BUILDPLATFORM ${GO_IMAGE} AS go
ARG TARGETOS TARGETARCH
ARG WIREGUARD_GO_VERSION=0.0.20250522
ENV CGO_ENABLED=0 GOOS=$TARGETOS GOARCH=$TARGETARCH GOFLAGS=-trimpath
RUN --mount=type=cache,target=/go/pkg/mod --mount=type=cache,target=/root/.cache/go-build \
    git -c advice.detachedHead=false clone -q --depth 1 -b ${WIREGUARD_GO_VERSION} https://git.zx2c4.com/wireguard-go /src/wireguard-go \
    && cd /src/wireguard-go && go build -ldflags "-s -w" -o /out/wireguard-go .
COPY aio-log /src/aio-log
RUN --mount=type=cache,target=/root/.cache/go-build cd /src/aio-log && go build -ldflags "-s -w" -o /out/aio-log .
COPY --from=xtc . /src/xtc
RUN --mount=type=cache,target=/go/pkg/mod --mount=type=cache,target=/root/.cache/go-build \
    cd /src/xtc && go build -ldflags "-s -w" -o /out/xray-tun-connect ./cmd/xray-tun-connect

# The interpreter at its final path: the image and the release share it.
FROM ${BASE_IMAGE} AS python
ARG PYTHON_VERSION=3.14.8
ENV UV_PYTHON_INSTALL_DIR=/opt/python UV_PYTHON_PREFERENCE=only-managed
COPY --from=uv /uv /usr/local/bin/uv
RUN uv python install ${PYTHON_VERSION} \
    && ln -s "$(uv python find ${PYTHON_VERSION})" /usr/local/bin/python3

FROM python AS base
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    S6_BEHAVIOUR_IF_STAGE2_FAILS=2 S6_CMD_WAIT_FOR_SERVICES_MAXTIME=0
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
       wireguard-tools iproute2 iptables openssh-server cron logrotate zstd procps ca-certificates \
    && rm -rf /var/lib/apt/lists/* /etc/ssh/ssh_host_*
# Chromium and its system libraries for the Playwright of the checker's lock
# file, so the first start downloads nothing. A release with another
# Playwright gets its browser from Ansible, into /app/opt/ms-playwright.
RUN --mount=type=bind,from=pwc,source=uv.lock,target=/tmp/uv.lock \
    version=$(python3 -c 'import tomllib; print(next(p["version"] for p in tomllib.load(open("/tmp/uv.lock", "rb"))["package"] if p["name"] == "playwright"))') \
    && uv venv -q /tmp/pw && uv pip install -q --python /tmp/pw "playwright==${version}" \
    && /tmp/pw/bin/playwright install --with-deps --only-shell chromium \
    && echo "${version}" > /etc/aio-playwright-version \
    && rm -rf /tmp/pw /var/lib/apt/lists/* && chmod -R a+rX /ms-playwright
COPY --from=go /out/wireguard-go /out/aio-log /out/xray-tun-connect /usr/local/bin/
COPY rootfs/ /
# Ansible compares it with xray-pwc-aio/VERSION and warns about an old image.
COPY VERSION /etc/aio-version
VOLUME /app

# The code lands at its final path, so the venv needs no relocation.
FROM python AS release-build
ARG RELEASE
ENV UV_PROJECT_ENVIRONMENT=/app/releases/${RELEASE}/venv UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
RUN --mount=type=bind,from=pwc,target=/src/pwc,rw \
    --mount=type=cache,target=/root/.cache/uv \
    cd /src/pwc && uv sync -q --frozen --no-dev --no-editable
COPY --from=go /out/xray-tun-connect /app/releases/${RELEASE}/bin/

FROM scratch AS release
ARG RELEASE
COPY --from=release-build /app/releases/${RELEASE} /

FROM ${ALLOY_IMAGE} AS alloy-image
FROM scratch AS alloy
COPY --from=alloy-image /bin/alloy /alloy

FROM ${CADDY_IMAGE} AS caddy-image
FROM scratch AS caddy
COPY --from=caddy-image /usr/bin/caddy /caddy

FROM base
