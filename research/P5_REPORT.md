# P3 補畫單、P5 第二隻角色（2026-10-23）

對照 [`SOTA.md`](SOTA.md) §4.3、§7 的 P3、P5 列。裁判仍是 `@esotericsoftware/spine-core@4.2.120`（`qa/`）。
猴子是第三方檔案、典獄長是使用者自己遊戲（Hard Time）的美術：兩者都在原地讀取，所有輸出都在 `/home/user/spinegen-work/` 底下，沒有進 repo。測試只用合成角色。

```bash
# 第一部分：猴子的補畫單（P2 最後的 rig + 橋接器的 celebrate 需求）
python -m spinegen.artreq /home/user/spinegen-work/p2/t3/final/monkey_joint \
    -o /home/user/spinegen-work/artreq/monkey --bridge /home/user/spinegen-work/bridge/v3/monkey_joint   # 70 s
# 第二部分：典獄長
W=/home/user/chenyuping1998/wp/apps/HardTime/static/assets/spines/htCast
O=/home/user/spinegen-work/warden
python -m spinegen.ingest_spine $W/warden.json $W/warden.atlas -o $O/layers
python -m spinegen.rigbuild $O/layers --mode joint -o $O/b1/warden_joint --name warden          # 20 s，沒有任何 --fit
node qa/run.mjs check --skeleton $O/b1/warden_joint/warden.json --atlas $O/b1/warden_joint/warden.atlas --ir $O/b1/warden_joint/rig.json
python -m spinegen.budget $O/b1/warden_joint/rig.json $O/b1/warden_joint/warden.atlas --joints $O/b1/warden_joint/joints.json \
    -o $O/b1/warden_joint/budget.json --trace --max-angle 150                                    # 含 IK 姿勢掃描，264 s
python -m spinegen.budget $W/warden.json $W/warden.atlas --joints $O/joints_shipped.json -o $O/budget_shipped.json --max-angle 150
python research/p2_crouch_grid.py $O/b1/warden_joint
python -m spinegen.artreq $O/b1/warden_joint -o $O/artreq                                       # 175 s
python research/p5_view.py $W/warden.json $W/warden.atlas $O/b1/warden_joint -o $O/b1/reactBig.png --anim reactBig --times 0.15,0.35,0.6,0.85
```

## 第一部分：補畫單（`spinegen/artreq.py`，SOTA P3 "artreq"）

### 做法

**規劃的動作**（`plan_poses`）：
- 目標角度：肩往外到 105°（SOTA P3：超過就換舉手的圖）、往內 30°；肘兩個方向到 120°（P2 還沒解決 2：120° 是肘窩被壓扁的角度）；每 5° 一格，一次只轉一根骨，IK 關掉（和 `budget` 同一種掃法）。
- 腿：IK 開著的姿勢，和 `budget.ik_scan` 同一種：蹲 0.02–0.10H × 重心 −40／0／+40 px、往旁邊跨步到 0.16H、抬腳到 0.12H。
- 橋接器（`bridge.json` 的 `budget.fit`）：每支動畫、每隻手臂「要求的」範圍（`before`，不是縮過的 `after`），肩 × 肘取格子，夾在上面的目標內（再過去要換圖，不是補畫）。
- 猴子共 249 個姿勢，在 1240×1230 的畫布上（`budget.scan_canvas`，整副骨架轉到 ±180° 都放得下）。

