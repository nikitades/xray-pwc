# The version is the repository's VERSION; the root Makefile passes the exact
# one (a dev build adds -dev.<hash>).
VERSION ?= $(shell cat ../VERSION)
LDFLAGS = -s -w -X main.version=$(VERSION)

.PHONY: build install test vet

build:
	CGO_ENABLED=0 go build -trimpath -ldflags "$(LDFLAGS)" -o xray-pwc-connect ./cmd/xray-pwc-connect

install:
	CGO_ENABLED=0 go install -trimpath -ldflags "$(LDFLAGS)" ./cmd/xray-pwc-connect

test:
	go test -race ./...

vet:
	go vet ./...
