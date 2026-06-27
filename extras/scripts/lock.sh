#!/usr/bin/env bash
# Blur-lock: per-output screenshot, blur, lock

set -euo pipefail

tmpdir=$(mktemp -d)
trap 'rm -rf "$tmpdir"' EXIT

args=()
while IFS= read -r out; do
    [ -n "$out" ] || continue
    if grim -l 0 -o "$out" "$tmpdir/$out.png" &&
        ffmpeg -y -i "$tmpdir/$out.png" -vf "boxblur=8:2" "$tmpdir/$out-blur.png" 2>/dev/null; then
        args+=("-i" "$out:$tmpdir/$out-blur.png")
    fi
done < <(wlr-randr 2>/dev/null | awk '/^[^ \t]/ {print $1}')

# Fallback to whole-canvas grab if per-output enumeration failed
if [ "${#args[@]}" -eq 0 ]; then
    grim -l 0 "$tmpdir/all.png"
    ffmpeg -y -i "$tmpdir/all.png" -vf "boxblur=8:2" "$tmpdir/all-blur.png" 2>/dev/null
    args=("-i" "$tmpdir/all-blur.png")
fi

exec swaylock -f "${args[@]}"