**每個姿勢收集的東西**（`Collector`），全部經過 `spinegen.lbs`（C1 釘在 spine-core 上）的蒙皮結果，不用剛性輪廓：
- **露出（reveal）**：這一格看得到的圖層 U 的紋理像素，用 `texel_home` 帶回靜止的位置，如果那裡靜止時被上層 O 蓋住（而且在 O 的覆蓋裡至少 2 px，O 的描邊邊緣那 1–2 px 不算），就記在 (U, O) 下。這就是 SOTA §2「沿動作掃遮擋者、取聯集、減掉靜止時看得到的」，直接在 U 自己的紋理上讀，所以 U 的蒙皮是精確的；「合理範圍」就是 U 自己的圖。
- **縫（extend）**：每個轉到的關節的 G4 接縫像素，和網格內部的 G5 洞，用畫在比較下面那一層最近的三角形（`art_gates.Part.locate`，最不在外的那個）帶回那一層自己的圖上：軀幹在袖子底下、前臂在袖口底下多畫一截，靜止時看不到。遮罩可以超出原圖（`pad`）。
- **擠扁（squash）**：網格三角形的面積比 < 0.3（G2 單一三角形的下限）或翻面，畫回那一層的圖上，按關節方向分（`elbow_l +`／`-`）。
- **夾帶碎片（clean）**：`budget.json` 的 artreq（G6b 判為上層帶著的碎片），遮罩 = `art_gates.duplicates` 的碎片紋理像素。
- **換圖（attachment）**：橋接器的 `artreq.json` 項目（一隻畫在某個姿勢的新手臂），加上每隻手臂舉過 105° 的 `arm_up_{s}`；遮罩是那個姿勢下手臂骨頭的膠囊（關節半寬），畫在整張角色畫布上。

同一個 (U, O) 的露出和縫合成一項（一個地方、一張遮罩）；遮罩往外擴 2 px（有縫時 4 px），但不碰靜止時看得到的像素。

**圖的檢查**：露出的地方只有「沒畫完」才是要畫的項目。猴子的圖層在每個遮擋處都是把邊緣顏色往裡拉的抹色（`cut_cast_layers.py` 的 UNDERLAY）；`copied_frac` 是露出的像素和最近的「靜止時看得到」的像素同色（ΔE < 3，第一圈 3 px 不算）的比例，≥ 0.5 判為抹色。有縫的項目不管圖看起來怎樣都要畫。看起來有畫的列為「請確認」，排在後面。

**輸出**（給美術的資料夾）：`README.md`（繁體中文，每項：哪裡不對、哪一層、用在哪個關節／角度／動畫、遮罩、截圖、提示詞）、`artreq.json`（`spinegen-artreq/1`，含 `signoff` 簽核欄位）、每項 `mask.png`（那一層圖片大小，必要時往外擴 pad）、`layer.png`（遮罩疊在圖層上）、`crop.png`（靜止 | 姿勢 | 問題塗洋紅；靜止那格用那一層自己的網格把問題位置帶回去對齊，蹲下或舉手時看到的是同一塊圖）、`prompt.txt`（英文＋中文的遮罩式影像編輯提示詞，寫法參考 motionkit 的生圖交付規格，這個分支上沒有那份文件，是自己寫的）。

### 猴子（`/home/user/spinegen-work/artreq/monkey/`，249 個姿勢，70 s）

24 項：22 項要畫、2 項請確認。

| # | 項目 | 遮罩 px | 問題最明顯的姿勢 | 對應的預期項目 |
|---|---|---|---|---|
| 1–4 | 夾帶碎片：左手、右手各帶一片褲子（596 / 429 px）、左上臂帶一條軀幹條紋（2381）、左大腿帶右大腿的胯下顏料（1466） | | 預算的 `visible_from` 角度 × 1.5 | 手上的褲子碎片、左上臂的軀幹條紋 |
| 5、6 | 軀幹在右／左上臂底下：抹色（copied 0.77 / 0.85）＋肩頂的縫（544 / 167 px，超出原圖 6 / 12 px） | 6473 / 6428 | 肩往外 105° | 腋下的深色抹色、楔形 |
| 8 | 軀幹在左大腿底下：胯下的白縫（84 px，往下超出原圖 21 px）＋大腿上緣滑下去露出的抹色 | 1219 | 蹲 0.10H、重心 +40 | 胯下的縫 |
| 7、10 | 右大腿在左大腿底下、軀幹在右大腿底下（蹲下露出，抹色 1.0 / 0.98） | 2035 / 820 | 蹲 0.10H、重心 −40 | （預期以外，量到的） |
| 9、11 | 兩條大腿在手底下（手一動就露出抹色） | 730 / 502 | 肩 ±5° | （預期以外） |
| 12–15 | 袖子在肘窩收窄：左 +、右 −、右 +、左 − | 1300 / 941 / 777 / 555 | 肘 ±115–120° | 肘窩的袖口收窄（120°） |
| 16–18 | 前臂（毛）在肘窩收窄 | 508 / 405 / 238 | 肘 ±120° | （同一個問題的另一層） |
| 19、20 | 另畫兩隻 celebrate 姿勢的手臂（橋接器：肩 −17 / +19°、肘 +171 / −168°；目前縮成 41% / 53% 播） | 畫布遮罩 | 動畫 celebrate | 橋接器的換圖 |
| 21、22 | 另畫兩隻舉過頭的手臂（肩 105–165°） | 畫布遮罩 | 肩 165° | SOTA P3 的 `arm_up_l/r` |
| 23、24 | 請確認：大腿在小腿底下（copied 0.43） | 270 / 466 | 蹲 0.10H | |

