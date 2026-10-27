---
name: spine-character
description: 角色動作／Spine 動畫的預設製程。開新角色（先定動作再定生圖姿勢）、寫生圖拆層需求、角色圖層 PNG 套動作、做 Spine 4.3 骨架或動畫、驗收交件、檢查流暢度或破圖時，優先用 spine-motion-kit（spine-motion 指令）。
---

# 角色動作：先用 spine-motion-kit

## 角色美術與動作：零可見缺陷交付門檻（Stone，2026-10-25）

只要交付角色美術、rig、動作或含角色的遊戲，就適用以下硬性驗收。**殘影、毛邊／白邊、碎片、肢體分離、接縫露底、破口或錯誤輪廓任一仍可見，即為未完成；修好前不得回報完成、交件或打包。**

- 逐層在亮底、暗底與原生解析度檢查。分層肢體必須是完整連續的部件，補齊被遮住的解剖與關節重疊；軀幹不得夾手臂殘影、拳套不得夾臉／胸口或袖口外的毛、服裝不得夾腿部碎片。網格角色也須檢查輪廓拉裂與貼圖殘影。
- Stone 補充（2026-10-26，相撲猴前臂修正）：粗壯角色仍須有連續、自然收窄的上下臂輪廓；前臂與肘部不得出現非設計需要的腫塊、圓球堆疊或香腸式分節。先辨別來源畫稿與動作變形，連同上臂末端及袖口銜接一起檢查；修來源後用原動作幅度驗證彎肘、伸展與轉場，不能只修靜態或靠縮小動作掩蓋。
- 逐一檢查**所有交付動作**（spine-motion 預設六動作也須全看），包含起終格、過渡／中間格、最大彎曲與伸展。重點看肩、肘、頸、髖、膝、腕；靜態疊圖、檢查器 PASS 或流暢度 100 都不能代替實際渲染。檢查器不支援此 rig 格式時不得當作通過。
- 先修造成缺陷的來源拆層、遮擋區補繪、權重或 rig。不得靠縮小動作／角色、加遮擋或挑好看的靜態格掩蓋；修正後重看受影響動作。合理動作預算仍須通過完整表演驗收。
- 已整合遊戲須在桌機與直式手機實際試玩，包含 MG／FG、角色反應、轉場與 feature cut-in，檢查破口、殘影、裁切與接縫；保留實際截圖、動作預覽及新舊對照。
- 交件前核對來源層、實際合成 full、Spine images、runtime 裁圖與座標、烘焙動作、預覽及 upload 包皆為同一最終版本。母圖若與合成有差異須如實留對照，不得冒稱零差異；不得拿舊預覽證明新版正常。

## 大盜猴是樣板角色（Stone，2026-10-27）

說到「大盜猴」，就是要做到 `examples/characters/bandit-monkey/` 一模一樣的效果：標準成品是那裡的 `monkey.json`。要大盜猴的動作時直接用它，或用 `spine-motion character examples/characters/bandit-monkey/layers` 重跑（會自動帶上 `layers.json`、`joints_manual.json`、`celebrate.params.json`、`motion.params.json`），結果必須和 `monkey.json` 逐字相同。不要為了配合工具改動去改這些檔案；`npm test` 的大盜猴樣板測試失敗時，先問 Stone。新角色要「像大盜猴那樣」時，以它的需求單、交件和參數當起點。

角色動作、Spine 動畫的需求，**預設用這套工具做**，不要從零手寫 Spine JSON 或自己算骨架。工具在 GitHub `stone8000/spine-generator`（本機通常在 `~/tools/spine-generator`，裝好後有全域指令 `spine-motion`）。Node 22.18 以上；GIF 需要 Python 3 + Pillow。

找不到 `spine-motion` 指令時：到 repo 根目錄用 `node bin/spine-motion.js …`；本機沒有 repo 就 `git clone https://github.com/stone8000/spine-generator && cd spine-generator && npm ci`。

## 0. 開新角色的製程：先定動作，再定生圖姿勢

製程一開始（還沒生圖、要寫需求單時）**一定先做這步**，不要先生圖再想動作：

1. 跟使用者確認這個角色要做哪些動作。預設 6 個：idle、idle_weave、punch、hit、jump、celebrate；另外要揮武器、轉身、坐著等要問清楚。
2. 照 `docs/生圖交付規格.md`「第 0 步」的對照表，把每個動作對圖的要求寫進需求單開頭。例如出拳要畫拳頭不能畫手掌，慶祝要補畫肩下和腋下，跳躍和待機的膝蓋要微彎，後手要收在臉頰旁。
3. 確認模板做得到：只有「面向右的側面拳擊架式」。正面、四足、長裙披風，或需要轉身的動作，先告訴使用者要另做模板。
4. 拆圖規則要寫進需求單：被擋處畫完；關節兩邊重疊，**重疊處不要描邊**，多畫的部分不能超出相鄰部件的外緣；關節不要切平。

範例需求單：`examples/characters/bandit-monkey/需求單.md`。

## 1. 有角色圖層 PNG → 一行做完（最常用）

```bash
spine-motion character <交件資料夾>            # 加 --no-gif 只要 2 秒
```

