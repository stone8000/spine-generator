#!/usr/bin/env bash
# Installs spine-motion-kit on this computer for people and agents:
#   - npm ci + npm link → a global `spine-motion` command
#   - skills/spine-character → ~/.claude/skills (Claude Code) and ~/.codex/skills (Codex), as links to this repo,
#     so `git pull` updates them
#   - a short pointer block in ~/.codex/AGENTS.md (Codex's global instructions)
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

AGENTS="$HOME/.codex/AGENTS.md"
BEGIN="<!-- spine-motion-kit:begin -->"
END="<!-- spine-motion-kit:end -->"
touch "$AGENTS"
tmp="$(mktemp)"
awk -v b="$BEGIN" -v e="$END" '$0==b{skip=1} !skip{print} $0==e{skip=0}' "$AGENTS" > "$tmp"
cat >> "$tmp" <<MD
$BEGIN
## 角色動作／Spine 動畫：用 spine-motion-kit

角色動作、Spine 4.3 動畫、角色圖層套動作的需求，預設用 \`spine-motion\` 指令（repo：$KIT）。
最常用：\`spine-motion character <交件資料夾>\` → 輸出 Spine 4.3 JSON、報告.md、預覽.gif。
完整指引：$KIT/skills/spine-character/SKILL.md
$END
MD
mv "$tmp" "$AGENTS"
echo "✓ Codex 全域說明：$AGENTS"

if python3 -c 'import PIL' 2>/dev/null; then
  echo "✓ Python + Pillow：可以產生預覽 GIF"
else
  echo "! 沒有 Python 3 + Pillow，預覽 GIF 會略過（其他檔案照常）。要 GIF 請執行：python3 -m pip install --user pillow"
fi
echo "完成。試試：spine-motion character \"$KIT/examples/characters/boxer/layers\" --no-gif"