**看圖**（每一項的 `crop.png`、`layer.png` 都看過）：
- 腋下（5、6）：肩 105° 的那格，軀幹在手臂底下是一條垂直的深色抹色（右）或水平拉長的條紋（左），洋紅色正好蓋在那條抹色上；肩頂一個小三角形的白縫也塗到了。`layer.png` 上遮罩是軀幹圖層左右兩側那條被手臂擋住的帶子，右邊在圖片外多 6 px。
- 胯下（8）：蹲 0.10H 重心 +40 時，左大腿和軀幹之間一條斜的白縫被塗成洋紅；另一條水平的洋紅是大腿上緣往下滑、露出的軀幹抹色（繩結被拉長的那一段）。遮罩在軀幹圖片的下緣、胯下的位置，往下超出 21 px。
- 碎片（1–4）：左手 +48° 那格，一條綠色褲子碎片浮在手的右緣外（青色），正是手的圖片右緣那一條；左上臂 +93° 時，軀幹條紋碎片浮在脖子前面。
- 肘窩（12–18）：分方向以後，遮罩都在那個方向的肘窩那一側、袖口上方（之前兩個方向混在一起，外側也被塗到，已修）。
- 換圖（19–22）：洋紅膠囊是新手臂要畫的範圍；疊在用現在的圖硬轉到那個姿勢的畫面上（celebrate 的肘 171° 整段袖子摺起來），看得出為什麼要換圖。

**和預期的差別**：預期的 6 類都有；多出來的是腿在手、別的腿底下的抹色（7、9–11），都量到抹色 ≥ 0.95，是真的。項目數 24 超過 SOTA P3 驗收①的「≤ 6 項」：那一條是「cheer＋punch」兩支動作，這裡掃的是全部目標角度和腿的 IK 姿勢。

### 典獄長（同一個工具，`/home/user/spinegen-work/warden/artreq/`，187 個姿勢）

20 項，其中 10 項是「請確認」：典獄長的被擋住處是手畫補完的（Hard Time 的 P1–P4、W1–W4），copied 只有 0.07–0.41，所以沒有被當成抹色。要畫的是：拿警棍那隻手肘打開時上臂和手指之間的楔形縫（rigid 肘，見下面）、左手底下一小塊軀幹、肩和肘的擠扁、兩隻舉手的圖。這是「抹色判定」在第二隻角色上能分開「沒畫完」和「畫完了」的證據。

回歸測試（`tests/test_artreq.py`，合成角色）：`test_paint_mask_is_the_smeared_underlay_the_raised_arm_reveals`（抹色的軀幹：要畫、遮罩不碰靜止時看得到的像素、在手臂那一側；同一張圖畫完的版本只是「請確認」）、`test_a_seam_that_opens_is_painted_on_the_lower_layer`（方切的肘：縫畫到前臂、往上超出原圖）、`test_squash_mask_sits_in_the_crook_the_elbow_bends_into`、`test_clean_mask_is_the_shard_the_forearm_carries`（遮罩 = 碎片的紋理像素，逐像素）、`test_pack_folder_with_a_bridge_attachment`。

## 第二部分：重建典獄長（SOTA P5）

### 結果

