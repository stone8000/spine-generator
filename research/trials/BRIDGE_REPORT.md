# 銜接報告：spinegen → Spine 4.3 → motion kit → 4.3 裁判（2026-10-22）

定案：**Spine 4.3 是主要目標**，4.2 留給舊遊戲。前幾輪做了四件事：IR 的 4.3 輸出、QA 換成 4.3.13 也能跑、
spinegen 骨架接上 motion kit 的預設動作、看圖。**B5（最新）**：預設動作照圖的預算（budget.json）縮幅度、IK 保護改成每條鏈自己的門檻；
猴子改用 motion-research 最新的 rig（肘的袖子長度逐肘挑、腳踝整列串接、配件分父骨權重；和 `p2/r12` 逐位元相同）。

## 指令

```bash
cd qa && npm ci; cd ../qa43 && npm ci; cd ../motionkit && npm ci; cd ..
P2=/home/user/spinegen-work/p2/final; W=/home/user/spinegen-work/bridge
HT=/home/user/chenyuping1998/wp/apps/HardTime/static/assets/spines/htCast

# B1 同一份 IR 輸出 4.2 與 4.3，各自在 4.2.120 / 4.3.13 播，逐格比世界頂點
python3 -m spinegen.equiv43 $P2/monkey_joint/rig.json $P2/monkey_joint/monkey.atlas --out $W/b1/monkey_joint
python3 -m spinegen.equiv43 $HT/warden.json $HT/warden.atlas --out $W/b1/warden
# B2 用 4.3.13 當裁判
node qa/run.mjs check --runtime 4.3 --skeleton x.json --atlas x.atlas --ir rig.json
# B3 銜接（joint、rigid 各跑一次），再出對照圖
python3 -m spinegen.bridge_motionkit $P2/monkey_joint/rig.json $P2/monkey_joint/monkey.atlas -o $W/monkey_joint --name monkey_joint
python3 -m spinegen.bridge_motionkit $P2/monkey_rigid/rig.json $P2/monkey_rigid/monkey.atlas -o $W/monkey_rigid --name monkey_rigid
python3 research/bridge_sheets.py $W/monkey_rigid $W/monkey_joint -o $W/sheets --all-frames
# 選項比較（--leg-softness 0、--arm-softness 0、--no-ik-guard），同一格並排；上一版的輸出在 $W/v1_capped/
python3 -m spinegen.bridge_motionkit $P2/monkey_joint/rig.json $P2/monkey_joint/monkey.atlas -o $W/exp_soft/joint_noguard --name monkey_joint --no-ik-guard
python3 research/bridge_compare.py $W/compare/celebrate_legs_capped_vs_soft.png legs celebrate 4,31 $W/v1_capped/monkey_joint $W/monkey_joint
python3 research/bridge_compare.py $W/compare/punch_arms_guard.png arms punch 0,8,11 $W/v1_capped/monkey_joint $W/monkey_joint $W/exp_soft/joint_noguard
```

輸出都在 `/home/user/spinegen-work/bridge/`（不進 repo）。B5 的指令（rig 重建在 `$W/rigs`，輸出在 `$W/v3`）：

```bash
L=/home/user/spinegen-work/p2/monkey_layers; R=$W/rigs; V=$W/v3
python3 -m spinegen.rigbuild $L --mode joint -o $R/monkey_joint --fit-reach 'elbow_l,elbow_r=2,2.25,2.5,3,3.5'
python3 -m spinegen.rigbuild $L --mode rigid -o $R/monkey_rigid
python3 -m spinegen.budget $R/monkey_joint/rig.json $R/monkey_joint/monkey.atlas --joints $R/monkey_joint/joints.json \
    -o $R/monkey_joint/budget.json --artreq $R/monkey_joint/artreq.json --max-angle 150        # rigid 同樣
# 銜接：預設會讀 rig.json 旁邊的 budget.json / joints.json（--budget none 關掉），IK 保護每條鏈自己的門檻（--kit-guard 用 kit 原本的）
python3 -m spinegen.bridge_motionkit $R/monkey_joint/rig.json $R/monkey_joint/monkey.atlas -o $V/monkey_joint --name monkey_joint
python3 -m spinegen.bridge_motionkit $R/monkey_rigid/rig.json $R/monkey_rigid/monkey.atlas -o $V/monkey_rigid --name monkey_rigid
python3 research/bridge_sheets.py $V/monkey_rigid $V/monkey_joint -o $V/sheets --all-frames
# 對照：$V/exp/joint_{kitguard,nobudget,kitguard_nobudget,noguard_nobudget}、rigid_nobudget（同樣指令加 --kit-guard / --budget none / --no-ik-guard）
# 任何 4.3 檔對預算：每個關節每一格的角度、hold／transit 判定、摺疊數
python3 -m spinegen.budget_motion $V/monkey_joint/monkey_joint.motion.json $V/monkey_joint/monkey_joint.atlas \
    --budget $R/monkey_joint/budget.json --joints $R/monkey_joint/joints.json --loops idle,celebrate
```

