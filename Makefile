# Builds xray-pwc from this checkout, the same way here and in CI.
#
#   make version          the version these sources build as
#   make bundle           the release bundles, dist/<version>/xray-pwc-<version>-linux-<arch>.tar.zst
#   make image            the probe image, loaded into the local Docker
#   make tool TOOL=alloy TOOL_VERSION=v1.20.0 ARCH=arm64   one tool for Ansible
#   make test             every test and linter
#
# A release build (CI, on a tag vX.Y.Z) sets RELEASE=1 and gets VERSION as it
# is. Any other build is a dev build of the next release: VERSION-dev.<hash>,
# the hash of every source that goes into it, so different sources never
# share a version and the same ones always do.

include xray-pwc-aio/deps.env

BASE    := $(shell cat VERSION)
# files PATHS...: the files of these paths that exist and git does not ignore,
# NUL-separated.
files = git ls-files -co --exclude-standard -z -- $(1) | perl -0ne 'chop; print "$$_\0" if -f'
HASH    := $(shell $(call files,VERSION xray-pwc-probe xray-pwc-connect xray-pwc-aio) \
             | perl -0ne 'chop; print "$$_\0"; open my $$f, "<", $$_; local $$/; print <$$f>' \
             | shasum -a 256 | cut -c1-12)
VERSION := $(if $(RELEASE),$(BASE),$(BASE)-dev.$(HASH))
# The same version in Python's spelling (PEP 440).
PY_VERSION := $(if $(RELEASE),$(BASE),$(BASE).dev0+$(HASH))

ARCHES ?= amd64 arm64
ARCH   ?= $(shell docker version --format '{{.Server.Arch}}' 2>/dev/null || echo amd64)
IMAGE  ?= ghcr.io/nikitades/xray-pwc-aio
# Where `make image` puts the image: the local Docker by default, tagged with
# the version. CI pushes each platform by digest, then tags the list.
IMAGE_OUTPUT ?= type=docker
IMAGE_TAGS   ?= --tag $(IMAGE):$(VERSION)
PLATFORMS    ?= linux/$(ARCH)

empty :=
space := $(empty) $(empty)
comma := ,

DIST  := dist/$(VERSION)
TOOLS := dist/tools
CONNECT_TARGETS := linux-amd64 linux-arm64 darwin-amd64 darwin-arm64
CONNECT := $(CONNECT_TARGETS:%=$(DIST)/xray-pwc-connect-%)
WHEEL   := $(DIST)/xray_pwc_probe-$(PY_VERSION)-py3-none-any.whl
REQS    := $(DIST)/requirements.txt
BUNDLES := $(ARCHES:%=$(DIST)/xray-pwc-$(VERSION)-linux-%.tar.zst)

BUILD_ARGS = --build-arg VERSION=$(VERSION) --build-arg MIN_IMAGE=$(MIN_IMAGE) \
	--build-arg ALLOY_VERSION=$(ALLOY_VERSION) --build-arg CADDY_VERSION=$(CADDY_VERSION) \
	--build-context dist=$(DIST)

.PHONY: version check-version connect wheel bundle image tool sums test clean

version:
	@echo $(VERSION)

connect: $(CONNECT)
wheel: $(WHEEL) $(REQS)

$(DIST)/xray-pwc-connect-%:
	@mkdir -p $(DIST)
	cd xray-pwc-connect && CGO_ENABLED=0 GOOS=$(word 1,$(subst -, ,$*)) GOARCH=$(word 2,$(subst -, ,$*)) \
		go build -trimpath -ldflags "-s -w -X main.version=$(VERSION)" -o ../$@ ./cmd/xray-pwc-connect

# The wheel is built from a copy of the sources with the version set, so the
# checkout keeps VERSION as it is.
$(REQS): $(WHEEL)
	@test -f $@
$(WHEEL):
	@rm -rf $(DIST)/src && mkdir -p $(DIST)/src
	$(call files,xray-pwc-probe) | xargs -0 tar -cf - | tar -xf - -C $(DIST)/src
	cd $(DIST)/src/xray-pwc-probe && uv version -q --frozen $(PY_VERSION) \
		&& uv build -q --wheel -o $(CURDIR)/$(DIST) \
		&& uv export -q --frozen --no-dev --no-emit-project -o $(CURDIR)/$(REQS)
	@rm -rf $(DIST)/src

bundle: $(BUNDLES)
	@for f in $(BUNDLES); do echo "bundle: $$f"; done

$(DIST)/xray-pwc-$(VERSION)-linux-%.tar.zst: $(DIST)/xray-pwc-connect-linux-% $(WHEEL) $(REQS)
	docker buildx build --quiet --platform linux/$* --target bundle $(BUILD_ARGS) \
		--output type=tar,dest=- xray-pwc-aio | zstd -q -T0 -o $@.part && mv $@.part $@

image: $(addprefix $(DIST)/xray-pwc-connect-,$(PLATFORMS:linux/%=linux-%)) $(WHEEL) $(REQS)
	docker buildx build --platform $(subst $(space),$(comma),$(strip $(PLATFORMS))) --target image $(BUILD_ARGS) \
		$(IMAGE_TAGS) --output $(IMAGE_OUTPUT) $(IMAGE_FLAGS) xray-pwc-aio


# Alloy, Caddy or Chromium for one Playwright, for Ansible to bring to a probe
# whose image lacks the version a release needs.
TOOL_FILE = $(TOOLS)/$(TOOL)-$(TOOL_VERSION)-linux-$(ARCH).tar.zst
tool:
	@test -n "$(TOOL)" -a -n "$(TOOL_VERSION)" || { echo "TOOL=alloy|caddy|chromium TOOL_VERSION=..."; exit 2; }
	@mkdir -p $(TOOLS)
	@test -f $(TOOL_FILE) || { docker buildx build --quiet --platform linux/$(ARCH) --target $(TOOL) \
		--build-arg $(shell echo $(TOOL) | sed 's/chromium/playwright/' | tr a-z A-Z)_VERSION=$(TOOL_VERSION) \
		--output type=tar,dest=- xray-pwc-aio | zstd -q -T0 -o $(TOOL_FILE).part && mv $(TOOL_FILE).part $(TOOL_FILE); }
	@echo "tool: $(TOOL_FILE)"

# The release's files and their checksums, for the GitHub release.
sums:
	cd $(DIST) && shasum -a 256 xray-pwc-*.tar.zst xray-pwc-connect-* xray_pwc_probe-*.whl > SHA256SUMS

# pyproject.toml keeps the version too, for uv and the tests: the same one.
check-version:
	@test "$$(sed -n 's/^version = "\(.*\)"/\1/p' xray-pwc-probe/pyproject.toml)" = "$(BASE)" \
		|| { echo "xray-pwc-probe/pyproject.toml: the version is not $(BASE), as in VERSION"; exit 1; }

test: check-version
	cd xray-pwc-connect && go vet ./... && go test -race ./...
	cd xray-pwc-aio/aio-log && go vet ./... && go test ./...
	cd xray-pwc-aio/aio-net && GOOS=linux go vet ./...
	cd xray-pwc-probe && uv run --frozen pytest -q && uv run --frozen ruff check . \
		&& uv run --frozen ruff format --check . && uvx ty check src

clean:
	rm -rf dist