| 項目 | 原版（Hard Time 出貨的） | 生成器重建 | 備註 |
|---|---|---|---|
| 骨架 | 24 骨、4 條單骨 IK（`leg→knee`、`shin→foot`，目標掛 root，stretch/compress）、4 physics | 30 骨（含 2 個半角輔助骨）、2 條兩骨腿 IK、4 transform、4 physics | |
| 頂點 | 2634（每層 22 px 格網） | **606**（格網的 0.23；猴子 0.52） | |
| 多骨頂點 | 420 / 2634 | 267 / 606；B2 **0.996**（SOTA：0% → ≥ 90%） | |
| setup | | 和原版逐像素同一張圖（重組誤差 12 px > 2/255，1 px 48/255：重取樣的邊） | `$O/b1/setup.png` |
| QA（spine-core 4.2.120） | L6 失敗（limit 60/80）、L8 20 個 hull=0 | L1–L9、G1、G3、M1、P3、C1 過；**P1_FPS_ROBUST 失敗**（60 fps 4.03 px，上限 2.0） | 見還沒解決 |
| 帶過來的動畫 G1 | 0 | **0**（idle / react / reactBig / blink，252 格） | |
| physics `limit` | 60 / 60 / 60 / 80 | **5000**（修掉）；骨尖擺幅 whistle 9.3 → 26.9、cord1 4.7 → 11.3、cord2 16.4 → 28.0、keys 9.8 → 27.4 px | |
| 肘（θ_soft +／−） | 左 6 / 6、右 0 / 0（剛性卡片） | 左 **102 / 98**；右 0 / 0（setup 就折了 163°，保持剛性） | |
| 肩 | 左 16 / 16、右 118 / 6 | 左 22 / 56、右 84 / 6 | 右肩往 + 原版較大（原版 G4 在 118°、重建是上臂的單一三角形 84°）；兩邊往 − 都只有 6°（軀幹裡的前臂殘影，G6b） |
| 膝（逐關節，IK 關掉） | 4 / 4、2 / 4 | 52 / 56、36 / 74；IK 姿勢裡 **95° / 95°** | |
| 腳踝 | 4 / 6、6 / 4 | 42 / 42、40 / 46 | |
| 髖 | 2 / 36、40 / 2 | 10 / 36、22 / 10 | |
| 蹲下 | 原版的腿是兩條單骨 IK 加 stretch：髖往下時大腿被壓扁，不會彎膝 | 0.05H 腳底、整隻腳 **0.0000 px**，膝蓋往外各 178 px；spine-core 蹲的表 0.05–0.13H × −40…+40 px：**0 個摺疊**（沒有用 `--fit-crouch`） | 但不好看，見下 |
| IK 姿勢預算 | 沒有兩骨腿 IK，不能掃 | 蹲 hard 0.11–0.14H（髖的縫）；soft 0H（G6b，見下） | |

### 哪些直接就能用、哪些要改

**直接就能用（沒有改程式）**：`ingest_spine` 的分層（20 層、重組誤差可忽略）、模板 `biped_mascot` 的骨架（16 個角色骨＋8 個配件：警棍、哨子、對講機線、鑰匙）、肘和膝的關節帶與半角輔助骨、腿的兩骨 IK（膝蓋往外的規則、softness 的上限）、配件權重、physics 的帶過來、動畫的帶過來（4 支，沒有 timeline 被丟掉；但原版 key 在膝蓋 IK 目標 `knee_l/r` 上的移動，在重建裡落在沒有權重的配件骨上，不再動到腿）、`budget` 和 IK 姿勢掃描、`artreq`。蹲的表 0 摺疊是模板預設值直接得到的（猴子要 `--fit-crouch` 才到 0）。