## B5 預算與 IK 保護（`spinegen/budget_motion.py`、`bridge_motionkit` 3b／3c、kit 的 `boneAmplitude`／`ikGuardChains`）

**量法**（`budget_motion`）：在 4.3.13 播（60 fps、physics 開，和 G1 一樣），每個關節的角度 = 關節骨相對於關節帶 `upper` 骨的世界旋轉，
減掉 setup 的同一個值（就是預算掃描轉的那個局部角；腳掛在 IK 目標下也照樣量小腿對腳）。超過 θ_soft 連續 0.1 秒以上算「停在那裡」（hold，
循環頭尾相接），任何一格超過 θ_hard 算 transit 違規，加上 G1 摺疊數。

**縮幅度**（`fit_budget`）：關節分組：手臂 IK 鏈上的肩、肘 → 那隻手的拳頭目標；其他（髖、膝、踝：腳釘在地上，腿只會跟著身體彎）→ 身體（hip／chest 角色）。
每組一個幅度倍數（kit 新增的 `boneAmplitude`：這根骨的畫面軌和局部軌整條縮放），超出預算的組往下二分、沒超出的往回長，到 0.04 以內；
各組互相牽動（身體動、拳頭不動，手肘一樣會彎），二分完還是超出時，再在上面二分一個共同倍數（0 就是 setup，一定在預算內）。
`bridge.json` 的 `budget.fit` 每支動畫列出：被縮的關節、原因、倍數、縮前／縮後角度。手臂留不到 3/4 就寫一條補圖需求（`artreq.json`）：
限制是切圖接縫（rigid）就要關節的圖，否則要一隻畫成那個姿勢的手臂。

**IK 保護**（`guard_chains`，kit 新增 `ikGuardChains`）：kit 的保護在臂長 85% 以內不動、最多到 95%。猴子的手臂畫得幾乎打直（手腕在臂長 99.8%），
目標又為了 softness 往外推，所以靜止時就在 95% 外面：kit 的保護從第 0 格就把手拉進來。現在每條鏈：knee = max(85% 臂長, setup 的目標距離)，
max = knee 往「softness 解完全打直的距離（臂長 + softness）」走 3/4。左手 knee 330.3、max 337.3、打直 339.6；右手 338.2／348.0／351.3。
檢查（`rest_probe`）：第一支預設幅度 0 播第 0 格對 setup。

猴子（30／60 fps，4.3.13）：

| | joint 之前（kit 保護、沒有預算） | **joint B5** | rigid 之前 | **rigid B5** |
|---|---|---|---|---|
| kit 分數 idle／punch／celebrate／hit | 100／100／55（9 警告）／100 | **100／100／100／100** | 100／100／55／100 | 100／100／55（9 警告，helmet）／100 |
| qa G1（單格最多／摺疊三角形・格） | 8／317 | **0／0** | 0 | 0 |
| 預算：超出的關節 | celebrate 兩肘（+171、−168；hard 114、96） | 無 | 每支動畫：肘、膝，punch／celebrate 加肩 | 無 |
| 被縮的組（倍數） | — | celebrate：左手 0.41、右手 0.53；其他 1 | — | idle 左 0.08／右 0／身體 0.13；punch 0.25／0／0.18；celebrate 0.005／0／0.13；hit 0.26／0／0.12 |
| 幅度 0 的第 0 格對 setup | **56.7 px** | **0.000 px** | 58.3 px | 0.000 px |
| 第 0 格對 setup（實際動畫） | idle 58.9 px、punch 56.8 px | idle **39.6 px**、punch **0.00 px** | | |
| 手肘第 0 格（idle 左／右、punch 右） | 39.8°／−32.0°、−32.2° | **23.9°／4.2°、0.0°** | | |
| celebrate 手肘範圍 | 左 130…171°、右 −168…−120° | **左 56…85°、右 −87…−74°**（soft 96、90） | | |
| 其他 qa 4.3.13 | L1–L9、G3、M1（0 px）、P3 過；P1 不過 | 同左；P1 16.4 px（punch 右袖口；之前 15.8） | 同左，P1 15.8 px | **全過**（P1 1.2 px：幾乎不動） |

