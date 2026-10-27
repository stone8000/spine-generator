# AI 生圖交付與 Spine 4.3 動作驗證

依照 [`spine-motion-kit/docs/生圖交付規格.md`](../spine-motion-kit/docs/生圖交付規格.md) 完成真正 AI 角色繪製、16 張全畫布（`1024×1536` 透明背景）圖層拆解與關節補畫，並以 `spine-motion-kit`（Spine 4.3 runtime）完成自動猜關節、綁骨、6 種動作編譯、流暢度檢查與接縫（破圖）驗證。

## 交付檔案一覽

| 路徑 | 說明 |
|---|---|
| `layers/*.png` | **16 張標準交付圖層**（`1024×1536` RGBA 透明背景，部件留在原位、關節圓頭重疊、被遮擋處補畫完整），可直接拖入 `spine-motion-studio.html`「從圖層建立角色」 |
| `layer-sheet-ai.png` | 16 張 AI 圖層的 4×4 棋盤格分層總覽圖 |
| `composite.png` / `composite_transparent.png` | 16 張圖層依標準疊圖順序合成後的完整角色圖 |
| `character_ai_fullbody.png` | 提示詞 A 產出之整身 AI 拳擊手去背圖（`1024×1536` RGBA） |
| `parts_sheet_transparent.png` | AI 角色模組化部件原圖（去背 RGBA） |
| `true_joints.json` | 19 個關節點畫布座標（供對照自動猜關節誤差或載入工作室） |
| `output/boxer.gif` | **最終動畫展示 GIF**（239 幀，待機 → 出拳 → 受擊 → 閃身待機 → 跳躍 → 慶祝，0.12s 混接） |
| `output/boxer.json` + `output/images/*.png` | 可直接以 **Spine 4.3 `Import Data`** 匯入的完整骨架與 6 組動畫 |
| `output/report.md` | 自動猜關節誤差、流暢度分數與接縫檢查報告 |
| `output/總覽.png`、`output/sheet_punch2.png`、`output/sheet_celebrate4.png` | 動畫逐幀總覽與出拳／慶祝特寫分解圖 |
| `build_ai_character.py` | 從 AI 原圖提取部件、補齊關節圓頭並組裝 16 張 `1024×1536` 圖層的腳本 |

## Spine 4.3 自動驗證結果（摘自 `output/report.md`）

- **骨架建立**：20 根骨、16 個圖層、size `2.30`，**警告：無**
- **自動猜關節誤差**：主要關節（腰、肩、肘、腕、腿根、膝）誤差僅 **1–17 px**（未手動微調直接綁骨）
- **動作流暢度與接縫（破圖）檢查**：

| 動畫 | 長度 | 流暢度 | 錯誤/警告 | 接縫檢查 |
|---|---|---|---|---|
| `idle`（格鬥待機・彈跳型） | 2.00 s | **100** | 0 | ✓ |
| `idle_weave`（格鬥待機・閃身型） | 2.00 s | **100** | 0 | ✓ |
| `punch`（出拳） | 0.86 s | **100** | 0 | ✓ |
| `hit`（受擊反應） | 0.90 s | **100** | 0 | ✓ |
| `jump`（跳躍） | 1.20 s | **100** | 0 | ✓ |
| `celebrate`（慶祝） | 1.20 s | **100** | 0 | ✓ |
