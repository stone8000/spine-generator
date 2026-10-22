# qa：以官方 runtime 當裁判

用版本釘死的 `@esotericsoftware/spine-core@4.2.120`（預設）或 `4.3.13`（`--runtime 4.3`，主要目標），在 Node 裡逐格跑骨架，輸出兩種東西：
- **dump**：世界骨矩陣與世界頂點。Python 的 numpy LBS（`spinegen/lbs.py`）拿它比對，C1 閘門在 Python 那邊算。
- **check**：SOTA §5 的具名閘門。任何一條硬閘門失敗，exit 1，build 就不寫出貨檔。

runtime 只是 devDependency，由有 Spine 授權的人安裝；repo 裡沒有任何 runtime 原始碼（`node_modules/` 已被 gitignore）。
`package.json` 精確釘兩個套件（SOTA §3.3）：
- `@esotericsoftware/spine-core` 4.2.120：裁判。
- `@esotericsoftware/spine-canvaskit` 4.2.120：之後的預覽渲染要用（P1 還沒有用到）。它依賴的 spine-core 也是 4.2.120，npm 只裝一份；`npm test` 會檢查這兩件事。
`qa43/` 另外釘 `@esotericsoftware/spine-core` 4.3.13，`--runtime 4.3` 經由 `qa43/spine.mjs` 從那裡載入。

```bash
cd qa && npm ci          # 裝 4.2.120（精確版本，見 package-lock.json；含 canvaskit-wasm，約 32 MB）
npm test                 # 自測：合成手臂 + 本機有的話也跑典獄長／囚犯／猴子
```

## dump

```bash
node qa/run.mjs dump --skeleton x.json --atlas x.atlas [--anim name] [--times 0,0.1] [--fps 60] \
  [--physics update|reset|none] [--pose '{"chest":{"rotation":150}}'] [--pose-mode add|set] \
  [--constraints on|off] [--skin name] [--folds] [--dt grid|const] [--out f.json]
```

輸出 `{"runtime", "frames":[{"anim","t","trackTime"?,"bones":{名:{a,b,c,d,x,y}},"slots":{名:{"attachment","vertices"}}}]}`。

- 骨架放在原點，scale 1，不翻轉。`x`、`y` 是 worldX、worldY。
- 沒有 `--anim`：只輸出 setup pose（t = 0）。
- 有 `--anim`、沒有 `--times`：t = k/fps，從 0 到動畫長度（含）。每格依序做 `state.update(dt)`、`state.apply`、`skeleton.update(dt)`、`updateWorldTransform`；第一格用 `Physics.reset`，之後用 `--physics`（預設 update）。
- `--times`：落在 1/fps 格點上的時刻，和整段 dump 的同一格**逐位元相同**。不在格點上的時刻各自重跑一次（格點走到前一格，再走一個短步），不會改動其他時刻的 physics。
- `trackTime`：只有動畫格才有。track 0 實際用來套用動畫的時間，也就是每一步 dt 的浮點累加，不一定等於名目上的 `t`。Python 端（`oracle_check`）直接用它取樣 IR，不必自己重算。
- 頂點：mesh 用 `computeWorldVertices`（含 deform）。point attachment 是 `[x, y]`（4.2 的 `PointAttachment` 繼承 `VertexAttachment`，所以要先判斷它）。region 是 4 個角，順序是 attachment 局部座標的 (−w/2,−h/2)、(−w/2,+h/2)、(+w/2,+h/2)、(+w/2,−h/2)，也就是 y 朝上時的左下、左上、右上、右下。
- `--pose`：`add`（預設）把值加到當格的局部值上（setup 或動畫）；`set` 直接取代。每格算完就還原，不會累加。可寫 `@file.json`。
- `--constraints off`：所有約束的 mix 設成 0，得到純 FK。Python 端只做 FK 時用這個比對。
- `--folds`：每格多一個 `folds` 欄位（摺疊三角形數，定義見 G1）。預設不輸出，介面保持原樣。
- `--dt`：`grid`（預設）每步 dt = k/fps − (k−1)/fps；`const` 每步正好 1/fps，和固定步長的播放器、以及舊的 [E3] probe 一樣。兩者每步只差約 1e-17 s，但 physics 的步長等於幀長時，`remaining >= step` 偶爾會落到另一邊，P1 的數字就不同（見下方「目前量到的數字」）。
- 數值注意：spine-core 用 `PI = 3.1415927`（float32 常數）換算角度，0° 的骨也會有約 1e-7 的 b、c。

