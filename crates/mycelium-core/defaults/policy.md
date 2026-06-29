# Mycelium Self-Healing Policy

## Scope

The self-healing daemon repairs broken hash chains in the `entries` table.
Only `hash` and `prev_hash` columns may be modified. All other columns are
immutable.

## Constraints

1. **No deletions.** The LLM agent never deletes entries. There is no delete
   tool available.
2. **Hash integrity.** Every repair must produce valid truncated SHA-256
   hashes (16 hex characters) that satisfy the chain invariant:
   `hash = SHA256(prev_hash || entry_data)[..8]`.
3. **Entry count invariant.** The total number of entries in the `entries`
   table must never decrease.
4. **Atomic repairs.** Each repair operation is applied atomically. If any
   step in a repair fails, the entire repair is rolled back to the snapshot
   taken before the repair began.
5. **Audit trail.** Every repair action is logged with the agent's reasoning,
   the old hash, the new hash, and a timestamp.
6. **Safety first.** Always snapshot before mutation; verify after; roll back
   on failure.
