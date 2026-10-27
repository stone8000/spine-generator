# AGENTS.md

給 Codex、Claude Code 等 coding agent 的說明。這個 repo 是 **spine-motion-kit**：Spine 4.3 角色動作產生器。角色動作、Spine 動畫的需求預設用它做，不要從零手寫 Spine JSON。

完整的使用指引（什麼情況用哪個指令、怎麼讀報告、怎麼修）在 [`skills/spine-character/SKILL.md`](skills/spine-character/SKILL.md)。

## 最常用：圖層資料夾一行做完

```bash
node bin/spine-motion.js character <交件資料夾>          # 裝成全域後：spine-motion character <交件資料夾>
```

輸出 `<角色名>_spine/`：Spine 4.3 JSON + `images/`、`報告.md`（先讀它的結論）、`預覽.gif`、`預覽.html`、`joints.json`。說明見 [`docs/一行做角色.md`](docs/一行做角色.md)。

還沒有圖、要開新角色的製程時：**先列要做的動作，再定生圖姿勢**（`docs/生圖交付規格.md` 的「第 0 步」），寫進需求單後才生圖。

## 開發

- Node 22.18 以上，直接執行 TypeScript（不需要 build）。`npm ci` 安裝。
- `npm test`（61 個測試，用真正的 Spine 4.3.13 runtime 量測）、`npm run typecheck`。改了 `src/studio/` 要 `npm run studio` 重新打包 `studio/spine-motion-studio.html`。
- 預覽 GIF 用 `scripts/trial/render_gif.py`（Python 3 + Pillow）。
- `src/recipe.ts`、`src/roles.ts`、`src/solve.ts`、`src/cli.ts`、`package.json` 是 CRLF，其他是 LF：編輯時保留原本的換行。
- 回覆使用者一律用繁體中文。

## 裝到本機

```bash
scripts/install-local.sh
```

`npm ci`、`npm link`（全域 `spine-motion` 指令）、把 `skills/spine-character` 裝到 `~/.claude/skills/` 與 `~/.codex/skills/`，並在 `~/.codex/AGENTS.md` 加一段指向這裡的說明。