## check

```bash
node qa/run.mjs check --skeleton x.json --atlas x.atlas [--target 4.2] [--ir build/rig.json] \
  [--fps 30,60,120,240] [--pose JSON] [--pma true|false] [--c1 c1.json] [--skin name] \
  [--dt grid|const] [--out report.json]
```

stdout 是 `spinegen-qa/1` 報告；stderr 是每條閘門一行的摘要。exit：0 通過，1 有硬閘門失敗，2 用法錯誤或檔案讀不到。

每條閘門的格式是 `{id, pass, value, max, hard, worst?, note?, items?, data?, skipped?}`。`items` 列出具體是哪個 mesh 或約束；`hard: false` 是警告，不影響 exit。

| ID | 量法 | 門檻 |
|---|---|---|
| L1_VERSION | `skeleton.spine` 的 major.minor 等於 `--target`；載入不拋錯；宣告 <4.2 卻用了 4.2 功能（頂層 physics、`inherit`、`referenceScale`、physics timeline）；4.2 會忽略的舊鍵名（骨的 `transform`、linked mesh 的 `deform`、動畫的 `deform` 與 `draworder`、物件形式的 `skins`），item 會寫出 4.2 讀的新鍵名；4.3 的 `constraints[]` | 0 |
| L2_COUNTS | runtime 的 bones、slots、skins、animations、events、ik、transform、path、physics、mesh 數，同時對檔案本身的陣列（含 4.3 `constraints[]`）和 `--ir` | 差 0 |
| L3_ORDER | ik + transform + path + physics 的 `order` 剛好是 0..N−1。4.2 對每個 order 0..N−1 只跑第一個符合的約束（依 ik、transform、path、physics 的順序），所以缺號會把某條約束推到 N−1 之後，重複的 order 只有第一條會跑；item 會逐條寫出「never runs in 4.2」 | 0 |
| L4_CURVES_NAN | `curve` 是每個值 4 個數字；key 都是有限數；載入後的 frames／curves 與每一格 world 值都有限 | 0 |
| L5_IK_VALID | IK 骨都是 inherit normal；單骨 IK 有父骨；兩骨 IK 的父骨有父骨、子骨是直接子骨且長度 > 0 | 0 |
| L6_PHYSICS_VALID | 至少一個非零權重；rotate／shearX／scaleX 的骨長 ≥ 0.02H；fps、inertia、strength、damping、mass、limit 都明寫；limit ≥ 1000 | 0 |
| L7_ATLAS | page `size:` 等於 PNG 標頭的實際尺寸；region 在 page 內；每個 attachment 都有 region；`--pma` 有給才比 | 0 |
| L8_MESH | hull > 0 且 hull 頂點在最前：前 hull 個頂點依序走一圈邊界，其他邊界頂點都必須落在這個 hull 多邊形裡（洞的邊緣，編輯器視為內部頂點，合法，寫在 note）；落在外面的（外殼不是輪廓，或一塊分離的島）算違規。索引不越界；權重和 1 ± 1e-4 | 0 |
| L8_MESH_INFLUENCES | 每頂點 ≤ 3 個 influence。有 `--ir`（我們自己的輸出）是硬閘門，匯入檔只警告 | 0 |
| L9_TC_MIX | transform constraint 六個 mix 全部明寫（預設是 1） | 0 |
| G1_FOLDS | setup 加每支動畫 60 fps 的每一格。每個三角形的繞向（world 面積與 UV 面積正負號的乘積）和它所屬 mesh 在 setup 的繞向比對，不同就算摺疊。mesh 的 setup 繞向取多數決：一般是 −1（UV 朝下、骨架朝上）；故意鏡像的 mesh（骨 scaleX −1，左右共用美術）是 +1，鏡像本身不算摺疊，那是 G3 的事。setup 就已經和多數相反的三角形是靜止時的摺疊，會列在 item。value 是單格最多的摺疊數 | 0 |
| G3_FLIP | 每根骨 world 行列式的正負號與 setup 相同；IR 標 `flip` 的骨除外 | 0 |
| M1_FOOT_PLANT | IR 的 `foot_plant` 區間內，腳的位移 ≤ 1 px、速度 ≤ 5 px/s。沒有就 skipped | 1 px |
| P1_FPS_ROBUST | physics 骨尖端在 30／60／120 fps 對 240 fps，取 t = k/30 比較；門檻 (120/fps) px，也就是 4／2／1 px。`data.per_anim` 是每支動畫各自的偏差 | 見左 |
| P3_DETERMINISM | 同一組亂數 dt 序列（種子固定，1/240–1/24 s）跑兩次 | 0 |
| C1_ORACLE | Python 算（numpy LBS 對 dump）。value 取三者最大：world 頂點差、骨原點差、骨矩陣差 × 100 px（矩陣差換算成離骨 100 px 處的位移），所以沒有掛 attachment 的骨也要對。有 `--c1 c1.json`（`{value, max, pass, worst?}`）就併進報告；沒有就由 check 自己跑 `python -m spinegen.oracle_check --all`（setup 加每支動畫 60 fps，同樣的 `--pose`、`--skin`；直譯器用 `SPINEGEN_PYTHON`，預設 `python3`）。跑不起來時 C1 **失敗**（not measured），不會 skip：必過的閘門沒量到不算過 | 0.1 px |

