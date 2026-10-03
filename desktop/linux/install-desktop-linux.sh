#!/usr/bin/env bash
# Installs this KeepHarness desktop package for the current user, without root:
#   ~/.local/opt/keepharness-<version>/  and  ~/.local/share/applications/keepharness.desktop
# An existing installation of the same version is never overwritten; the menu entry it replaces
# is kept as previous.desktop inside the new folder.
set -euo pipefail

source_dir="$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd -P)"
version="$(cat "$source_dir/VERSION")"
case "$version" in
  '' | *[!0-9A-Za-z.+-]*) echo "Invalid package version." >&2; exit 1 ;;
esac
target="$HOME/.local/opt/keepharness-$version"
entry="$HOME/.local/share/applications/keepharness.desktop"

mkdir -p "$HOME/.local/opt" "$HOME/.local/share/applications"
if [ -e "$target" ]; then
  echo "KeepHarness $version is already installed at $target. The existing installation was left untouched." >&2
  exit 1
fi
# Copy beside the target first, so an interrupted copy never leaves a half-installed version behind.
stage="$(mktemp -d "$HOME/.local/opt/.keepharness-XXXXXX")"
trap 'rm -rf -- "$stage"' EXIT
cp -a "$source_dir/." "$stage/"
if [ -f "$entry" ]; then cp -- "$entry" "$stage/previous.desktop"; fi
mv -T -- "$stage" "$target"

temp_entry="$(mktemp "$HOME/.local/share/applications/.keepharness-XXXXXX")"
while IFS= read -r line; do
  case "$line" in
    Exec=*) printf 'Exec="%s/keepharness"\n' "$target" ;;
    Icon=*) printf 'Icon=%s/share/icons/hicolor/256x256/apps/keepharness.png\n' "$target" ;;
    *) printf '%s\n' "$line" ;;
  esac
done < "$target/share/applications/keepharness.desktop" > "$temp_entry"
chmod 644 "$temp_entry"
mv -- "$temp_entry" "$entry"

echo "KeepHarness $version installed in $target. Look for KeepHarness in the applications menu."
