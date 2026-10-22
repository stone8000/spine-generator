# spine-motion-kit · Spine 4.3 流暢動作產生器

把「想要什麼動作」變成可以直接匯入 Spine 4.3 編輯器、也能直接在遊戲裡播放的動畫資料，並用真正的 Spine 4.3 runtime 檢查它順不順。

| 能力 | 說明 |
|---|---|
| **14 種動作預設** | 格鬥待機（Iron Jaw 實測數值）、呼吸、甩尾跟隨、出拳、受擊、跳躍、慶祝、符號落定／得獎／待機／預告、大字砸入 |
| **畫面空間重定向** | 預設描述「畫面上怎麼動」，工具依你的骨架（任何層級、旋轉、鏡像、IK）算出每根骨要 key 的值 |
| **物理烘焙** | 用 Spine 4.3 Physics 模擬頭髮、尾巴的晚半拍跟隨，再烘成一般關鍵影格：可編輯，關掉物理也能播 |
| **IK 防打直** | 手腳 IK 目標靠近極限時柔性收回，手肘膝蓋不會啪一聲打直或翻轉 |
| **runtime 精準取樣** | 依 Spine runtime 實際的播放方式（每段曲線 10 條直線）放關鍵影格，消除大動作的細微頓挫 |
| **流暢度檢查** | 任何 Spine 4.3 動畫（手做的也可以）：等速段、停頓、折角、速度突變、循環接縫、畫面頓挫、IK 打直、全身同步 |
| **離線預覽** | 單一 HTML 檔，內含 Spine runtime，洋蔥皮、動作軌跡、事件提示、高度／角度曲線 |
| **AI 生圖直接套用** | GPT / Gemini / MJ 生的分層 PNG → 自動猜關節、拖曳微調 → 自動建 Spine 4.3 骨架（含手腳 IK），用你的圖即時預覽 |
| **接縫檢查** | 用你的圖逐幀算出哪個關節露出背景（破圖）、在第幾秒，紅圈標出位置 |
| **翻面與關節角度** | 一行指令的報告裡：手肘膝蓋有沒有反折、部件有沒有鏡像、待機第一格離原圖幾度、每個動作把每個關節轉了幾度；附 `budget.json` 就檢查超出角度預算 |

---

## 最快的用法：一行做角色

拿到角色交件（部件 PNG，可附 `layers.json`），一行指令做完建骨架、套 6 個動作（待機兩種、出拳、受擊、跳躍、慶祝）、流暢度、接縫、翻面與關節角度檢查：

```bash
spine-motion character 交件資料夾/
```

輸出在旁邊的 `<角色名>_spine/`：Spine 4.3 JSON + `images/`（Import Data 即可）、`報告.md`（開頭就是結論與修法）、`預覽.gif`、`預覽.html`。選項與怎麼看報告見 **[`docs/一行做角色.md`](docs/一行做角色.md)**。

**裝到本機**（全域 `spine-motion` 指令，並把使用指引裝成 Claude Code 與 Codex 的技能）：

```bash
git clone https://github.com/stone8000/spine-generator ~/tools/spine-generator
~/tools/spine-generator/scripts/install-local.sh
```

給 agent 的說明在 [`AGENTS.md`](AGENTS.md) 與 [`skills/spine-character/SKILL.md`](skills/spine-character/SKILL.md)。

---

## 最簡單的用法：動作工作室（不用安裝）

直接用瀏覽器打開 **[`studio/spine-motion-studio.html`](studio/spine-motion-studio.html)**。這是單一檔案，引擎和 Spine 4.3 runtime 都包在裡面，離線也能用，可以直接傳給美術。

1. **開啟骨架**：按「開啟骨架 JSON」選 Spine 4.3 匯出的檔案，或先按「範例：骨架」「範例：符號」試用。工具會自動對應骨骼、依骨架大小調整動作位移（size），並只列出適合這副骨架的動作。
   沒有骨架、只有圖？見下一節「用 AI 生的圖做角色」。
2. **挑動作**：清單依把握度排序。

   | 星等 | 意思 | 動作 |
   |---|---|---|
   | ★★★ | 有 Iron Jaw 實測數據，並在 Spine 4.3 runtime 驗證 | 格鬥待機・彈跳型、格鬥待機・閃身型、出拳、符號落定、大字砸入 |
   | ★★ | 動畫常規，驗證過流暢 | 呼吸、甩尾跟隨、受擊反應、符號得獎、得獎循環、符號待機、預告顫動 |
   | ★（進階，預設隱藏） | 要看骨架怎麼做 | 跳躍（不彎腿）、慶祝（需要手臂 IK） |