像素門檻以 H = 900–1800 px 為準，超出這個範圍就依 H 線性換算。H 取 `skeleton.height`；沒有寫時，改用 setup pose 的外框高度。

`--ir` 目前讀這些欄位：
- `counts`：明寫的期望數量，優先使用。`spinegen/ir.py` 的 `save()` 會寫出這個欄位。沒有 `counts` 時，改從 `spinegen-rig/1` 推算：`bones`、`slots`、`skins`、`events`、`ik`、`transform`（`half_angle` 算一條）、`path`、`physics`，以及 `skins[].attachments` 裡的 mesh 數。IR 沒有的鍵就不比對。
- `foot_plant: [{bone, anim?, spans?: [[t0,t1]], point?: [x,y]}]`。
- `flip: [骨名]`，或在 `bones[]` 的骨上寫 `flip: true`。

## `--runtime 4.3`

```bash
cd qa43 && npm ci          # 4.3.13 釘在 qa43/（qa/ 自己的 node_modules 維持 4.2.120）
node qa/run.mjs check --runtime 4.3 --skeleton x.json --atlas x.atlas [--ir build/rig.json]
node qa/run.mjs dump  --runtime 4.3 --skeleton x.json --atlas x.atlas --anim idle
```

兩個 runtime 的差異都包在 `lib/rt42.mjs`、`lib/rt43.mjs`（骨的世界矩陣在 4.3 是 `bone.appliedPose`、約束是一條清單、
`setupPose()` 等等），閘門只寫一次。`--target` 預設等於 `--runtime`。4.3 judge 跑 L1–L9、G1、G3、M1、P1、P3，其中依格式而不同的：

