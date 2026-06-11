# Mycelium Hermes Plugin

Automatic mycelium integration for Hermes Agent. No agent memory required.

## What it does

- **on_session_start** — runs mycelium precheck + loads evolution patches automatically on every new session
- **`/mycelium`** — slash command for status, precheck, patches, resume, evolution

## Install

```bash
cd ~/Documents/mycelium && bash plugin/install.sh
```

Restart Hermes or run `/reload` to activate.

## Usage

```
/mycelium              — help
/mycelium status       — brain stats + evolution dashboard
/mycelium precheck     — run health gate
/mycelium patches      — show active evolution patches
/mycelium resume       — smart session resume
/mycelium evolution    — evolution engine status
```

## Uninstall

```bash
rm ~/.hermes/plugins/mycelium
```
