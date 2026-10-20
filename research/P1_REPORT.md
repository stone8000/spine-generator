# P1 驗收報告：裁判與輸出骨幹（2026-10-20，審查後修訂）

對照 [`SOTA.md`](SOTA.md) §7 的 P1 列。裁判是 `@esotericsoftware/spine-core@4.2.120`（`qa/`），4.3 用 `4.3.13`（`qa43/`）。`qa/` 另外精確釘 `@esotericsoftware/spine-canvaskit@4.2.120`（§3.3），三者都是精確版本。第三方檔案都在原地讀取，沒有複製進 repo。

```bash
HT=/home/user/chenyuping1998/wp/apps/HardTime/static/assets/spines/htCast
MK=/home/user/hacksaw-character-motion/gobananas_monkey/spines/bananditBandit
(cd qa && npm ci) && (cd qa43 && npm ci)
```

## 結果

| # | 驗收項目 | 指令 | 量到的值 | 結果 |
|---|---|---|---|---|
| ① | 典獄長 L2 計數、L8 標出 hull=0 | `node qa/run.mjs check --skeleton $HT/warden.json --atlas $HT/warden.atlas` | L2：runtime 與檔案都是 24 骨、20 slot、20 mesh、4 IK、4 physics、4 動畫，差 0。L8：value 20，20 條都是 `hull=0` | 通過 |
| ② | 猴子 L1 標出「宣告 4.1.20 卻有 physics」 | `node qa/run.mjs check --skeleton $MK/monkey.json --atlas $MK/monkey.atlas` | L1 FAIL，value 2：`declares 4.1.20, target is 4.2`、`top-level physics[] (7 constraints) is a 4.2 feature in a file declaring 4.1.20` | 通過 |
| ③ | 典獄長用 4.3.13 載入 | `node qa43/load.mjs --skeleton $HT/warden.json --atlas $HT/warden.atlas` | **沒有拋錯**，約束靜默掉光：constraints 0（檔案 8）、ik 0／4、physics 0／4。L2_COUNTS FAIL，exit 1 | 通過 |
| ④ | 胸骨 +150° 時 G1 ≥ 1；react 是 0 | `node qa/run.mjs dump ... --pose '{"chest":{"rotation":150}}' --folds`<br>`node qa/run.mjs check ... --pose '{"chest":{"rotation":150}}'`<br>`node qa/run.mjs dump ... --anim react --folds` | setup 加 +150°：**83** 個摺疊（與 [E11] 相同）。check 掃全部動畫：單格最多 86（react t=0.55，torso 佔 75），共 21027 個摺疊三角形格。react 本身：60 fps 的 61 格全部 **0**；不加 pose 的 check，G1 在 252 格裡是 0。G1 改成「和 mesh 自己的 setup 繞向比」之後，這些數字沒有變（三隻角色都沒有鏡像的 mesh） | 通過 |
| ⑤ | numpy LBS 與 spine-core 差 ≤ 0.1 px | `python -m spinegen.oracle_check $HT/warden.json $HT/warden.atlas [--anim idle --times 0,0.3,0.7,1.0,1.4,2.0 \| --anim react --times 0,0.1,0.333,0.433,0.667,1.0 \| --all]` | setup：4.51e-5 px。idle 6 個時刻：4.53e-5。react 6 個時刻（含節拍邊界）：4.53e-5。胸骨 +150°：4.65e-5，摺疊數兩邊相同。60 fps 全格：idle 121、react 61、reactBig 61、blink 8 格，最大 4.56e-5。`--all --folds`（setup 加全部動畫）：典獄長 4.56e-5（252 格）、囚犯 4.19e-5（252 格）、猴子 1.17e-4（1273 格）。C1 現在也比骨：骨原點最大 7.3e-5 px，骨矩陣最大 3.9e-7（乘 100 px 是 3.9e-5 px）。沒有 attachment 或頂點數不符，也沒有摺疊數不符 | 通過 |
| ⑥ | 典獄長 → IR → `emit/spine42` → spine-core，頂點差 ≤ 0.01 px | `python -m spinegen.reemit_check $HT/warden.json $HT/warden.atlas --fps 30 --physics none --gates` | 20 個 mesh 全部重排 hull。拓樸：兩個**檔案**的三角形經排列對回原本，逐個索引、順序與繞向都相同，UV 差 0。world：setup 加 4 支動畫、30 fps、128 格，頂點最大差 **6.37e-6 px**，骨原點與矩陣差 0。physics 打開（60 fps，252 格）也是 6.37e-6 px。囚犯 6.20e-6 px、猴子 6.48e-6 px（30 fps 不開 physics，以及 60 fps 開 physics 都一樣） | 通過 |

