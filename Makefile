SHELL := /bin/bash
ROOT := $(CURDIR)

.PHONY: install-daemon deploy-daemon status-daemon verify runtime-verify resume patterns

install-daemon:
	bash scripts/install-myceliumd.sh

deploy-daemon:
	bash scripts/deploy-myceliumd.sh

status-daemon:
	bash scripts/status-myceliumd.sh

verify:
	python3 scripts/mycelium.py verify

runtime-verify:
	/usr/bin/python3 $$HOME/.hermes/myceliumd/runtime/scripts/mycelium.py verify

resume:
	bash scripts/mycelium-start

patterns:
	python3 scripts/detect-patterns.py
