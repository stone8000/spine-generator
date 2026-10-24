---
name: spine-character
description: 角色動作／Spine 動畫的預設工具。遇到角色圖層 PNG 要套動作、做 Spine 4.3 骨架或動畫、檢查動畫流暢度或破圖、寫生圖拆層需求時，優先用 spine-motion-kit（spine-motion 指令）。
---

# 角色動作：先用 spine-motion-kit

角色動作、Spine 動畫的需求，**預設用這套工具做**，不要從零手寫 Spine JSON 或自己算骨架。工具在 GitHub `stone8000/spine-generator`（本機通常在 `~/tools/spine-generator`，裝好後有全域指令 `spine-motion`）。Node 22.18 以上；GIF 需要 Python 3 + Pillow。

找不到 `spine-motion` 指令時：到 repo 根目錄用 `node bin/spine-motion.js …`；本機沒有 repo 就 `git clone https://github.com/stone8000/spine-generator && cd spine-generator && npm ci`。

## 1. 有角色圖層 PNG → 一行做完（最常用）

```bash
spine-motion character <交件資料夾>            # 加 --no-gif 只要 2 秒
```

- 資料夾放部件 PNG（`head.png`、`torso.png`、`arm_front_upper.png`… 檔名見 `docs/生圖交付規格.md`），可附 `layers.json`（疊圖順序）、`joints.json`（手調關節）、`celebrate.params.json`（慶祝參數）；放在資料夾或上一層都會自動用。
- 輸出在旁邊的 `<角色名>_spine/`：`<角色名>.json` + `images/`（Spine 4.3，Import Data）、`報告.md`、`預覽.gif`、`預覽.html`、`joints.json`。
- 6 個動作：idle（彈跳待機）、idle_weave（閃身待機）、punch、hit、jump、celebrate。
- **一定要讀 `報告.md`** 再回報：開頭有結論（可以用／需要看一下），⚠ 項目旁邊寫了怎麼修。把 `預覽.gif` 給使用者看。

常見修正：
- 關節檢查 ⚠（脖子反折、頭頂點太低、肩膀點太低）→ 改輸出的 `joints.json`（畫布像素，y 向下）後 `--joints joints.json` 重跑；或請使用者用 `studio/spine-motion-studio.html` 拖紅點。
- 接縫 ⚠ → 是圖的問題：那個關節兩邊的圖層要補畫、要重疊，回給生圖端。
- 慶祝手臂轉太多（偏正面、手臂下垂的角色）→ `celebrate.params.json` 寫 `{ "params": { "swing": 30 } }`。
- 流暢度 < 80 多半是腿畫太直造成 IK 頓挫；分數 ≥ 80 就算順。

## 2. 已經有 Spine 4.3 骨架 JSON → 配方產生動畫

```bash
spine-motion init 骨架.json                    # 猜骨骼角色，產生 骨架.recipe.json
spine-motion generate 骨架.json 骨架.recipe.json --check --preview
spine-motion presets                           # 14 種動作預設與參數
```

預設：格鬥待機（彈跳／閃身）、呼吸、甩尾跟隨、出拳、受擊、跳躍、慶祝、符號落定／得獎／得獎循環／待機、預告顫動、大字砸入。配方格式見 `docs/配方格式.md`，每個預設的參數見 `docs/動作預設手冊.md`。

## 3. 檢查任何 Spine 4.3 動畫

```bash
spine-motion check 動畫.json --loops idle,run  # 流暢度報告（手做的也行）
spine-motion preview 動畫.json                 # 離線預覽頁
```

## 4. 還沒有圖 → 先寫生圖需求

照 `docs/生圖交付規格.md`：面向右的拳擊架式、1024×1536 透明 PNG、拆成固定檔名的圖層、被遮住的地方要補畫、關節兩邊要重疊。參考圖 `docs/pose-reference.png`。範例需求單：`examples/characters/bandit-monkey/需求單.md`。

## 限制

只支援 Spine 4.3 JSON；從圖層建角色只有「面向右側面拳擊架式」一套模板（正面、四足、長裙披風要另做）；只動骨骼，不做網格變形。