## 審查意見的處理

每一條都先用審查者的腳本（`gen.py`、`gen2.py`–`gen5.py`）重現，再對照 spine-core 4.2.120 的 `dist/` 查證 runtime 的行為（只讀，沒有抄進 repo）。**九條都屬實，沒有駁回。** 下表的「之前」是審查者量的值，「之後」是修正後同一支腳本量的值，都是 setup pose 的 C1。

| 審查項目 | 問題 | 修正 | 之前 → 之後 |
|---|---|---|---|
| P1-5（local 絕對 TC） | runtime 的 `applyAbsoluteLocal` 不把旋轉與 shearY 的差 wrap 到 ±180，scale 的混合還會除以骨自己的 scale。Python 原本兩者都照「乾淨」的算法。IR 的 `half_angle` 就是這種約束 | `lbs._transform_local` 照 runtime 的算法 | scale 混合 30.29 → 0；轉角超過 180° 82.46 → 0；`half_angle` 的 fore 在 181／200／−200° 時 82.46 → 0 |
| LBS-APPLIED-TRANSFORM | runtime 的 `updateAppliedTransform` 不是 compose 的反函數：有 shear 時 y scale 少一個 cos(shear) 因子；noRotationOrReflection 不把父骨的轉角加回去；noScale 以姿勢的 `rotation` 建座標框；root 的 shearY 正負號相反 | `lbs.applied_from_world` 照 runtime 重寫。physics 在 reset（setup 與明給姿勢就是這一格）時，mix ≠ 0 的骨要重算 applied 值；`world(physics="reset"\|"none")` | 先 world TC、再對父骨做 IK：normal 8.08／5.20 → 1.5e-14／0；noRotationOrReflection 49.99／22.99 → 8.0e-14／0；noScaleOrReflection 32.0／42.57 → 4.6e-7／4.6e-7；noScale 32.0 → 4.6e-7。local TC 讀 world TC 動過的骨：0.61 → 1.4e-14。physics（noScale）之後接 IK：16.19 → 4.6e-7 |
| （審查腳本中另一例） | world TC 的角度 runtime 只 wrap 一圈，Python 原本整個 wrap | `_wrap_pi_once` | 偏移 250°：82.46 → 0 |
| P1-5（兩骨 IK） | 父骨 scale 不均勻時（\|psx−psy\| > 1e-4），runtime 用橢圓的二次式解，Python 原本只用餘弦定律近似並發警告 | `lbs._ik2_ellipse`：從「子骨末端在 (psx·l2, psy·l2) 的橢圓上」推導二次式，構不到時取橢圓上最近或最遠的點 | 44.81 → 0；警告拿掉 |
| LBS-CONSTRAINT-ORDER | 4.2 對每個 order 0..N−1 只跑第一個符合的約束。缺號會把約束推到 N−1 之後，重複的只有第一條會跑；lbs 原本全部都跑 | reader 用 `ir.dead_by_order_42` 標 `runs: false` 並發警告；lbs 只跑 `ir.running()`。L3 的 item 會寫出「never runs in 4.2」 | order 缺號 143.2 → 1.0e-14；order 重複 143.2 → 1.0e-14 |
| REEMIT-GENERAL | emitter 重新編號後，原本不跑的約束會開始跑 | `runs: false` 的約束寫成 inert：mix 全 0、physics 的 mix 不再吃 global、刪掉 key 它的 timeline。`report["inert"]` 和 warnings 會列出來 | `reemit_check` order 缺號：FAIL 143 px → PASS 0 |
| READER-4.2-KEYS | reader 原本會讀 4.2.120 忽略的舊鍵名 | 只讀 4.2.120 讀的鍵：骨的 `transform`、linked mesh 的 `deform`、動畫的 `deform`、`draworder`、物件形式的 `skins` 一律忽略，每個都發警告。`read(..., upgrade_legacy=True)`（`reemit_check --upgrade-legacy`）明確選擇升級時，每一項都記成「behaviour change」。L1 也會標出這些鍵 | `transform: noScale` 的骨：42.4 → 7.1e-15；`reemit_check` FAIL 42.4 → PASS 0；加 `--upgrade-legacy` 時 FAIL 42.4，並說明這是刻意的行為改變 |
| G1-DEFINITION | 故意鏡像的 mesh（骨 scaleX −1）在 setup 每個三角形都被算成摺疊 | JS 和 Python 一起改：每個 mesh 在 setup 的繞向取多數決當基準（`foldReferences`／`lbs.fold_reference`）。setup 就和多數相反的三角形，列成 G1 的 item | 8 個三角形的鏡像 mesh：G1 FAIL 8 → pass 0（note 寫出 mirrored at setup）；把它折過 170° 仍會算摺疊 |
| GATE-C1-ENFORCED | 沒給 `--c1` 時 C1 是 SKIP，而 SKIP 算通過；`c1_result` 也不看骨 | check 沒有 `--c1` 時自己跑 `python -m spinegen.oracle_check --all`（setup 加每支動畫 60 fps）；跑不起來就 FAIL（not measured）。C1 的值取頂點差、骨原點差、骨矩陣差 × 100 px 三者最大 | 骨差 143 px 但沒有 slot 的情況：PASS → FAIL（單元測試）；`SPINEGEN_PYTHON` 指向不存在的直譯器時：C1 FAIL，exit 1 |
| P1-CANVASKIT（兩條） | `qa/package.json` 只釘了 spine-core | `npm install --save-dev --save-exact @esotericsoftware/spine-canvaskit@4.2.120`；`npm test` 檢查兩個都精確是 4.2.120、canvaskit 用的是同一份 spine-core（只裝一份，`Skeleton` 是同一個物件） | 已釘 |
| README-E3-BASELINE | README 說「改成先 reset 再跳進第一格，能重現 1.39／0.90」，沒有指令可以驗證 | 查證後發現**原說法是錯的**：reset 的時間點不影響（先在 setup reset，30／60 fps 仍是 1.92／1.20）。真正的原因是 dt 序列：[E3] 每步用固定的 1/fps，這裡用 k/fps − (k−1)/fps。新增 `--dt grid\|const`，P1 的報告多了 `data.per_anim`。`check --dt const` 重現 react 30 fps 1.39、60 fps 0.90，`npm test` 會驗證 | 有指令，數字重現 |

