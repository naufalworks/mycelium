SHELL := /bin/bash
ROOT := $(CURDIR)

.PHONY: test install-daemon deploy-daemon status-daemon test-daemon-e2e test-daemon-offline verify runtime-verify resume patterns

install-daemon:
	bash scripts/install-myceliumd.sh

deploy-daemon:
	bash scripts/deploy-myceliumd.sh

test:
	pytest tests/test_myceliumd.py -q

status-daemon:
	bash scripts/status-myceliumd.sh

test-daemon-e2e:
	bash scripts/test-myceliumd-e2e.sh

test-daemon-offline:
	pytest tests/test_myceliumd.py -q

verify:
	python3 scripts/mycelium.py verify

runtime-verify:
	/usr/bin/python3 $$HOME/.hermes/myceliumd/runtime/scripts/mycelium.py verify

resume:
	bash scripts/mycelium-start

patterns:
	python3 scripts/detect-patterns.py