（「之前」：同一副新 rig，用 `--kit-guard --budget none` 跑，`$W/v3/exp/{joint,rigid}_kitguard_nobudget`；只換保護 `exp/joint_nobudget`、只開預算 `exp/joint_kitguard`。）
各自的效果：**預算**單獨就把 celebrate 修到 G1 0、kit 分數 100（kit 保護或新保護都一樣）；**新保護**單獨讓 punch 第 0 格回到原畫（0.00 px）、idle 手肘第 0 格從
39.8°／−32.0° 變 23.9°／4.2°，kit 分數和 kit 保護一樣（idle 100）；完全不加保護 idle 掉到 90（2 警告）。

**idle 的第 0 格還是離 setup 39.6 px**：這不是保護（幅度 0 時 0.000 px，關掉保護也一樣 39.6 px），是預設本身的相位：第 0 格是髖最低（下沉 13.5 px）、
兩拳各在自己的相位上；整個循環最接近 setup 的一格也還差 15.5 px（t = 0.65 s），所以換起點也做不到 < 0.5 px。要 idle 從原畫開始，得在預設加「從靜止淡入」
的開頭（不是循環的一部分）或讓循環繞 setup 走，這是 kit 的事。

**rigid 的預算幾乎什麼都不讓動**：切圖的肘 2–12°、膝 2–8° 就開縫（G4 seam），所以 idle／punch／hit 的手臂只剩 0–26%，身體 12–18%。這是對的答案
（rigid 的圖撐不住這些動作），補圖需求寫的是「關節要有重疊的圖，或改用 joint 模式」，7 條。rigid celebrate 的 9 個警告全在 helmet：身體幾乎不動時，
「典型加速度」只剩 156，helmet 的 runtime physics 小抖動就超過倍數門檻；分數是相對值的副作用，不是看得到的頓挫。

**celebrate 現在是什麼**：雙拳舉到皮帶高度（左 0.41 × 抬 180 px ≈ 74 px、右 0.53 × 180 ≈ 95 px，上下各再 ±15–20 px），手肘往外彎 56–87°，
身體照原本彈 18 px：看起來像「雙手扠腰在彈」，不是慶祝。要真的舉手，art request 已經寫出來（`$W/v3/monkey_joint/artreq.json`，左右各一條：
一隻畫成舉高、彎肘、拳頭過肩的手臂，celebrate 時換上）。

## B1 `spinegen/emit/spine43.py`

和 4.2 輸出共用 `spine42.prepare()`（hull 在前、從沒跑過的約束寫成不作用、順序重排成連續），所以兩個檔的頂點順序相同，可以一對一比。
4.3 的寫法：一條 `constraints[]`（照 IR 求值順序，沒有 `order`）、transform 用 `source / localSource / localTarget / additive / properties`、
IK 用 `scaleY`、path 用 `slot`、linked mesh 用 `source`、physics 每個欄位都寫（IR 沒寫的填 4.3 預設並記在 report）。
半角輔助骨用試驗驗證過的寫法（`properties.rotate.to.rotate.scale = mix`、`mixRotate 1`），另加 `offset = (1−mix) × 輔助骨 setup 角`，
意思才和 IR（4.2 的局部絕對混合）完全一樣；有動畫 key 到輔助骨時改用直接寫法。

30 fps、每支動畫每一格：

| 骨架 | physics none | physics update（4.3 − 4.2） |
|---|---|---|
| 猴子 joint（641 格） | **1e-6 px** | 最大 4.90 px（throwit，袖口）；其他動畫 0.02–4.07 |
| 猴子 rigid（641 格） | **0 px** | 頂點 0 px（沒有圖掛在 physics 骨上），骨矩陣差 0.142 |
| 典獄長（128 格，經 `spine42_reader`） | **0 px** | 4.49 px（reactBig，radio_cord）；60／120／240 fps 時 2.3／2.1／2.2 px |

physics 的差不是參數：兩邊欄位都明寫了。4.3 的 physics 會在固定步長之間內插（`xLag`、`rotateLag`），4.2 直接顯示最後一步，
所以 4.3 的跟隨晚最多一個 physics 步長；30 fps 時一幀兩步，差最大。

