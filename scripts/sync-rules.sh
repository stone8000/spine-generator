#!/usr/bin/env bash
# Writes agents/global-rules.md into the global instructions of Claude Code (~/.claude/CLAUDE.md) and Codex
# (~/.codex/AGENTS.md), so every session on this computer sees it, whatever folder it starts in.
# Only the block between the markers is replaced; the rest of each file is kept. Safe to run again.
set -euo pipefail
KIT="$(cd "$(dirname "$0")/.." && pwd)"
BEGIN="<!-- spine-motion-kit:begin -->"
END="<!-- spine-motion-kit:end -->"
RULES="$(sed "s|{{KIT}}|$KIT|g" "$KIT/agents/global-rules.md")"

write_block() {
  local file="$1" tmp
  mkdir -p "$(dirname "$file")"
  touch "$file"
  tmp="$(mktemp)"
  # also drops an older hand-written block that used :start instead of :begin
  awk -v b="$BEGIN" -v e="$END" '$0==b||$0=="<!-- spine-motion-kit:start -->"{skip=1} !skip{print} $0==e{skip=0}' "$file" > "$tmp"
  printf '%s\n%s\n%s\n' "$BEGIN" "$RULES" "$END" >> "$tmp"
  if cmp -s "$tmp" "$file"; then rm "$tmp"; else mv "$tmp" "$file"; fi
}
write_block "$HOME/.claude/CLAUDE.md"
echo "✓ Claude Code 全域說明：$HOME/.claude/CLAUDE.md"
write_block "$HOME/.codex/AGENTS.md"
echo "✓ Codex 全域說明：$HOME/.codex/AGENTS.md"
