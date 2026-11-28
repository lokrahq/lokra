#!/bin/sh
# Install the standalone lokra, which needs no Python:
#
#   curl -fsSL https://lokra.dev/install.sh | sh
#
# LOKRA_VERSION=0.8.3 installs that release instead of the latest one.
# LOKRA_BIN_DIR puts the lokra command somewhere other than ~/.local/bin.
set -eu

repo="https://github.com/lokrahq/lokra"
bin_dir="${LOKRA_BIN_DIR:-$HOME/.local/bin}"
data_dir="${XDG_DATA_HOME:-$HOME/.local/share}/lokra"

fail() {
  printf 'lokra install: %s\n' "$1" >&2
  exit 1
}

case "$(uname -s)" in
  Darwin) os=macos ;;
  Linux) os=linux ;;
  *) fail "there is no standalone build for $(uname -s); install with: pipx install lokra" ;;
esac
case "$(uname -m)" in
  arm64 | aarch64) arch=arm64 ;;
  x86_64 | amd64) arch=x86_64 ;;
  *) fail "there is no standalone build for $(uname -m); install with: pipx install lokra" ;;
esac
command -v curl >/dev/null 2>&1 || fail "curl is needed to download lokra"

version="${LOKRA_VERSION:-}"
if [ -z "$version" ]; then
  latest=$(curl -fsSLI -o /dev/null -w '%{url_effective}' "$repo/releases/latest") || fail "could not reach GitHub"
  version="${latest##*/v}"
fi
version="${version#v}"
case "$version" in
  [0-9]*) ;;
  *) fail "could not find the latest release; set LOKRA_VERSION" ;;
esac

asset="lokra-$version-$os-$arch.tar.gz"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
printf 'Downloading lokra %s for %s %s\n' "$version" "$os" "$arch"
curl -fsSL "$repo/releases/download/v$version/$asset" -o "$tmp/$asset" || fail "release v$version has no $asset"
curl -fsSL "$repo/releases/download/v$version/SHA256SUMS" -o "$tmp/SHA256SUMS" || fail "release v$version has no SHA256SUMS"

expected=$(awk -v f="$asset" '$2 == f || $2 == "*" f { print $1 }' "$tmp/SHA256SUMS")
if command -v sha256sum >/dev/null 2>&1; then
  actual=$(sha256sum "$tmp/$asset" | cut -d ' ' -f 1)
else
  actual=$(shasum -a 256 "$tmp/$asset" | cut -d ' ' -f 1)
fi
[ -n "$expected" ] && [ "$expected" = "$actual" ] || fail "the download does not match SHA256SUMS; nothing was installed"

link="$bin_dir/lokra"
if [ -e "$link" ] || [ -L "$link" ]; then
  case "$(readlink "$link" 2>/dev/null || true)" in
    "$data_dir"/*) ;;
    *) fail "$link already exists, probably from pipx or uv; uninstall that first, or set LOKRA_BIN_DIR" ;;
  esac
fi

mkdir -p "$data_dir" "$bin_dir"
rm -rf "$data_dir/$version.new"
mkdir "$data_dir/$version.new"
tar -xzf "$tmp/$asset" -C "$data_dir/$version.new"
rm -rf "$data_dir/$version"
mv "$data_dir/$version.new" "$data_dir/$version"
ln -sf "$data_dir/$version/lokra/lokra" "$link"
for old in "$data_dir"/*; do
  [ "$old" = "$data_dir/$version" ] || rm -rf "$old"
done

printf 'Installed lokra %s at %s\n' "$version" "$link"
case ":$PATH:" in
  *":$bin_dir:"*) ;;
  *) printf '%s is not on your PATH. Add it, for example:\n  echo '\''export PATH="%s:$PATH"'\'' >> ~/.profile\n' "$bin_dir" "$bin_dir" ;;
esac