4.3 不會照 4.2 播的寫法，emitter 會逐條警告並列出受影響的骨（`report["differs"]`）：4.2 局部絕對縮放除以骨自己的縮放、
世界旋轉 offset 超過 ±180°、世界 shearY offset、世界相對 shearY（4.2 先 wrap）、以及「寫世界矩陣的約束之後又有約束移動它的祖先」
（非均勻縮放或特殊 inherit 時兩版不同）。合成的 features 骨架裡這些全都有；被標記以外的骨，兩版差 ≤ 8.8e-7 px。
spinegen 自己產生的骨架（IK → 半角 → physics）一條都沒有。

## B2 QA 在 4.3.13

`qa/run.mjs --runtime 4.2|4.3`：runtime 差異都包在 `qa/lib/rt42.mjs`、`rt43.mjs`，閘門只寫一次。4.3 跑 L1–L9、G1、G3、M1、P1、P3；
L1／L3／L9 套 4.3 的格式規則（頂層 4.2 約束、未知 type、4.3 條目裡的 4.2 鍵名、殘留的 `order`、同名約束、清單順序要等於 IR 求值順序、
沒有被 `properties` 驅動的 mix）。**C1 只在 4.2**：`spinegen.lbs` 是 4.2.120 的模型，4.3 時 skipped 並指向 4.2 輸出的 C1 與 equiv43。
`npm test` 87 個（4.2 原本 55 + 4.3 合成手臂一錯一閘門 32）。

猴子 joint 的純 4.3 輸出（原本的 9 支動畫）：除了 P1 全過。P1 在 4.3 是 5.64／2.72／1.01 px（30／60／120 fps，門檻 3.81／1.90／0.95），
4.2 是 19.1／12.1／0.94：4.3 的內插讓 fps 影響小很多，但來源骨架的袖口 physics 還是太敏感。

## B3 `spinegen/bridge_motionkit.py`

1. 輸出時才加手臂 IK：拳頭目標 `ik_hand_l/r` 掛 root；兩骨 IK 排在腿 IK 之後、半角輔助骨之前；彎的方向取 setup 手肘的方向。
   **softness = 0.1 × 手臂長（30.9／31.9 px）**。猴子的手臂畫得幾乎打直，softness 一加，解出來的手會停在手腕前面；
   做法和 kit 自己的 autorig 一樣（`motionkit/src/autorig.ts` 的 `softTarget`）：把目標沿「肩 → 手腕」推出去（22.3／20.2 px），
   讓 softness 解出來剛好落在畫的手腕上。上一版說「只能把 softness 壓到 0.70／1.35 px 才不會動到 setup」，**這個理由是錯的**，已改掉。
2. **腿 IK 也加 softness**：0.15 × 腿長（kit 的 `LEG_SOFTNESS`），rigbuild 原本只給 0／3.56 px，現在 36.0／37.6 px，
   目標推出 36.0／18.0 px，掛在目標下的腳骨反向補回，腳還在原位。目標上有圖、或有別的約束讀它時不推（留原值並寫 note）；
   每次推完都檢查 setup，超過 0.01 px 就退回。
   setup 有沒有動：lbs 3.0e-5 px；4.3.13 實際播放，和沒加任何東西的 4.3 輸出比頂點 3.6e-5 px（不加 softness 時也是這麼多，是解 IK 的捨入）。
3. 角色從模板角色來：hip/body = `hips`、chest = `torso`、head、fists（facing 方向較前的當 fistFront）、shoulders、shoulderBack。
   7 個配件已經有 runtime physics，不交給 kit 的頭髮烘焙（不然會動兩次），所以沒有 hair。size = 1.5（kit 自己估的）。
4. 原本的 9 支動畫丟掉：它們是 FK key，手臂 IK mix 1 會把手釘住。

預設（手 0.1、腿 0.15、kit 的 IK 保護開）。joint／rigid 的 kit 分數相同：

| | idle | punch | celebrate | hit |
|---|---|---|---|---|
| kit 流暢度分數（上一版） | **100**（80，4 警告） | 100（100） | **55，9 警告**（35，13） | 100（100） |
| 手肘：第 0 格／最大（相對 setup） | 40°／41°、32°／33° | 右 **32°**／75° | 130°／171°、123°／168° | 0°／12° |
| 膝蓋最大（上一版） | 10°、13°（35°、25°） | 5°、8°（26°、17°） | 15°、15°（44°、28°） | 10°、17°（34°、20°） |
| 小腿末端離腳：不夠長／穿進靴子 | 12.0／**10.2** px | 15.9／5.5 | 17.5／**15.3** | 16.5／9.0 |
| 藝術閘門不過的格數（joint vs rigid） | 43／61 vs 61／61 | 18／26 vs 26／26 | 37／37 vs 35／37 | 4／27 vs 8／27 |