| ID | 4.3 的規則 |
|---|---|
| L1_VERSION | 另外檢查 4.3 不讀、而且不會說的寫法：頂層 `ik`/`transform`/`path`/`physics`（4.3 只讀 `constraints[]`）、`type` 不是 ik/transform/path/physics/slider 的條目、4.3 條目裡的 4.2 鍵名（transform 的 `target`/`local`/`relative`，path 的 `target`，IK 的 `uniform`）、linked mesh 用 `parent` 沒有 `source` |
| L2_COUNTS | runtime 的數量從 `data.constraints` 依型別數 |
| L3_ORDER | 4.3 沒有 `order`，照 `constraints[]` 的順序跑。檢查：條目上殘留的 `order` 與位置不符、同型別同名（timeline 只會綁到第一個）；有 `--ir` 時，清單順序必須等於 IR 的求值順序（spinegen 4.3 emitter 的「連續順序」） |
| L9_TC_MIX | 4.3 條目：`properties` 驅動的每個屬性，其 mix 都要明寫；沒有任何 `properties` 的條目什麼都不做；動畫 key 了某個沒被驅動的屬性的 mix（4.2 一定生效，4.3 什麼都不做）也算違規 |
| C1_ORACLE | **只有 4.2**：`spinegen.lbs` 是 4.2.120 的模型。4.3 時 skipped，note 指向同一份 IR 的 4.2 輸出（`--runtime 4.2` 跑 C1）與 `python -m spinegen.equiv43`（4.2 輸出在 4.2.120、4.3 輸出在 4.3.13 逐格比對） |

`npm test` 也跑 `test/qa43.test.mjs`（32 個，合成手臂的 4.3 版與一錯一閘門的變體）；qa43/ 沒裝時整組 skip。

## qa43

```bash
cd qa43 && npm ci && node load.mjs --skeleton x.json [--atlas x.atlas]
```

用 spine-core 4.3.13 載入，並列印 runtime 計數與檔案計數，exit 1 表示有東西掉了或載入拋錯。4.2 檔沒有被名字引用的約束會被靜默丟掉；有被動畫或 skin 引用時會拋錯（[E5]）。

## 目前量到的數字（2026-10-20）

| 角色 | 結果 |
|---|---|
| 典獄長 | L2：24 骨、20 mesh、4 IK、4 physics，與檔案相符。L8：20 個 mesh 都是 hull=0；重新輸出後剩 1 條（torso 有一塊在輪廓外的 6 頂點島）。L6：8 條（沒寫 fps／strength／mass，limit 60–80）。G1：0（252 格）；胸骨 +150° 時 setup 是 83，全部動畫中單格最多 86。P1：30／60／120 fps 分別是 2.66／1.64／0.53 px，通過；`--dt const` 時是 2.04／1.33／1.62 px，120 fps 不過。C1（check 自己量，252 格）：4.56e-5 px。4.3.13：約束 8 → 0，沒有拋錯。 |
| 囚犯 | L8：15 個 mesh 都是 hull=0；重新輸出後 0 條（towel 的洞在 hull 內，合法）。P1：60 fps 3.02 px，超過門檻 2（reactBig，towel2）。C1（252 格）：4.19e-5 px。 |
| 猴子 | L1：宣告 4.1.20 卻帶 7 條 4.2 頂層 physics。P1：60 fps 12.0 px，超過門檻 1.97（throwit，cuffL）。C1（1273 格）：1.17e-4 px。 |

P1 的基準和 [E3] 不同（典獄長 react 在 30／60 fps：這裡 1.92／1.20 px，[E3] 是 1.39／0.90 px）。原因是 dt 序列，不是 physics reset 的時間點：
- 這裡預設 `--dt grid`，每步 dt = k/fps − (k−1)/fps，好讓每個 fps 都正好落在 t = k/30。[E3]（`research/experiments/spine42/qa_probe.mjs`）每步用固定的 1/fps。
- 用 [E3] 的步法就能重現它的數字：`node qa/run.mjs check --skeleton $HT/warden.json --atlas $HT/warden.atlas --dt const`，報告裡 P1 的 `data.per_anim.react` 是 30 fps 1.39、60 fps 0.90（`npm test` 會驗證）。
- 改 reset 的時間點沒有影響：先在 setup pose reset、第一格改用 update，30／60 fps 仍是 1.92／1.20。
- `--dt const` 時，典獄長在 120 fps 反而是 1.62 px，超過門檻 1 px（reactBig）。所以 P1 的結果取決於播放器怎麼切 dt；這是 runtime 真實的行為。
