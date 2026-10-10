LDFLAGS = -s -w

.PHONY: build install test vet dist

build:
	CGO_ENABLED=0 go build -ldflags "$(LDFLAGS)" -o xray-tun-connect ./cmd/xray-tun-connect

install:
	CGO_ENABLED=0 go install -ldflags "$(LDFLAGS)" ./cmd/xray-tun-connect

test:
	go test ./...

vet:
	go vet ./...

# Static binaries for the probes (Linux) and local development (macOS).
dist:
	@for target in linux/amd64 linux/arm64 darwin/arm64; do \
		GOOS=$${target%/*} GOARCH=$${target#*/} CGO_ENABLED=0 \
		go build -ldflags "$(LDFLAGS)" -o dist/xray-tun-connect-$${target%/*}-$${target#*/} ./cmd/xray-tun-connect; \
	done
