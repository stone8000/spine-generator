# 範例角色：大盜猴（AI 生圖的真實交件）

Go Banandit 的 MG 大盜猴改擺拳擊架式，照 [`需求單.md`](需求單.md)（[`docs/生圖交付規格.md`](../../../docs/生圖交付規格.md) 套在這隻角色上的版本）請 AI 生圖、拆成 14 個圖層。這是第一隻**真正由 AI 生的**角色，用來驗證工具能不能直接把 AI 的圖套上動作：待機、出拳、受擊、閃身、跳躍、慶祝。

| 檔案 | 內容 |
|---|---|
| `layers/` | 14 張 1024×1536 圖層、`layers.json`（這張圖專用的疊圖順序）、`sha256.json`（含 `full.png` 的雜湊） |
| `full.png` | 拆圖之前的整身圖 |
| `joints_manual.json` | 手動調好的 19 個關節點（自動猜關節在這隻角色上會偏，見下面） |
| `celebrate.params.json` | 這隻角色的慶祝參數（限制版，見下面） |
| `monkey.json` | 用上面三個檔案跑出來的 Spine 4.3 骨架與 6 個動畫，圖片路徑指向 `./layers/`，可直接 Import Data |
| `需求單.md`、`交件說明.md`、`reference/` | 給生圖端的需求單、生圖端的交件說明、需求單附的參考圖 |
| `generation/` | 生圖紀錄：完整提示詞、對位資料、疊回去的檢查（輪廓 IoU 99.94%）與對照圖 |
| `verification/` | 動作驗證：流暢度與接縫報告（自動／手動關節）、關鍵幀、各項對照圖 |

## 驗證結果（手動關節）

| 動畫 | 流暢度 | 接縫 |
|---|---|---|
| idle | 80 | ✓ |
| idle_weave | 90 | ✓ |
| punch | 100 | ✓ |
| hit | 100 | ✓ |
| jump | 100 | ✓ |
| celebrate | 85 | ✓ |

- **接縫：6 個動作全部沒有露底**，補畫、重疊都照規格做到了。
- **疊圖順序**：這張圖後手拳套護在胸前、繩結蓋在前腿上，模板的固定順序會讓胸口蓋掉後手拳套。照 `layers/layers.json` 重排就正常（工作室已支援，見 [`docs/studio-layers-order.png`](../../../docs/studio-layers-order.png)；對照見 `verification/疊圖順序對照.png`）。
- **自動猜關節失準**：這隻比模板更偏正面、站得寬，脖子底被猜到頭底上方、頭頂偏到帽簷、肩膀和腳踝偏 50–90 px；肘、腕、膝是準的。手動調 7 個點（脖子底、頭底、頭頂、兩肩、兩踝）後才合理，結果就是 `joints_manual.json`。工作室的關節檢查會提醒這種情況（[`docs/studio-joint-check.png`](../../../docs/studio-joint-check.png)；對照見 `verification/關節_自動vs手動.png`）。
- **扣分來源**：待機和慶祝的分數低，是腿部 IK 打直時的頓挫。這張圖兩腿畫得幾乎打直（寬站姿），模板預期膝蓋微彎。畫面上看不太出來，但數字有反映。

## 慶祝用限制版參數

工具的慶祝預設是雙手舉過頭（`swing` 180，不限制）。大盜猴這種偏正面、手臂往外下垂的角色會變成後手上臂轉 170°、拳套浮在頭上，所以這隻角色單獨用限制版：`swing` 30、`elbow` -25、`angle` 65、`elbowBack` -40、`angleBack` 50（`celebrate.params.json`；工作室裡把這 5 個滑桿調成同樣的值）。後手上臂轉 41°、前手 36°，流暢度 85、接縫 ✓。舉過頭版的樣子見 `verification/慶祝_舉過頭版_大盜猴.png`，修正前後見 `verification/慶祝修正對照.png`。

只把 `swing` 調成 30、其他用預設，在大盜猴上會頓挫（流暢度 20），所以要 5 個一起調。

## 重跑

在 repo 根目錄執行（需要 Python + Pillow）。產生的 `monkey.json` 除了圖片路徑（`./images/`）以外，和這裡收錄的逐字相同：

```bash
D=examples/characters/bandit-monkey
python scripts/trial/render_gif.py dump $D/layers /tmp/monkey_alpha
NAME=monkey JOINTS=$D/joints_manual.json CELEBRATE=$D/celebrate.params.json \
  node scripts/trial/render_frames.ts /tmp/monkey_alpha /tmp/monkey_out - $D/layers/layers.json
python scripts/trial/render_gif.py gif $D/layers /tmp/monkey_out/frames.json /tmp/monkey.gif
```

第三個參數改成 `-` 以外的關節檔，報告會多一段「自動猜關節的誤差」；拿掉 `JOINTS=` 就用自動猜關節（結果見 `verification/report_自動關節.md`）。GIF（約 9 MB）沒有放進 repo。