**改了程式（一般性的，猴子 `rig.json` 和 P2 最後的版本逐位元相同，已重建驗證）**：
1. **骨頭對應看結構**（`template.match_bones`）：典獄長有一根掛在 root 的 IK 目標叫 `knee_l`，真正的小腿叫 `shin_l`；舊規則「最靠近 root 的贏」挑了 `knee_l`。也挑了 `waist` 當軀幹，而不是手臂掛的 `chest`。現在依模板順序決定，候選要在它模板父骨對應的骨頭底下，再比模板 `match` 的先後，最後才比深度。
2. **圖層屬於哪根骨看權重**（`ingest_spine._role`）：典獄長每個 slot 都掛在 root，網格權重才是真的骨頭；以前 20 層全部變成 root。現在 slot 骨不在網格權重裡時，取權重總和最大的骨。
3. **兩條關節帶的列落在同一條線上**（`mesh._band_points`，`ROW_MERGE` 0.01 px）：右肩和右肘各分一半距離，在 98 px 處各放一列，一條是直的約束邊、一條穿過列內點；斜的手臂上兩者不完全共線，三角化沿那條線裂開，網格分成兩塊（qa L8：24 個邊界頂點在 hull 外）。現在留列內點多的那一列。合成的斜手臂上：修前 17 / 17 個間距都裂開，修後 0。
4. **洞的簡化輪廓跑出外輪廓時不挖洞**（`mesh._outlines`）：保守的防護，那塊照實心網格，透明處由貼圖處理。
5. **setup 就折起來的關節保持剛性**（`rigbuild.joint_bands`，`FOLDED_MIN` 135°）：典獄長拿警棍的手臂在 setup 折了 163°，前臂的圖在上臂軸的「上臂那一側」，關節帶的 ramp 把前臂的圖算給上臂：reactBig 手肘打開時，警棍和手指跟著前臂走，前臂的圖卻留在肩膀（`$O/b1/reactBig06.png` 修前）。一根軸排不出兩段，所以兩段各自 100% 跟自己的骨頭，像原版的卡片。修後 reactBig 四格和原版一致（`$O/b1/reactBig.png`）。
6. **釘住的腳在腳踝線以下 100% 是腳**（`rigbuild._planted_sole`）：鞋子很寬，腳尖離傾斜的小腿軸 60 px，在關節帶的側向範圍外，bone heat 留了 4% 的小腿權重，0.05H 時整隻腳滑 1.2 px（建置的蹲下檢查失敗）。現在 0.0000。
7. **physics `limit` < 1000 拉到 5000**（`rigbuild`，`PHYS_LIMIT_MIN`／`PHYS_LIMIT_FIX`）：Hard Time 的 60/80 讓 follow-through 在幾百單位／秒就飽和。`match_physics` 改成和「limit 修好的原版」比擺幅（`_limit_fixed`），不然會把修好的擺幅又縮回 bug 的大小。兩邊擺幅相同，所以 mix 都留在 1。

回歸測試（`tests/test_p5_generality.py`，合成）：結構對應、權重決定圖層骨、同一條線的兩列（修前會失敗）、折起來的肘保持剛性、腳踝線以下全是腳、limit 拉高並用修好的原版比擺幅。

**角色專屬的、沒有一般化的**：
- 拿警棍的右肘仍然是剛性的：預算 0 / 0（和原版一樣，手肘一打開就開縫，`artreq` 第 1 項要求上臂往手指底下多畫一截）。真正的解法是一張不折的 rest 美術，或把警棍手臂做成換圖。
- 原版的 `waist` 骨變成髖下的配件：軀幹（`chest`）直接掛在髖上，原版 key 在 `waist` 上的動作（目前 4 支動畫都沒有 key `waist`）會只動到權重在 `waist` 上的頂點。模板只有一根軀幹骨。

### 看圖

- `$O/b1/setup.png`：原版 | 重建，看不出差別。
- `$O/b1/reactBig.png`（0.15 / 0.35 / 0.6 / 0.85 s，各一對）：手臂、警棍、頭、鑰匙、對講機線都和原版一致；腿不一樣：原版用 stretch IK 把大腿壓短，重建的兩骨 IK 讓膝蓋往外彎。
- `$O/b1/crouch003c.png`、`crouch010c.png`：**這是典獄長最不像的地方**。0.03H 時重建的兩條褲管已經從膝蓋往外彎成弧形，0.10H 是 O 型腿的青蛙蹲；網格本身是乾淨的（0 摺疊、腳不滑、褲管順著彎），但寫實比例、長直褲管的正面角色，膝蓋往外的規則（`knee: outward`，為大頭吉祥物定的）看起來是錯的。原版的「壓扁」在小幅度時反而自然。

