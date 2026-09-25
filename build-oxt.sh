#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Blagovest Petrov <blagovest@petrovs.info>
# SPDX-FileCopyrightText: 2026 Vute Tech Ltd. <https://vute.tech>
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Packs src/ into dist/dragoman-libreoffice-<version>.oxt (an .oxt is a
# zip) and prints its path.
#
# The version lives in git tags only; src/description.xml carries the
# placeholder 0.0.0, and the packed copy gets the real version:
#   --version X.Y.Z[.N]   as given
#   a vX.Y.Z tag on HEAD  X.Y.Z
#   otherwise             <last vX.Y.Z tag>.<commits since it>, or
#                         0.0.0.<commit count> before the first tag, so a
#                         development build sorts above the release it
#                         follows in the Extension Manager
# The archive is reproducible: sorted entries and every timestamp set to
# SOURCE_DATE_EPOCH (default: the HEAD commit time).
#
# Usage: ./build-oxt.sh [--version X.Y.Z[.N]]

set -euo pipefail

here="$(cd -- "$(dirname -- "$0")" && pwd)"
version=""
case "${1:-}" in
--version) version="${2:?usage: build-oxt.sh [--version X.Y.Z[.N]]}" ;;
"") ;;
*) echo "usage: build-oxt.sh [--version X.Y.Z[.N]]" >&2; exit 2 ;;
esac

if [ -z "$version" ]; then
    if tag="$(git -C "$here" describe --tags --exact-match --match 'v[0-9]*' HEAD 2>/dev/null)"; then
        version="${tag#v}"
    elif described="$(git -C "$here" describe --tags --long --match 'v[0-9]*' HEAD 2>/dev/null)"; then
        # v1.2.3-5-gabcdef0: five commits after v1.2.3
        base="${described%-*}"
        version="${base%-*}"
        version="${version#v}.${base##*-}"
    else
        version="0.0.0.$(git -C "$here" rev-list --count HEAD 2>/dev/null || echo 0)"
    fi
fi
[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+(\.[0-9]+)?$ ]] || {
    echo "version must look like 1.2.3 or 1.2.3.4, got: $version" >&2
    exit 2
}

epoch="${SOURCE_DATE_EPOCH:-$(git -C "$here" log -1 --format=%ct 2>/dev/null || date +%s)}"
out="$here/dist/dragoman-libreoffice-$version.oxt"
mkdir -p "$here/dist"
rm -f "$out"

python3 - "$here/src" "$out" "$version" "$epoch" <<'EOF'
import os
import sys
import time
import zipfile

src, out, version, epoch = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
# Zip timestamps have two-second resolution and start in 1980.
stamp = time.gmtime(max(epoch, 315532800))[:6]

placeholder = '<version value="0.0.0"/>'
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for root, dirs, files in os.walk(src):
        dirs[:] = sorted(d for d in dirs if d != "__pycache__")
        for name in sorted(files):
            if name.endswith(".pyc"):
                continue
            path = os.path.join(root, name)
            arcname = os.path.relpath(path, src)
            with open(path, "rb") as f:
                data = f.read()
            if arcname == "description.xml":
                text = data.decode()
                if text.count(placeholder) != 1:
                    sys.exit(f"description.xml must contain {placeholder} exactly once")
                data = text.replace(placeholder, f'<version value="{version}"/>').encode()
            info = zipfile.ZipInfo(arcname, stamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, data)
print(out)
EOF
