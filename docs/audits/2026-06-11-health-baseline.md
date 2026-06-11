# Mycelium Health Baseline — 2026-06-11

## verify
✅ Integrity chain valid — 145 turns, all hashes match.

## status
🍄 Mycelium — Brain Status
==================================================
  Turns:      145
  Size:       77.6 KB
  Format:     v2 (tiered + hashed)
  Sessions:   19
  Date range: 2026-06-10 → 2026-06-11

  By type:
    finding      4
    decision     30
    idea         12
    talk         98
    gardener     1

  By tier:
    S    32 turns
    A    15 turns
    B    98 turns

  Findings: 4
    critical   1

  Top entities:
    mycelium                  95x
    hermes                    26x
    gh                        25x
    git                       19x
    myceliumd                 17x
    json                      11x
    launchd                   11x
    jsonl                     8x
    sql                       8x
    python                    8x



## web status
mycelium observatory
backend running → pid 40674 · http://127.0.0.1:8421/api/health

## api daemon
{
    "ok": true,
    "running": true,
    "state": {
        "last_assistant_id": 10010,
        "last_verify_hour": "2026-06-11T08",
        "imports": 105
    },
    "state_path": "/Users/azfar.naufal/.hermes/myceliumd/state.json",
    "log_path": "/Users/azfar.naufal/.hermes/myceliumd/myceliumd.log",
    "health_url": "http://127.0.0.1:20151/health"
}

## raw daemon health

## backend tests
.................                                                        [100%]
17 passed in 0.41s

## frontend build + tsc

> mycelium-web-ui@0.1.0 build
> vite build

vite v5.4.21 building for production...
transforming...
✓ 31 modules transformed.
rendering chunks...
computing gzip size...
dist/index.html                   0.44 kB │ gzip:  0.28 kB
dist/assets/index-CM20x3eD.css    8.98 kB │ gzip:  2.62 kB
dist/assets/index-9peZAKlL.js   174.33 kB │ gzip: 53.26 kB
✓ built in 338ms
