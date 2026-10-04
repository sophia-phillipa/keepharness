#!/bin/sh
# Starts the packaged KeepHarness desktop client (installed as `keepharness`, next to keepharness-bin).
# KEEPHARNESS_PYTHON points at a Python environment with KeepHarness installed; without it the one
# install.sh made is used (KEEPHARNESS_VENV, else ~/.local/share/keepharness/venv). A KeepHarness
# admin that is already running is attached to; KEEPHARNESS_ADMIN_PORT and KEEPHARNESS_PORT pick its ports.
set -eu

# Chromium's caches live in a private folder under the profile, not in ~/.cache. The backend and
# the CLIs it starts keep the user's own cache folder (see backendEnv in policy.cjs).
cache_home="$HOME/.config/KeepHarness/xdg-cache"
(umask 077 && mkdir -p -- "$cache_home")
export KEEPHARNESS_HOST_XDG_CACHE_HOME="${XDG_CACHE_HOME:-}"
export XDG_CACHE_HOME="$cache_home"

if [ -z "${KEEPHARNESS_PYTHON:-}" ]; then
  venv="${KEEPHARNESS_VENV:-$HOME/.local/share/keepharness/venv}"
  if [ -x "$venv/bin/python" ]; then export KEEPHARNESS_PYTHON="$venv/bin/python"; fi
fi

launcher_dir=$(CDPATH='' cd -- "$(dirname -- "$(readlink -f -- "$0")")" && pwd -P)
if [ -r /proc/sys/kernel/apparmor_restrict_unprivileged_userns ] &&
   [ "$(cat /proc/sys/kernel/apparmor_restrict_unprivileged_userns)" = 1 ] &&
   [ ! -u "$launcher_dir/chrome-sandbox" ]; then
  echo 'Ubuntu AppArmor may block the Chromium sandbox: check kernel.apparmor_restrict_unprivileged_userns and the apparmor policy for this installation.' >&2
fi
exec "$launcher_dir/keepharness-bin" "$@"