上一版「穿進靴子」全是 0（硬 IK 夠得到就一定到）。qa（4.3.13）：兩版 L1–L9、G3、M1（0 px）、P3 都過；joint 的 G1 有 16 個摺疊
（celebrate 右前臂），rigid 0；P1 兩版都不過（15.8 px，punch 右袖口）。

各選項（joint，`bridge.json` 在 `exp_soft/`）：

| 手 softness／腿 softness／IK 保護 | idle | celebrate | idle 手肘第 0 格 | punch 右手肘第 0 格 |
|---|---|---|---|---|
| 0.1／0.15／開（預設） | 100 | 55 | 40°、32° | 32° |
| 0.1／0.15／關（`--no-ik-guard`） | 90（2 警告） | 55 | 24°、4.5° | **0°** |
| 0.1／原值／開（`--leg-softness 0`） | 80（4） | 30（14） | 40°、32° | 32° |
| 0／0.15／開（`--arm-softness 0`） | 100 | 65（7） | 49°、32° | 33° |
| 上一版（0.70／1.35 px，原值，開） | 80（4） | 35（13） | 49°、32° | 33° |

kit 的警告分兩種：腿的（臀部上下時膝蓋「啪」一下伸直）加了腿 softness 就全部消失；剩下的都在 celebrate：手肘折 170° 的加速度尖峰，
再加手 softness 帶出的 prop、cuffR 兩個。IK 保護會把幾乎打直的手臂先拉到 95% 臂長以內，所以 idle 和 punch 第 0 格就不是原畫；
關掉保護第 0 格比較接近原畫，但 idle 掉到 90，punch 的出拳手臂幾乎打直（最大彎 12°）。預設維持 kit 的保護。
手 softness 的代價：kit 的拳頭軌跡現在從推出去的目標開始算，看得到的拳頭位置和上一版差最多 11 px（idle／punch／hit）、22 px（celebrate）。

## 看圖 B5（`$W/v3/sheets/*.png`，4.3.13 的世界頂點直接畫）

藝術閘門不過的格數（`--all-frames`，30 fps）：joint idle 30／61、punch 3／26、celebrate 35／37、hit 5／27（之前 43、18、37、4）；
rigid 8／61、1／26、4／37、3／27（之前 61、26、35、27：rigid 現在幾乎不動）。

- **celebrate**（joint）：不再摺疊。兩拳在皮帶高度，手肘向外彎，最高和最低兩格幾乎一樣：像扠腰彈跳，不像慶祝。看得到的毛病都是已知的圖：
  手裡的綠色褲子碎片、腋下軀幹圖裡的紅色袖子碎片和深色楔形。35／37 格不過：G5 洞 1–4 個（彎出去的手臂和軀幹圍住的背景，和 P2 蹲下時手和大腿圍住的一樣，
  不是破圖），以及 G2 腳踝面積 0.87（腿 softness 讓小腿穿過靴口 15 px，見膝蓋的取捨）；沒有摺疊、沒有接縫。rigid：幾乎靜止。
- **idle**（joint）：雙手垂在畫的位置附近（之前被保護收到腰邊），身體上下彈；最高那格手邊有小片綠色碎片。rigid：幾乎靜止。
- **punch**（joint）：蓄力那格就是原畫的垂手（之前右手肘一開始就彎 32°）；出拳那格右臂往旁邊舉 41°、手肘彎 39°，拳頭在腰側前方，還是「側舉」不是「直拳」，
  右腋下的深色楔形還在（預算沒擋：肩 +41° 在 soft 60° 以內，楔形是軀幹圖裡塗的陰影，art request 早就列了）。rigid：右手完全不動（倍數 0），左手 25%。
- **hit**（joint）：身體往後縮、頭低下，正常；右手邊一小片綠色碎片。

## 看圖 B3（`sheets/*.png` 每支動畫 rigid／joint；`compare/*.png` 是上一版、預設、關保護的同一格並排）

