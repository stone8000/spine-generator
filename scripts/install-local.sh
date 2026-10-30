#!/usr/bin/env bash
# Installs spine-motion-kit on this computer for people and agents:
#   - npm ci + npm link → a global `spine-motion` command
#   - skills/spine-character → ~/.claude/skills (Claude Code) and ~/.codex/skills (Codex), as links to this repo,
#     so `git pull` updates them
#   - the rule block agents/global-rules.md in ~/.claude/CLAUDE.md and ~/.codex/AGENTS.md (scripts/sync-rules.sh)
#   - macOS: a launchd job that pulls main and refreshes those rules at login and every hour (scripts/auto-sync.sh)
# Safe to run again.
set -euo pipefail
KIT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$KIT"

node -e 'const [a,b]=process.versions.node.split(".").map(Number); if (a<22||(a===22&&b<18)) { console.error("需要 Node.js 22.18 以上，目前 "+process.version); process.exit(1) }'
npm ci --no-audit --no-fund
if npm link >/dev/null 2>&1; then
  echo "✓ 全域指令：$(command -v spine-motion || echo 'spine-motion（開新的終端機後可用）')"
else
  # no write access to the global npm prefix: put a small launcher in ~/.local/bin instead
  mkdir -p "$HOME/.local/bin"
  printf '#!/bin/sh\nexec node "%s/bin/spine-motion.js" "$@"\n' "$KIT" > "$HOME/.local/bin/spine-motion"
  chmod +x "$HOME/.local/bin/spine-motion"
  echo "✓ 全域指令：$HOME/.local/bin/spine-motion（請確認 ~/.local/bin 在 PATH 裡）"
fi

for dir in "$HOME/.claude/skills" "$HOME/.codex/skills"; do
  mkdir -p "$dir"
  ln -sfn "$KIT/skills/spine-character" "$dir/spine-character"
  echo "✓ 技能：$dir/spine-character"
done

"$KIT/scripts/sync-rules.sh"

# macOS: keep this computer in sync with main automatically (at login and every hour), see scripts/auto-sync.sh
if [ "$(uname)" = "Darwin" ]; then
  LABEL="spine-motion-kit.auto-sync"
  PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
  mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
  cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>/bin/bash</string><string>$KIT/scripts/auto-sync.sh</string></array>
  <key>RunAtLoad</key><true/>
  <key>StartInterval</key><integer>3600</integer>
  <key>StandardOutPath</key><string>$HOME/Library/Logs/spine-motion-sync.log</string>
  <key>StandardErrorPath</key><string>$HOME/Library/Logs/spine-motion-sync.log</string>
</dict>
</plist>
PL
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$PLIST"
  echo "✓ 自動同步：開機登入時與每小時 git pull main 並更新全域說明（紀錄：~/Library/Logs/spine-motion-sync.log）"
fi

if python3 -c 'import PIL' 2>/dev/null; then
  echo "✓ Python + Pillow：可以產生預覽 GIF"
else
  echo "! 沒有 Python 3 + Pillow，預覽 GIF 會略過（其他檔案照常）。要 GIF 請執行：python3 -m pip install --user pillow"
fi
echo "完成。試試：spine-motion character \"$KIT/examples/characters/boxer/layers\" --no-gif"
