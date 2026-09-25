# Security policy

## Reporting a vulnerability

Use this repository's **Security → Advisories → Report a vulnerability** option
when GitHub private vulnerability reporting is available. See the repository's
[Security page](https://github.com/cepeter/SillyTavern-Telegram-Bridge/security).
Do not include bot tokens, provider credentials, private character cards, chat
transcripts, database files, or working exploit details in a public issue.

When a private reporting option is unavailable, open a public issue requesting a
private disclosure channel **without sensitive details**, then wait for a
maintainer to identify that channel. This policy does not invent a security email
address or imply that private reporting has been enabled by this commit. There
is no guaranteed response-time commitment or bounty program.

A useful private report includes the affected commit or release, platform and
Python version, the required configuration, a minimal reproduction using dummy
data, expected versus observed behavior, and the potential impact. Redact logs
before sharing them. Do not test against another person's bot, endpoint or data.

## Supported development line

This is a **preproduction** project. Security fixes are developed on `main` and
released after review. Older release tags do not have a standing backport or
long-term-support commitment. A historical tag is an immutable release record,
not an assurance that it contains current fixes. Review the changelog and locked
dependency changes before updating; keep recoverable backups of private data.

## Operational boundaries

The bridge is intended to run as a private bot under a dedicated, unprivileged
user account. Configure the Telegram user allowlist explicitly. Protect the
private environment file and native SillyTavern data with appropriate ownership
and permissions; POSIX file-mode validation is not a Windows ACL implementation.
Run only software and native configuration you trust under the same account.

Provider catalog URLs do not authorize credential egress. Keep endpoint and
private-network grants explicit and minimal. The hardened urllib provider client
uses an independently configured endpoint policy, validated DNS addresses and
verified TLS. Third-party SDKs have their own transports; do not infer identical
transport guarantees for every integration or arbitrary external program.

Self-update requires an SSH-signed annotated release tag authorized by an
independently provisioned public allowed-signers file outside both updated trees.
Never use a private signing key as that file or trust keys fetched from the same
release they would authorize. Do not rewrite old tags to bypass verification.
The updater refuses changed runtime dependency locks for automatic installation;
use the reviewed manual installation procedure in that case.

Source-checkout advancement, live-mirror activation and service restart are not
one filesystem-wide atomic transaction. Inspect explicit partial-update or
restart-required outcomes before continuing. A restart request is not proof of
process health. Previous mirrors are retained for recovery; do not delete them
until the deployment has been verified.

Tests, coverage, static checks, package auditing and CodeQL are defense-in-depth
controls, not a guarantee that the project is vulnerability-free. Report suspected
boundary failures even when the current checks pass.
