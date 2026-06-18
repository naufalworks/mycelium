SHELL := /bin/bash
ROOT := $(CURDIR)

.PHONY: test install-daemon deploy-daemon status-daemon test-daemon-e2e test-daemon-offline verify runtime-verify resume patterns web-backend web-frontend web-build web-test web web-stop web-restart web-status web-open web-install-service web-uninstall-service web-service-status web-logs install-cli

install-daemon:
	bash scripts/install-myceliumd.sh

deploy-daemon:
	bash scripts/deploy-myceliumd.sh

test:
	pytest tests/ -q

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

web-install-service:
	bash scripts/mycelium-web install-service

web-uninstall-service:
	bash scripts/mycelium-web uninstall-service

web-service-status:
	bash scripts/mycelium-web service-status

web-logs:
	bash scripts/mycelium-web logs

install-cli:
	bash scripts/install-mycelium-cli.sh

.PHONY: go-build go-proxy go-mcp go-all

go-build:
	cd go && go build ./...

go-proxy:
	cd go && go build -o mycelium-proxy ./cmd/proxy/

go-mcp:
	cd go && go build -o mycelium-mcp ./cmd/mcp/

go-all: go-build go-proxy go-mcp
	@echo "Go binaries built:"
	ls -la go/mycelium-*

install-proxy:
	cd go && go build -o /usr/local/bin/mycelium-proxy ./cmd/proxy/ && go build -o /usr/local/bin/mycelium-mcp ./cmd/mcp/
	@echo "Installed to /usr/local/bin: mycelium-proxy, mycelium-mcp"

install-hooks:
	bash hooks/install.sh

e2e: test go-all
	@echo "🔄 Run brain E2E tests..."
	go run ./go/cmd/verify/main.go 2>/dev/null || true
	python3 scripts/precheck.py --stats
	python3 scripts/mycelium.py verify

.PHONY: install
install: go-install install-hooks
	@echo "🍄 Mycelium installed! Run 'mycelium status' to verify."

.PHONY: go-install
go-install:
	cd go && go build -o /usr/local/bin/mycelium ./cmd/mycelium/
	cd go && go build -o /usr/local/bin/myceliumd ./cmd/myceliumd/
	cd go && go build -o /usr/local/bin/mycelium-proxy ./cmd/proxy/
	cd go && go build -o /usr/local/bin/mycelium-mcp ./cmd/mcp/
	@echo "✅ Go binaries installed to /usr/local/bin"

.PHONY: uninstall
uninstall:
	rm -f /usr/local/bin/mycelium /usr/local/bin/myceliumd /usr/local/bin/mycelium-proxy /usr/local/bin/mycelium-mcp
	-launchctl unload ~/Library/LaunchAgents/com.naufal.myceliumd.plist 2>/dev/null
	rm -f ~/Library/LaunchAgents/com.naufal.myceliumd.plist
	@echo "🧹 Mycelium uninstalled"

.PHONY: backup
backup:
	mycelium backup

.PHONY: restore
restore:
	@echo "Usage: make restore ARCHIVE=<path>"
	mycelium restore $(ARCHIVE)