**新的 fixture 真的會抓到這些錯嗎？** `synth.features` 加了上面每一種情況（local 絕對 TC 的六個 mix 都不為 0，目標差 340°；兩個 parent 下各五種 inherit 的 world TC 加後續 IK；physics 骨後接 IK；不均勻 scale 的兩骨 IK，含構不到；需要多 wrap 一圈的 world TC；setup 就鏡像的 mesh；新的 `pose_wrap`）。另外加了 `order_gap`、`order_dup` 兩隻合成骨架。逐一把修正改回舊的寫法，再跑 C1（setup 加三個 pose）：

| 改回舊寫法 | C1 |
|---|---|
| 不改（基準） | 6.4e-6 px，摺疊數全部相符 |
| world TC 整個 wrap | 163.8 px |
| local 絕對 TC 用 wrap、不除以 scale | 246.6 px |
| applied 值用精確的反解 | 448.6 px |
| physics reset 當成什麼都不做 | 39.3 px |
| 兩骨 IK 只用餘弦定律 | 44.9 px |
| G1 不取 setup 基準 | 頂點相符，但 4 格的摺疊數不符 |

每一項都會讓 C1 失敗，所以這些測試不是碰巧通過。

## 輸出檔的 L1–L9

| 閘門 | 原檔 | 重新輸出 | 說明 |
|---|---|---|---|
| L1–L5、L7、L9 | pass | pass | L2 加 `--ir`（IR 的 counts）也是差 0 |
| L6 PHYSICS | FAIL 8 | FAIL 4 | 沒寫的 fps、strength、mass 都補寫了。剩下 4 條都是 `limit 60/80 < 1000`：這是 Hard Time 的內容 bug（節流 follow-through），重新輸出必須忠實保留才能畫得一樣，留給 P5 修 |
| L8 MESH | FAIL 20 | FAIL 1 | 20 個 `hull=0` 全部修好。剩下 torso：在輪廓外 43 px 處有一塊分離的 6 頂點島。Spine 的 hull 只能是一個多邊形，任何 hull 都裝不下它；要修只能拆成另一個 attachment，屬於美術或結構的修改，不是格式問題 |
| C1 | 4.56e-5（check 自己量，252 格） | 4.56e-5（252 格） | 兩邊都是 check 自己量的，涵蓋 setup 加全部動畫 |

