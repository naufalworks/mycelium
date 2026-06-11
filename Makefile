SHELL := /bin/bash
ROOT := $(CURDIR)

.PHONY: test install-daemon deploy-daemon status-daemon test-daemon-e2e test-daemon-offline verify runtime-verify resume patterns web-backend web-frontend web-build web-test web web-stop web-restart web-status web-open web-logs install-cli

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

web-backend:
	bash scripts/start-mycelium-web-backend.sh

web-frontend:
	bash scripts/start-mycelium-web-frontend.sh

web-build:
	bash scripts/build-mycelium-web.sh

web-test:
	pytest web/backend/tests -q

web:
	bash scripts/mycelium-web start

web-stop:
	bash scripts/mycelium-web stop

web-restart:
	bash scripts/mycelium-web restart

web-status:
	bash scripts/mycelium-web status

web-open:
	bash scripts/mycelium-web open

web-logs:
	bash scripts/mycelium-web logs

install-cli:
	bash scripts/install-mycelium-cli.sh