- **腿 softness 的取捨**（`compare/idle_legs_capped_vs_soft.png`、`celebrate_legs_capped_vs_soft.png`）：臀部往下時，上一版膝蓋確實彎下去
  （celebrate 最低那格是 O 型腿蹲下），現在腿幾乎是直的，褲管往下蓋住靴口 10–15 px。這是 softness 的數學：腿畫成全直時，
  推出去的目標剛好在 softness 曲線的平端，臀部一開始下降，膝蓋幾乎不跟著彎（左腿起步時完全不跟，右腿只跟 31%）。
  沒有斷開、沒有接縫，但「蹲」的感覺不見了。臀部往上那格兩版一樣（腿不夠長，褲管離開靴口 12–18 px，被靴子的領口蓋住）。
- **IK 保護**（`compare/idle_arms_guard.png`、`punch_arms_guard.png`）：開著時第 0 格雙拳被收到腰邊；關掉時手臂垂下、接近原畫。
- **punch**：還是往旁邊舉到水平，不像出拳；joint 的手肘是連續的弧，rigid 的手肘斷開（G4 195 px）；右腋下的深色楔形還在。
- **celebrate**：還是不能用：兩骨 IK 把前臂往上折 170°，joint 皺成一團（G1），rigid 只剩碎片。
- **idle**／**hit**：joint 可以接受；rigid 手肘有缺口。手裡的褲子碎片、左腰的紅色碎片照舊（補圖需求）。

## 還沒解決

1. **physics 兩版不同**，最多 4.9 px（30 fps）；60 fps 以上約 2 px。這是 4.3 runtime 的行為，emitter 改不了。來源骨架的袖口 physics 在 4.2、4.3 都過不了 P1。
2. **C1 只有 4.2**：4.3 檔案靠 4.2 輸出的 C1 加上 equiv43（只在 physics none 時證明相同）。
3. **膝蓋：沒有兩全的銜接器設定**。腿畫成全直時，硬 IK 蹲得下去但會啪一下伸直；softness 不會啪，但蹲不下去、褲管蓋進靴子 10–15 px。
   要兩個都有，得在角色上把膝蓋先彎 5–15°（kit 的交付規格也這樣寫）。臀部上升時 12–18 px 的「腿不夠長」兩種設定都一樣，也是角色的事。
4. **預設和這個角色對不上**（B5 後）：celebrate 照預算縮成扠腰彈跳（G1 0），真的慶祝要補一隻舉高的手臂（artreq 已寫）；出拳還是側舉，要給垂手角色的預設。
   保護造成的第 0 格彎曲已經沒了（幅度 0 時 0.000 px、punch 第 0 格 0.00 px），但 **idle 第 0 格離原畫 39.6 px**：預設的相位，循環裡最接近也差 15.5 px，
   要 kit 加「從靜止淡入」的開頭。
8. **預算是一次一個關節**：膝蓋、腳踝各自都在預算內，但腿 softness 讓小腿穿過靴口（celebrate 15 px），G2 腳踝面積 0.87 在 joint 的 sheets 上一直不過；
   `budget_motion` 只看單關節角度和 G1，沒看組合造成的 G2／G4。要嘛把 art_gates 的組合判定接進 fit，要嘛先解膝蓋（第 3 點）。
9. **rigid 的預算讓預設幾乎不動**（手臂 0–26%、身體 12–18%）：數字是對的（切圖關節 2–12° 就開縫），但這樣的 rigid 輸出沒有用；rigid 要動就要補關節的圖。
   另外 rigid celebrate 的 kit 分數 55 是相對門檻的副作用（身體幾乎不動時 helmet physics 的小抖動算成頓挫）。
10. joint 預算裡兩個肘的 hard_by 是 `hole@None`（`spinegen.budget` 的 G5 洞沒有對到 slot 名稱）；只影響標籤，不影響角度。
5. rigbuild（另一條線在改）給 4.2 輸出的腿 softness 還是壓在 slack 以下；銜接器只在輸出 4.3 時補上。rigbuild 若改用「推目標」，4.2 也會有。
6. 正面角色的 `facing` 是隨便選的（`--facing 1`；`--facing -1` 也能跑，idle 90、celebrate 70，前後拳互換）。銜接器會丟掉骨架原本的動畫；要保留，得在那些動畫裡把手臂 IK 的 mix key 成 0。
7. 補圖需求還在：手裡的褲子碎片、腋下的陰影、軀幹圖裡的袖子顏色。

測試：`python3 -m pytest tests -q` 273 個、`cd qa && npm test` 87 個、`cd motionkit && npm test` 46 個，全部通過。
B5 新增：銜接器 5 個（保護門檻、hold／transit／循環接頭判定、分組、補圖需求的原因、端到端用合成預算把 celebrate 縮進預算），kit 2 個（`ikGuardChains`、`boneAmplitude`）。
