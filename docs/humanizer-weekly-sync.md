# Weekly Humanizer Prompt Sync — Specification

Status: **Spec only** — no code shipped. Author: cepeter. Related: PR #159 (Humanizer
response style), `bridge/humanize.py`.

## 1. Purpose

Keep the bridge's Humanizer behavior aligned with upstream
[`blader/humanizer`](https://github.com/blader/humanizer) as its AI-tell pattern list
evolves, without requiring a human to edit code weekly.

This spec covers **only the reference tier**. The active prompt remains a curated,
human-reviewed artifact. Full automation of the active prompt is an explicit non-goal
(§8).

## 2. Two-tier model

| Tier | What it is | How it updates |
|---|---|---|
| **Reference** | Raw upstream `SKILL.md` + MIT license + provenance | Weekly, automatic, atomic |
| **Active prompt** | Compact `HUMANIZER_SYSTEM_PROMPT` the model reads | Human-reviewed, via normal release |

The reference exists so a human has an auditable, versioned, latest upstream copy on
hand when they choose to update the active prompt. It is never sent to the model.

## 3. Weekly sync flow

On the weekly trigger, the sync worker:

1. Resolve the **pinned upstream tag** (never `main`) — e.g. `v3.0.0`.
2. Fetch `SKILL.md` from `raw.githubusercontent.com` for that tag.
3. Verify **SHA-256** against an expected value, and enforce a **64 KB size cap**.
4. Validate structure — the file must contain the expected section markers
   (`## A.`, `### 1.`, `## How to work`). Reject otherwise.
5. Write to a temp file, then atomically `rename` into place.
6. Record provenance (commit, version, fetched-at, sha256).
7. If the upstream version changed, emit a Telegram notice with the diff link.

## 4. Storage layout

```text
$SILLYTAVERN_BRIDGE_HOME/humanizer/
  reference.md          # raw SKILL.md, with MIT header retained
  reference.README      # upstream source URL + license notice
  provenance.json       # { "commit": ..., "version": ..., "fetched_at": ..., "sha256": ... }
```

The shipped `HUMANIZER_SYSTEM_PROMPT` in `bridge/humanize.py` remains the source of
truth for what is actually sent to the model. The files above are inputs to a human
decision, not runtime dependencies.

## 5. Trigger mechanism

**Recommended: systemd user timer**, mirroring the existing self-update restart pattern
(`bridge/self_update.py::_schedule_user_service_restart`).

```text
~/.config/systemd/user/humanizer-sync.service   # oneshot, runs the sync script
~/.config/systemd/user/humanizer-sync.timer     # OnCalendar=weekly, Persistent=true
```

- Runs **outside** the bridge cgroup, so a fetch hang or crash cannot take down the
  bridge service (`sillytavern-telegram.service`).
- `Persistent=true` means a missed window (machine was off) runs once on next boot.
- Alternative (rejected for now): an internal recurring job via `job_service`.
  Rejected because a blocking fetch would occupy a worker and is not isolated from
  the bridge process.

## 6. Network trust boundary

The fetch must pass through `bridge/network_security.py` (`EndpointPolicy`,
`strict_urlopen`) — the same trust boundary the self-update path already uses for
GitHub.

- `raw.githubusercontent.com` must be allowed on the appropriate allowlist before the
  sync can run. It is **not** implicitly allowed today.
- HTTPS only; reject redirects to any host outside the allowlist.

## 7. Failure modes

| Failure | Behavior |
|---|---|
| Network / timeout | Keep last-good reference; no notice spam (rate-limited) |
| SHA-256 mismatch | Reject; keep last-good; emit a warning |
| Size > 64 KB | Reject; keep last-good |
| Missing section markers | Reject; keep last-good (upstream restructured) |
| Tag not found | Keep last-good; mark provenance stale |

Every failure is **fail-open** for the bridge: generation continues unchanged. The
sync can never block or break a user turn.

## 8. Explicit non-goals

- ❌ Auto-rewrite the active prompt (prompt-injection surface; non-deterministic
  derivation; risks flattening roleplay voice).
- ❌ Per-response fetch of upstream (latency, cost, network dependency per turn).
- ❌ A runtime library dependency on `blader/humanizer`.
- ❌ Following unpinned `main`.

If full automation is ever wanted, it requires three additional safeguards: pin +
verify, a deterministic template extracting only the §pattern list, and a
**shadow + `/humanize-promote`** gate so a change is never silently applied.

## 9. Provenance & visibility

- `provenance.json` records the exact commit/version a reference came from.
- Expose the active reference version in `/status` (read-only) so a user can see what
  upstream snapshot is available versus what the active prompt shipped.

## 10. Open decisions before implementation

1. Pin strategy: fixed tag per release, or a "latest tag" discovery that still verifies
   hash? (Fixed tag is safer; latest-tag needs its own verification.)
2. Diff surface: link to GitHub compare, or store a local `reference.diff`?
3. Notification channel: reuse the `/update` panel, or a dedicated one-way notice?
4. Who may run `/humanize-promote` in the full-auto variant (if ever adopted)?
