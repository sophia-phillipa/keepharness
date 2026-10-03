# Product identity and independent forks

`control/product.py` is the identity source: display name, distribution/command
slug, environment prefix, state/config paths relative to home, MCP name, branding
icon and light/dark defaults. The shipped values remain KeepHarness and all
existing identifiers are unchanged. Provider protocol identifiers and Tailscale
owner identifiers are outside this naming contract.

A downstream changes `PRODUCT` and its branding assets, then runs
`python -m control.product`. The build backend runs the same generator before
building. Static exceptions are the distribution metadata, downloadable
standalone MCP bridge/installer, UI labels and browser theme defaults. Do not edit
those generated identity values separately. `python -m control.product --identity
/path/to/identity.json` applies a complete synthetic/fork identity through the
same supported generator; it requires no patches to other source files.

Build each identity in its own source directory and install each wheel in its own
virtual environment. The bootstrap derives its environment and command from the
identity. The downloadable installer embeds that identity so the client does not
need the server's Python packages. Default state, configuration, commands, MCP
registration and browser theme storage are separate. The original product alone
accepts legacy unprefixed environment aliases.

A private `harness.identity.json` marker binds state to its slug and lineage.
A different identity cannot reuse marked state. Only the historical upstream may
adopt unmarked existing state; forks must start with new state. Local model folder
validation protects the effective admin state directory, its parents, children
and symlink aliases, as well as historical sensitive paths.

`tests/test_second_identity_build.py` builds and installs the original and a
synthetic identity side by side in temporary directories, exercises the generated
entry points and creates separate state markers. It never runs systemd or uses a
real user profile. Product renaming and migration of existing state are not part
of this release.