- 資料夾放部件 PNG（`head.png`、`torso.png`、`arm_front_upper.png`… 檔名見 `docs/生圖交付規格.md`），可附 `layers.json`（疊圖順序）、`joints.json`（手調關節）、`celebrate.params.json`（慶祝參數）、`motion.params.json`（各動作參數，例如出拳距離）、`budget.json`（每個關節撐得住的角度）；放在資料夾或上一層都會自動用。
- 輸出在旁邊的 `<角色名>_spine/`：`<角色名>.json` + `images/`（Spine 4.3，Import Data）、`報告.md`、`預覽.gif`、`預覽.html`、`joints.json`。
- 6 個動作：idle（彈跳待機）、idle_weave（閃身待機）、punch、hit、jump、celebrate。
- **一定要讀 `報告.md`** 再回報：開頭有結論（可以用／需要看一下），⚠ 項目旁邊寫了怎麼修。把 `預覽.gif` 給使用者看。

常見修正：
- 關節檢查 ⚠（脖子反折、頭頂點太低、肩膀點太低）→ 改輸出的 `joints.json`（畫布像素，y 向下）後 `--joints joints.json` 重跑；或請使用者用 `studio/spine-motion-studio.html` 拖紅點。
- 接縫 ⚠ → 是圖的問題：那個關節兩邊的圖層要補畫、要重疊，回給生圖端。
- 關節處多一圈黑線、像人偶關節，或肩膀在靜止時就凸出一塊 → 也是圖的問題：重疊的那段不要描邊、多畫的部分收進相鄰部件的輪廓裡（交付規格第 2 條鐵律），開補畫單。
- 慶祝手臂轉太多（偏正面、手臂下垂的角色）→ `celebrate.params.json` 寫 `{ "params": { "swing": 30 } }`。
- 出拳太短、看不出在打拳 → `motion.params.json` 寫 `{ "punch": { "reach": 170, "lean": 9 } }`。前手畫成張開的手掌時，出拳和慶祝都不像打拳：前手改畫拳頭（可以先用後手拳頭圖放到前手腕）。
- 出拳時後手、受擊時雙手預設就會跟著身體走（`guard` 預設 1）。要舊的「手留在原地」效果才設 `{ "punch": { "guard": 0 } }` 或 `{ "hit": { "guard": 0 } }`。後手還是被甩開時，再加 `"lean": 0`。
- 正面寬架式的角色，套用大盜猴那種壓低後手的 `celebrate.params.json` 會讓後手掉到肚子前 → 拿掉它，用預設的雙手高舉。
- 流暢度 < 80 多半是腿畫太直造成 IK 頓挫；分數 ≥ 80 就算順。
- 翻面 ⚠（手肘／膝蓋反折、部件鏡像）→ 這個動作對這張圖不適用，調參數或開補畫單；會讓結論變成「需要看一下」。
- 超出角度預算 ⚠ → 只在有 `budget.json` 時出現；調小動作幅度或補畫。
- 第一格離原圖 ⚠（只看待機）→ 只提醒、不影響結論，預設待機本來就從擺動途中開始。

### 整批／排程驗收交件

交件是 zip、圖超過 2048 px、或要排程監看交件資料夾時，用 intake 流程（跟工作室同一套檢查）：`python -I scripts/intake_images.py prepare <交件> <工作資料夾>` → `npm run intake -- <工作資料夾>` → `python -I scripts/intake_images.py render <工作資料夾>`，看 `output/report.md`。

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

先做第 0 步（定動作 → 定姿勢）。然後照 `docs/生圖交付規格.md`：面向右的拳擊架式、1024×1536 透明 PNG、拆成固定檔名的圖層、被遮住的地方要補畫、關節兩邊要重疊。參考圖 `docs/pose-reference.png`。範例需求單：`examples/characters/bandit-monkey/需求單.md`。

## 5. 遊戲整合與重拆的版本一致性

- 先確認遊戲實際使用的 Spine runtime 版本。輸出是4.3，不可直接交給4.2載入器；需要相容runtime，或以真正4.3 runtime評估動畫（含IK／physics）後烘焙成遊戲可讀資料。不要為單一角色無故升級所有共用遊戲的runtime，也不要改版本字串冒充相容。
- 重拆美術的前後對照先沿用同一份 `joints.json` 與 `motion.params.json`；若必須改關節，留下差異與理由。生成前確認實際載入的參數：舊 `celebrate.params.json` 會覆蓋同名慶祝參數，不得讓歷史檔默默蓋過本次配方；保存CLI報告與實際使用的配方。
- 輸入只包含實際交付的部件；每層維持同一畫布與原位。先從一張完整角色取得一致可見解剖，再補繪遮擋區；不要用多格圖集各生不同部件、碎毛球或露出的矩形補片代替完整肢體。
- 正確順序是：驗收來源層與合成 → 官方工具產生rig／動作與報告 → 產生runtime裁圖並保存crop原點 → 依這些crop與真正4.3動作評估結果輸出／烘焙 → 重新量測角色邊界 → 遊戲實測 → 更新upload、縮圖、封面與預覽。來源輪廓、crop或rig改了，就重建受影響的下游資料，不能只換PNG配舊座標。
- 若只改RGB且能證明alpha、crop、rig與烘焙矩陣逐byte未變，可保留既有幾何資料；仍須同步來源、Spine images、runtime貼圖、建置與upload，重出實際預覽。不要把歷史參數報告或舊GIF混入本次交件。

## 限制

只支援 Spine 4.3 JSON；從圖層建角色只有「面向右側面拳擊架式」一套模板（正面、四足、長裙披風要另做）；只動骨骼，不做網格變形。
