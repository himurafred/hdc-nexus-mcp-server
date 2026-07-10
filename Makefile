#------------------------------------------------------------------------------
# Variables
#------------------------------------------------------------------------------
PHONY :=

REGISTRY_HOST ?= 10.244.20.62:8081/local-docker-repository
IMAGE_NAME    ?= hdc-nexus-mcp-server

VERSION ?= $(shell v=$$(git describe --long --tags 2>/dev/null | sed 's/\([^-]*-g\)/r\1/;s/-/./g'); echo "$${v:-dev}")

IMAGE_FULL   ?= ${REGISTRY_HOST}/${IMAGE_NAME}
IMAGE_LATEST ?= ${IMAGE_FULL}:latest
IMAGE_TAG    ?= ${IMAGE_FULL}:${VERSION}

CONTAINER_ENGINE ?= $(shell if command -v podman >/dev/null 2>&1; then echo podman; else echo docker; fi)

PORT ?= 8000

#------------------------------------------------------------------------------
# Image targets
#------------------------------------------------------------------------------
PHONY+=image/build
image/build:
	$(CONTAINER_ENGINE) build \
		--tag ${IMAGE_LATEST} \
		--tag ${IMAGE_TAG} \
		--label "org.opencontainers.image.version=${VERSION}" \
		--label "org.opencontainers.image.source=https://github.com/himurafred/hdc-nexus-mcp-server" \
		.

PHONY+=image/push
image/push:
	$(CONTAINER_ENGINE) push ${IMAGE_LATEST}
	$(CONTAINER_ENGINE) push ${IMAGE_TAG}

PHONY+=image/release
image/release: image/build image/push

PHONY+=image/clean
image/clean:
	-$(CONTAINER_ENGINE) rmi ${IMAGE_LATEST}
	-$(CONTAINER_ENGINE) rmi ${IMAGE_TAG}

#------------------------------------------------------------------------------
# Dev targets
#------------------------------------------------------------------------------
PHONY+=dev/install
dev/install:
	pip install -r requirements.txt

PHONY+=dev/run
dev/run:
	uvicorn app.main:app --host 0.0.0.0 --port $(PORT) --reload

PHONY+=test
test:
	python -m pytest tests/ -v

.PHONY: ${PHONY}
