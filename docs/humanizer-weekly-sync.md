# Humanizer reference refresh — implementation specification

**Status: design only.** This repository does not install a refresh script,
service, timer, status panel, promotion command, or scheduled network task.
The active response prompt is the reviewed constant in `bridge/humanize.py`.

## Purpose and trust boundary

A future opt-in administrative task may keep an auditable local copy of the
reviewed `blader/humanizer` reference and its MIT license. The copy is reference
material for maintainers; it must never be imported, executed, sent to the
model, or consulted during generation. Availability or failure of this task
must have no effect on chat, startup, or the active Humanizer prompt.

The reference and active prompt are separate artifacts:

| Artifact | Owner and update mechanism |
|---|---|
| Reviewed reference snapshot | An explicitly enabled administrative fetch of one immutable, verified source revision |
| Active response prompt | An ordinary reviewed source-code PR followed by the normal release process |

## Pinning and approval

A future implementation must require a checked-in, reviewed manifest containing
an immutable upstream commit SHA, the exact `SKILL.md` and `LICENSE` paths,
expected SHA-256 digests for both files, and a human-readable reference label.
No manifest or an incomplete manifest means the task is disabled. Example
versions, hashes, and section headings are not trust anchors.

Do not follow `main`, resolve mutable tags at execution time, discover a latest
release, or download the expected hash beside the file being verified. Updating
the approved commit or either expected digest is a separate maintainer-reviewed
PR. A weekly run refreshes or verifies **the pinned snapshot**; it cannot discover
or approve a new upstream version. This deliberate restriction makes unattended
fetching reproducible without implying that it automatically tracks upstream.

## Network and resource policy

A standalone task must use the existing `bridge.network_security.strict_urlopen`
transport with an explicit, task-specific allowlist. There is no `EndpointPolicy`
class to instantiate. Allow only HTTPS on the approved raw GitHub host and do not
forward provider keys, bot tokens, or other credentials. Existing redirect and
DNS-address checks must remain enabled. Do not change the bridge's normal
provider allowlists to make an administrative fetch work.

Use a finite request timeout, a finite overall task deadline, no unbounded
retries, and read at most 64 KiB plus one byte **per file** before refusing an
oversized response. Require both expected SHA-256 digests before accepting any
content. Optional format checks supplement hashes; they never replace them.
Treat upstream Markdown as untrusted text, even after verifying provenance.

## Coherent publication

Proposed storage, entirely outside runtime prompt configuration:

```text
$SILLYTAVERN_BRIDGE_HOME/humanizer-reference/
  snapshots/<commit>-<manifest-digest>/
    SKILL.md
    LICENSE
    provenance.json
  current.json
```

Build a new, uniquely named staging directory on the same filesystem. Write
both verified files and provenance, flush their contents, and then publish the
complete immutable snapshot directory. Finally atomically replace one small
`current.json` pointer identifying that snapshot. Readers resolve the pointer
once and read all files from that immutable directory. Do not independently
replace a reference file and provenance file: a crash between those writes can
publish a mixed version.

Provenance records the approved source repository, commit, paths, manifest
digest, verified content digests, and successful fetch time. It must not contain
credentials. Keep the last successful snapshot; failed or partial downloads
never move the pointer. Limit retained snapshots and remove abandoned staging
directories without following symlinks or deleting outside this task's root.
Retain the complete upstream license and copyright notice with every snapshot.

## Scheduling, visibility, and failures

When implemented and explicitly enabled, use a dedicated user-systemd oneshot
service and weekly timer, separate from the bridge process and its worker pool.
Missed-run behavior, service timeout, storage quota, and disable/uninstall steps
must be included in that implementation's tests and documentation. Creating
this specification does not authorize or install such a timer.

Default reporting is a bounded administrative log with the approved revision,
verification result, and whether the last successful snapshot remains available.
Deduplicate repeated errors. Do not expose fetched reference content or secrets
in logs and do not spam Telegram. A future status surface must distinguish the
reference revision from the shipped active prompt revision. No such status
surface is included here.

Network errors, unavailable commits, unexpected redirects, oversized content,
hash mismatch, malformed manifests, and write/publication failures all preserve
the last successful reference. They must neither modify the active prompt nor
prevent the bridge from operating. A new approved manifest can record a GitHub
compare link for maintainer review; there is no automatic promotion command.

## Acceptance criteria before an implementation can merge

Tests must cover disabled-by-default operation, immutable manifest resolution,
restricted credential-free transport, bounded reads and deadlines, both digest
checks, failure at each publication step, coherent readers across replacement,
restart cleanup, restricted permissions, retention bounds, and unchanged chat
behavior with the reference task absent or failing. No runtime dependency,
per-response fetch, automatic prompt rewriting, or compatibility facade may be
introduced to implement reference refresh.