## 第三部分：寫實角色的腿（第二種腿的樣式，`leg_style`）

```bash
O=/home/user/spinegen-work/warden
python -m spinegen.rigbuild $O/layers --mode joint -o $O/b3/warden_joint --name warden      # 12 s；自動選 forward
python research/p2_crouch_grid.py $O/b3/warden_joint --depths 0.03,0.05,0.10 --shifts=-40,-20,0,20,40 --runtime 4.2
python research/p2_crouch_grid.py $O/b3/warden_joint --depths 0.03,0.05,0.10 --shifts=-40,-20,0,20,40 --runtime 4.3
python -m spinegen.budget $O/b3/warden_joint/rig.json $O/b3/warden_joint/warden.atlas \
    --joints $O/b3/warden_joint/joints.json -o $O/b3/warden_joint/budget.json --trace --max-angle 150   # 9 分鐘
python research/p5_leg_view.py $W/warden.json $W/warden.atlas $O/b1/warden_joint $O/b3/warden_joint \
    -o $O/b3/legs.png --labels "shipped,outward b1,forward b3"
```

### 做法

模板 `biped_mascot` 多一個 `leg_style`（`rigbuild --leg-style auto|outward|forward`，預設 `auto`）：

- **`outward`**：原來的兩骨 IK、膝蓋往外（吉祥物）。程式路徑完全沒變：猴子重建的 `rig.json` md5 `34a966dc…` 和 P2 最後的版本相同（`$O/monkey_check2`，4 分 25 秒）。
- **`forward`**：正面看，膝蓋往鏡頭彎，所以蹲的時候腿是「沿自己的方向變短」（透視縮短），不是往旁邊折。每條腿：
  - 膝蓋目標 `ik_knee_<s>`（掛在腳的 IK 目標底下，跟著腳走），一條 world 平移的 transform constraint `knee_follow_<s>` 把它往髖拉：垂直方向 `knee_drop` = 0.3（小腿分到 30% 的縮短，大腿 70%：髖往後坐的蹲法，小腿接近垂直），水平方向 = 膝蓋在 髖–踝 線上的位置（`on_line`，典獄長 0.47），所以膝蓋一直在 髖–踝 線上，重心偏移時兩個膝蓋跟著移同樣的比例；
  - 大腿：單骨 IK 指向膝蓋目標（compress，大腿 scaleX 變短）；小腿：單骨 IK 指向腳（compress + stretch：小腿繼承大腿沿軸的縮短，要能在大腿的座標裡伸回腳上）；
  - 關節帶、半角輔助骨、釘住的腳（腳踝線以下 100% 是腳）和 outward 一樣。和原版的差別：原版的膝蓋目標靠動畫 key（髖的一半），這裡由 constraint 自動跟著，任何動作（橋接器、IK 掃描）都會用到。
- **`auto`**：看圖決定，兩個條件都要成立才選 forward：腿長（髖到踝）/ 身高 H ≥ 0.38，腿長 / 腿寬（大腿、小腿圖層的不透明面積 / 長度）≥ 3.5。典獄長 0.431 H、4.30 → forward；猴子 0.284 H、1.81 → outward；測試的合成角色 0.354 H、3.08 → outward。
- 一般化的配套：`ir.leg_chains`（兩種樣式的腿都認得：兩骨 IK，或大腿單骨 IK + 子骨小腿單骨 IK）取代各處的「`len(bones) == 2`」：`rigbuild.crouch`／`foot_contacts`／`planted_vertices`／`shin_bottoms`、`budget.ik_legs`、橋接器的 `planted_feet`、`p2_crouch_grid`、`p2_crouch_view`。`crouch()` 對 forward 的腿改量：膝蓋往旁邊移多少（≤ max(2 px, 0.1 × 髖下降)）、大腿／小腿的縮短比例、畫面上的彎曲（≤ 20°）。
- **G2 的「預期面積」**（`art_gates.band_area_ratio(expected=)`，只有 IK 掃描的 forward 腿會用）：透視縮短本來就讓面積變小，用 setup 的面積比會把第一格就判成擠扁（合成角色：沒有這條時直蹲 soft 0H，`area@hip_l`）。現在 forward 腿的關節以兩根骨中較短的沿軸縮放為預期值，帶和三角形都乘上它。outward 的腿 expected = 1，數字不變。