3. **微調**：右邊調骨骼對應、動作參數，以及每個動作都有的「速度」「幅度」「朝向」。預覽與流暢度分數會即時更新。
4. **加入匯出清單**，全部挑好後按「下載動畫 JSON」（用 Spine 4.3 編輯器 Import Data 匯入，或直接給遊戲載入），或「下載配方」（給命令列工具重新產生）。

修改工作室程式後，用 `npm run studio` 重新打包。

### 用 AI 生的圖做角色

完整規格、提示詞（中英）、檢查清單見 **[`docs/生圖交付規格.md`](docs/生圖交付規格.md)**。重點：

1. 讓生圖 agent 照 [`docs/pose-reference.png`](docs/pose-reference.png) 畫一個面向右、拳擊架式的角色（1024×1536、透明背景），再拆成 16 個固定檔名的圖層（13 個必要），**每張都是整張畫布、部件留在原位，被擋住的地方要補畫、關節兩邊要重疊**。
2. 工作室按「從圖層建立角色」一次選全部 PNG。右邊會列出圖層檢查（缺圖、尺寸不同、檔名不對）；中間把 19 個紅點拖到真正的關節（滾輪放大、拖空白處平移、雙擊還原），按「建立骨架」。
3. 之後和一般骨架一樣挑動作、微調。預覽用你的圖播放，結果欄多一個**接縫檢查**：哪個關節在第幾秒露出背景，按「看這一幀」跳過去，紅圈標出位置。
4. 匯出的動畫 JSON 旁邊建 `images/` 資料夾放 PNG，Spine 4.3 Import Data 即可。

先按「範例：圖層角色」看正確的結果，「範例：有問題的圖層」看接縫檢查怎麼抓出肩膀沒畫、頭在下巴切平的圖。

完整的角色範例（圖層、關節、動作結果與驗證報告）在 [`examples/characters/`](examples/characters/)：[大盜猴](examples/characters/bandit-monkey/)是 AI 生圖的真實交件（含自訂疊圖順序 `layers.json`），[拳擊手](examples/characters/boxer/)是照規格畫的測試角色。

---

## 安裝（命令列工具）

需要 Node.js **22.18 以上**（可直接執行 TypeScript）。

```bash
npm install
npm test
```

## 三步驟

**1. 用 Spine 4.3 匯出骨架 JSON**（只要骨架、不需要任何動畫）。

**2. 產生配方草稿**：工具依骨骼名稱與 IK 約束猜出 hip、head、頭髮鏈、拳頭 IK 目標等角色，並依骨架高度估出動作位移倍率 `size`。

```bash
node bin/spine-motion.js init my-fighter.json
```

打開產生的 `my-fighter.recipe.json`，確認 `roles` 對應的骨骼正確、`facing` 正確（角色面向右 = `1`，向左 = `-1`）。

**3. 產生動畫，同時檢查與預覽**：

```bash
node bin/spine-motion.js generate my-fighter.json my-fighter.recipe.json --check --preview
```

會得到：

| 檔案 | 用途 |
|---|---|
| `my-fighter.motion.json` | 原骨架加上新動畫。用 Spine 4.3 編輯器 **Import Data** 匯入後可以繼續微調，或直接給遊戲載入 |
| `my-fighter.motion.check.md` | 流暢度報告 |
| `my-fighter.motion.preview.html` | 雙擊用瀏覽器開啟即可預覽 |

其他指令：

```bash
node bin/spine-motion.js presets
```

```bash
node bin/spine-motion.js check some-animation.json --loops idle,run
```

```bash
node bin/spine-motion.js preview some-animation.json
```

---

## 範例

`examples/` 有兩副測試骨架和三份配方：

```bash
npm run demo
```

| 配方 | 內容 |
|---|---|
| `examples/recipes/fighter.recipe.json` | 格鬥角色：彈跳待機、閃身待機、呼吸、出拳、受擊、跳躍、慶祝 |
| `examples/recipes/symbol.recipe.json` | 符號：漂浮待機、落定、得獎、得獎循環、預告顫動；大字砸入 |
| `examples/recipes/custom.recipe.json` | 不用預設，自己組 wave / poses / strike 軌道 |

