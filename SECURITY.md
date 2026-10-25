# Security boundaries

Baton is a local CLI running with the invoking user's filesystem permissions.
It is not a multi-user service or a filesystem sandbox. `CLAUDE_CONFIG_DIR`,
`XDG_DATA_HOME`, and `XDG_STATE_HOME` are trusted operator configuration: choosing
these locations deliberately selects where Baton reads or stores session data.
Do not expose environment settings or `--dir`, `--out`, and `--pack` directly to
untrusted remote callers. A service wrapper must impose its own allowlist.

Session-store contents are untrusted. Project identity is checked before a
handoff is built, and launch prompts distinguish history from current user
authorization. SQLite stores are opened read-only using encoded file URIs.
Private exports require POSIX ownership and permissions; native Windows users
must use WSL. Credential redaction is best effort, not anonymization. Inspect
exports before sharing them, and rotate credentials exposed in older exports.

## Static-analysis triage

The October 2026 CodeQL `py/path-injection` findings sourced from the three
environment variables above describe intentional local configuration, rather
than an untrusted remote path crossing an authorization boundary. Findings in
tests follow those same functions into synthetic, temporary fixtures. Tests
isolate all session stores from the developer's real home directory.

These findings can be classified as false positives for this local-CLI threat
model. This does not establish a safe boundary for a future server wrapper;
untrusted session text must never be promoted into trusted environment settings
or filesystem arguments.
