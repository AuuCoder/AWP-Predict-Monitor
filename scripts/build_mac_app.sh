#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST_DIR="$ROOT_DIR/dist"
APP_NAME="AWP Predict Fleet.app"
APP_DIR="$DIST_DIR/$APP_NAME"
CONTENTS_DIR="$APP_DIR/Contents"
MACOS_DIR="$CONTENTS_DIR/MacOS"
RESOURCES_DIR="$CONTENTS_DIR/Resources"
PAYLOAD_DIR="$RESOURCES_DIR/awp-predict"

mkdir -p "$DIST_DIR"

python3 - <<PY
from pathlib import Path
import shutil

app_dir = Path(r"""$APP_DIR""")
payload_dir = Path(r"""$PAYLOAD_DIR""")
root_dir = Path(r"""$ROOT_DIR""")

if app_dir.exists():
    shutil.rmtree(app_dir)

(app_dir / "Contents" / "MacOS").mkdir(parents=True, exist_ok=True)
(app_dir / "Contents" / "Resources").mkdir(parents=True, exist_ok=True)
shutil.copytree(root_dir, payload_dir, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__", "dist", "*.pyc"))
PY

cat > "$CONTENTS_DIR/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDevelopmentRegion</key>
  <string>en</string>
  <key>CFBundleExecutable</key>
  <string>awp-predict-fleet</string>
  <key>CFBundleIdentifier</key>
  <string>local.codex.awp.predict.fleet</string>
  <key>CFBundleInfoDictionaryVersion</key>
  <string>6.0</string>
  <key>CFBundleName</key>
  <string>AWP Predict Fleet</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleShortVersionString</key>
  <string>0.1.0</string>
  <key>CFBundleVersion</key>
  <string>1</string>
  <key>LSMinimumSystemVersion</key>
  <string>13.0</string>
  <key>NSHighResolutionCapable</key>
  <true/>
</dict>
</plist>
PLIST

cat > "$MACOS_DIR/awp-predict-fleet" <<'SH'
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../Resources/awp-predict" && pwd)"

export PYTHONPATH="$ROOT_DIR${PYTHONPATH:+:$PYTHONPATH}"
exec /usr/bin/env python3 "$ROOT_DIR/awp_predict_browser_app.py"
SH

chmod +x "$MACOS_DIR/awp-predict-fleet"

echo "Built: $APP_DIR"