測試骨架 `examples/rigs/fighter.json` 刻意做得很難搞：髖骨旋轉 90°、頭在三層之下、拳頭 IK 目標掛在 root。測試證明預設在這種骨架上仍然能精準重現 Iron Jaw 的畫面動作（振幅誤差 < 3%、相位誤差 ≤ 1 幀）。

---

## 文件

| 文件 | 內容 |
|---|---|
| [`docs/動作預設手冊.md`](docs/動作預設手冊.md) | 每個預設的角色、參數、時序 |
| [`docs/配方格式.md`](docs/配方格式.md) | 配方 JSON 完整說明、自訂軌道 |
| [`docs/流暢原則與Spine技巧.md`](docs/流暢原則與Spine技巧.md) | 為什麼這樣會順；Spine runtime 不寫在手冊裡的行為；給動畫師的建議 |
| [`docs/一行做角色.md`](docs/一行做角色.md) | `spine-motion character`：交件資料夾一行做完、輸出、看報告修問題 |
| [`docs/生圖交付規格.md`](docs/生圖交付規格.md) | 給 GPT / Gemini / MJ 的姿勢、圖層、提示詞與交件檢查；工作室訊息對照 |

## 驗證

`npm test` 共 55 個測試，全部用真正的 Spine 4.3.13 runtime 播放後量測：

- 每個預設在測試骨架上編譯、播放、通過流暢度檢查（循環 0 錯誤 0 警告）
- Iron Jaw 待機數值在 90° 旋轉骨架與鏡像骨架上都準確重現
- 物理烘焙的頭髮跟隨與即時物理相差 ≤ 1 幀
- IK 防打直：沒有保護時手肘打直鎖死，有保護時保持彎曲、跳動減少 3 倍以上
- 流暢度檢查能抓到故意做壞的待機（等速段、停頓、折角、接縫）
- 命令列完整流程：init → generate → check → preview
- 每個預設、每個參數都有工作室的清單與滑桿設定；速度、幅度控制正確（速度 2× 長度減半、事件跟著移動）
- 從圖層自動猜關節誤差 < 25 px；建出的骨架靜止時主要骨骼都落在畫的關節上、IK 解出的肘膝與畫的位置差 < 0.5 px
- 腿畫得幾乎打直時，所有角色動作都不會讓膝蓋打直頓挫（腿部 IK 柔化並補償目標位置）
- 出拳時身體往前衝，手臂仍伸到 90–96 %（IK 目標在身體之後求解，防打直量的是移動後的肩膀）
- 慶祝的雙拳舉到肩膀上方，手肘保持彎曲；自動骨架上手肘朝下、拳套在肩膀前方超過一個上臂長（上臂不橫過下巴）
- 慶祝的上臂不會離開畫的姿勢太多（`swing`，預設 30°）：手臂畫成往外下垂的角色，肩膀端不會轉出軀幹
- 交件附 `layers.json` 疊圖順序時骨架照它疊（後手拳套可以在胸前），沒列到的圖層留在模板的相對位置
- 關節合理性檢查：模板與自動猜的關節不報；脖子反折、頭軸歪掉、頭頂點太低、肩膀點太低都會列出來
- 跳躍時雙腳在下蹲與落地期間踩住地面（誤差 < 0.5），膝蓋往前彎
- 一行做角色：交件資料夾（含 `layers.json`、手調關節）直接產生 6 個動畫，疊圖順序照交件；缺必要圖層時指出檔名；PNG 讀取涵蓋各種色彩格式與濾波
- 接縫檢查：好的圖在各動作每一幀都不報；故意做壞的圖（肩下沒畫、頭在下巴切平）準確抓到前肩與脖子，且不誤報其他關節

## 授權提醒

本工具使用並在預覽頁內嵌官方 Spine Runtimes（`@esotericsoftware/spine-core` 4.3.13）。依 Spine Runtimes 授權，使用 runtime 的每位使用者都需要持有 Spine 編輯器授權。

## 限制

- 只支援 **Spine 4.3** 匯出的 JSON（4.3 runtime 只讀新的 `constraints` 格式，舊檔的 IK／物理會被忽略，所以工具會直接拒絕並說明）。
- 動作只作用在骨骼上，不產生網格變形（deform）、顏色、附件切換。逐幀序列（例如 Iron Jaw 的出拳影片）不在本工具範圍。
- 預設的「好看」數值來自 Iron Jaw 實測與動畫常規，套到不同畫風時請用 `intensity`、`size` 等參數調整，再用預覽確認。
- 從圖層建立角色目前只有一套模板：面向右的側面拳擊架式。正面角色、四足、長裙披風要另做模板。