### 典獄長的結果（`$O/b3/warden_joint`）

| 項目 | outward（b1） | **forward（b3）** |
|---|---|---|
| 選到的樣式 | （當時唯一的） | auto → forward（0.431 H、長寬比 4.30） |
| 骨架 | 30 骨、2 兩骨 IK、4 transform | 32 骨（+2 膝蓋目標）、4 單骨 IK、6 transform（+2 `knee_follow`） |
| 頂點、B2 | 606、0.996 | 606、0.996（網格、權重相同） |
| 蹲 0.03／0.05／0.10H × dx −40／−20／0／+20／+40，spine-core 4.2.120 | 0 摺疊 | **0 摺疊**（15 格） |
| 同上，spine-core 4.3.13（`emit.spine43`） | | **0 摺疊**，膝蓋位置和 4.2 一樣（`$O/b3/grid_4.3.json`） |
| 整隻腳滑動 | 0 | **0.0000 px**（兩個 runtime、15 格） |
| 膝蓋往旁邊移 | 0.03H 就各往外 139 px，0.10H 245 px（彎 43° → 80°） | 直蹲 **0 px**；重心 ±40 px 時兩膝同方向各 **18.8 px**（0.47 × 偏移），左右相同 |
| 腿在畫面上的彎曲 | 43–80° | 1.0–2.8°（0.03–0.10H） |
| 大腿／小腿縮短 | | 0.03H 0.91／0.96，0.05H 0.85／0.93，0.10H 0.69／0.85 |
| 小腿底緣滑過鞋面 | | 0.21／0.37／0.83 px（小腿沿軸縮短，褲管底跟著往上收一點） |
| QA 4.2（`--ir`） | L1–L9、G1、G3、M1、P3、C1 過；P1 失敗 | 同左（G1：帶過來的 4 支動畫 252 格 0；C1 5.1e-5 px；P1 60 fps 4.03 px，和之前一樣） |
| QA 4.3 | P1 失敗（3.15 px） | 同左，其他全過 |
| IK 姿勢預算：直蹲 | soft 0H（G6b）、hard 0.14H（髖的縫） | **soft 0.14H**（G6b `thigh_l\|pelvis`）、**hard ≥ 0.20H**（掃描上限） |
| IK 姿勢預算：重心 ±20／±40 | hard 0.13–0.14／0.11–0.13H | hard 0.08–0.09／0.09–0.11H（±20：`hole@thigh_r`，胯下兩條褲管中間的縫；±40：髖的縫） |
| 跨步、抬腳 | | 跨步 hard 0.12–0.14H（鑰匙／髖的縫），抬腳 hard 0.20–0.22H |

膝的「IK 姿勢裡轉了幾度」對 forward 沒有意義（膝蓋在畫面上幾乎不彎：0.8–6°），預算的角度欄是小數字是對的。

### 看圖

- `$O/b3/legs.png`：三列（原版、outward b1、forward b3）× 六格（setup、0.03H、0.05H、0.10H、0.10H dx −40、+40）。
  - outward（b1）：0.03H 兩條褲管已經從膝蓋往外彎成弧形，0.10H 是青蛙蹲，鞋子被小腿拉成斜的。
  - forward（b3）：褲管保持直的，整條腿變短，膝蓋留在 髖–踝 線上；重心左右移時兩條腿一起斜過去，看起來是重心放在一隻腳上。跟原版的「壓扁」幾乎一樣，差別是原版大腿、小腿各縮一半（膝蓋的 key 是髖的一半），這裡大腿縮得多（0.10H：0.69 vs 0.85），膝蓋在畫面上比較高。
  - **老實說**：正面的膝蓋往前彎，在 2D 裡本來就只能讀成「腿變短」；畫面上沒有任何「膝蓋」的線索（沒有膝蓋凸出、褲子沒有在膝蓋處起皺），所以小幅度（0.03–0.05H）讀起來是自然的屈膝下沉；到 0.10H 大腿只剩 69%，褲子看起來偏短、有點像蹲低了但腿沒有變粗，比原版的同幅度略「矮」。不會再有 O 型腿。要更深的蹲，應該換一張蹲姿的腿圖（補畫單），不是再壓。
