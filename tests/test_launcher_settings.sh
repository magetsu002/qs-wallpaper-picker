#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

PROJ="$TMP/project"
mkdir -p "$PROJ/scripts" "$PROJ/config" "$TMP/bin" "$TMP/home"
cp "$ROOT/scripts/open_picker.sh" "$PROJ/scripts/open_picker.sh"
cp "$ROOT/scripts/cache_paths.sh" "$PROJ/scripts/cache_paths.sh"
cp "$ROOT/config/Settings.qml.example" "$PROJ/config/Settings.qml.example"
printf 'import QtQuick\nItem {}\n' > "$PROJ/Main.qml"

cat > "$PROJ/scripts/sync_thumbs.sh" <<'STUB'
#!/usr/bin/env bash
exit 0
STUB
chmod +x "$PROJ/scripts/"*.sh

cat > "$TMP/bin/quickshell" <<'STUB'
#!/usr/bin/env bash
set -euo pipefail
project="$(dirname -- "${!#}")"
test -f "$project/config/Settings.qml"
cmp -s "$project/config/Settings.qml" "$project/config/Settings.qml.example"
STUB
chmod +x "$TMP/bin/quickshell"

HOME="$TMP/home" PATH="$TMP/bin:$PATH" "$PROJ/scripts/open_picker.sh"
cmp -s "$PROJ/config/Settings.qml" "$PROJ/config/Settings.qml.example"

printf '// user override\n' > "$PROJ/config/Settings.qml"
cat > "$TMP/bin/quickshell" <<'STUB'
#!/usr/bin/env bash
set -euo pipefail
project="$(dirname -- "${!#}")"
grep -Fxq '// user override' "$project/config/Settings.qml"
STUB
chmod +x "$TMP/bin/quickshell"

HOME="$TMP/home" PATH="$TMP/bin:$PATH" "$PROJ/scripts/open_picker.sh"
grep -Fxq '// user override' "$PROJ/config/Settings.qml"

echo "PASS launcher materializes missing settings without overwriting user settings"
