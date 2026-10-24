#!/bin/bash
# Double-click to install ScriptCutter on this Mac. It opens by itself when it is ready.
cd "$(dirname "$0")" || exit 1
if [ -f app/install.sh ]; then
  exec /bin/bash app/install.sh "$@"
fi
exec /bin/bash install.sh "$@"  # the Mac folder of the source code