- `$O/b3/legs_drop.png`：`knee_drop` 0.3（預設）和 0.5（等於原版的動畫）的比較，兩個都可以；0.5 膝蓋位置和原版一樣，0.3 比較像髖往後坐的真實蹲姿。這是模板的參數。
- `$O/b3/legs_zoom.png`、`$O/b3/crotch2.png`：0.09–0.10H、重心 +20／+40 時胯下兩條褲管之間有一條細的白縫（預算的 `hole@thigh_r`），**原版在同一個姿勢也有一模一樣的縫**：骨盆的褲子圖在胯下太窄，是圖的問題（補畫單的項目），不是腿的樣式。

### 測試（`tests/test_leg_style.py`，合成角色，9 個）

`auto` 依圖選樣式（合成角色 → outward；把模板門檻降下來同一張圖 → forward；太粗 → outward；明確指定一定贏）、forward 的骨架和 constraint 順序（`knee_follow` < 大腿 IK < 小腿 IK < 半角輔助骨，setup 不變，`budget.ik_legs` 的腳不是膝蓋目標）、蹲 0.03／0.05／0.10H × ±40 px 0 摺疊、整隻腳 0 滑動、膝蓋左右對稱且移動 < 0.7 × 偏移、大腿縮得比小腿多、outward 的 rig 沒有 forward 的東西、IK 掃描能掃 forward 的腿（直蹲 soft = hard = 0.10H；把預期面積拿掉同一格就不過）、4.2 與 4.3 runtime 和 lbs 在蹲的姿勢逐頂點一致（≤ 0.01 px，0 摺疊）。

## 還沒解決

1. **P1_FPS_ROBUST**：典獄長 limit 修好以後 60 fps 4.03 px（上限 2.0，最差 reactBig 的 cord2），原版因為被節流才通過（1.64）。和猴子的還沒解決 7 同一類：要用 (f, ζ) 重新指定彈簧，或對 fps 敏感的鏈加 fps 設定。
2. **寫實角色的蹲**：已加第二種腿的樣式（第三部分，`leg_style: forward`），典獄長自動選到，0 摺疊、腳不滑、膝蓋不往外。還沒做的：正面的腿往前彎在 2D 只能讀成「變短」，0.10H 以上看起來偏矮，要換蹲姿的腿圖；側面角色的 forward（膝蓋往 facing 彎）還是用 outward 的 `knee: facing`；`auto` 的門檻（0.38 H、長寬比 3.5）是在三個角色之間挑的。P5 的 SOTA 驗收（idle ≤ 60 key、react 四肢 ≥ 60°）屬於動作合成，這次沒有做。
3. **G6b 把畫好的底層當殘影**：典獄長蹲 0.01H 就因 `ghost@thigh|pelvis` 不過（soft 0H）：骨盆在大腿底下手畫補完的褲子，和大腿的褲子相關性高，被判成殘影。P2 還沒解決 4（G6b 只看顏色）的另一個例子；`artreq` 的抹色判定在這裡反而分得開（copied 0.09–0.12）。
4. **補畫單的項目數**：猴子 24 項。肘窩的擠扁每個方向每層一項（7 項），可以合成「每個肘一項、兩張遮罩」；腿在手底下的抹色是真的，但手一動 5° 就露出，美術可能寧願整條大腿畫完。抹色判定的門檻（ΔE 3、0.5）是在猴子（0.77–1.0）和典獄長、合成角色（0.07–0.43）之間挑的，只有兩個真角色。
5. **自動補畫**（SOTA P3 的 Qwen-Image-Edit 與遮罩外 ΔE ≤ 3 拒收）、姿勢表、`fist_front` 沒有做；提示詞和遮罩已經是那一步的輸入。