囚犯重新輸出後 L8 是 0：towel 的洞在 hull 裡面，編輯器把洞的邊緣當成內部頂點，所以合法。猴子重新輸出後 L1、L6 也通過（寫成 4.2.43，每個 physics 欄位都明寫）。三隻角色的 reader 都沒有遇到舊鍵名，也沒有不跑的約束；只有猴子有「宣告 4.1.20 卻有頂層 physics」的警告。

## 測試

- `python3 -m pytest tests -q`：106 個通過。拿掉 oracle（`SPINEGEN_QA_RUN` 指向不存在的檔案）時，77 個通過、29 個 skip。
- `cd qa && npm test`：55 個通過。
- 新測試涵蓋：applied 值的 runtime 行為（手算）、不均勻兩骨 IK、鏡像 mesh 與 setup 摺疊（JS 和 Python 各一）、order 缺號與重複（C1、re-emit、L3、IR）、舊鍵名的忽略與明確升級（reader、L1、re-emit）、C1 會比骨、C1 沒量到就失敗、`--dt const`、兩個 runtime 套件的版本釘定。

## 不屬於 P1 驗收、但量到的事

**P1_FPS_ROBUST 閘門**（不是 P1 階段）：
- 典獄長通過：60 fps 1.64 px，門檻 2.00。
- 囚犯沒過：60 fps 3.02 px，門檻 2。
- 猴子沒過：60 fps 12.04 px，門檻 1.97。

這是 runtime 真實的行為，重新輸出前後數字相同。

**P1 取決於播放器怎麼切 dt。** 典獄長用 `--dt const`（每步正好 1/fps）時，30／60／120 fps 是 2.04／1.33／1.62 px，120 fps 超過門檻 1 px（reactBig）。兩種 dt 每步只差約 1e-17 s，但 physics 的步長等於幀長時，`remaining >= step` 偶爾會落到另一邊。預設的 grid dt 保證每個 fps 都正好落在 t = k/30。閘門該用哪一種 dt、要不要兩種都跑，留給 P4 決定（見 [`qa/README.md`](../qa/README.md)）。

## 還沒做的事

- lbs 不模擬 physics，只模擬 reset 那一格與 `Physics.none`。動畫格的 C1 一律用 `--physics none` 比。
- path constraint 沒有實作；mix 不為 0 時會發警告，C1 的頂點比對會抓到差異。三隻角色和合成骨架都沒有作用中的 path constraint。
- spine-canvaskit 已經釘好版本，也確認能載入，但 P1 還沒有用到它（預覽渲染屬於後面的階段）。
