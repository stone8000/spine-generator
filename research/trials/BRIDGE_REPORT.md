# 銜接報告：spinegen → Spine 4.3 → motion kit → 4.3 裁判（2026-10-22）

定案：**Spine 4.3 是主要目標**，4.2 留給舊遊戲。這一輪做了四件事：IR 的 4.3 輸出、QA 換成 4.3.13 也能跑、
spinegen 骨架接上 motion kit 的預設動作、看圖。

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
```

輸出都在 `/home/user/spinegen-work/bridge/`（不進 repo）。

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

1. 輸出時才加手臂 IK：拳頭目標 `ik_hand_l/r` 掛 root、放在前臂末端；兩骨 IK 排在腿 IK 之後、半角輔助骨之前；彎的方向取 setup 手肘的方向。
   猴子的手臂畫得幾乎打直（setup 只剩 0.70／1.35 px 的餘裕），softness 想要 31 px，為了不動 setup 只能給 0.70／1.35 px。setup 移動 3e-5 px。
2. 角色從模板角色來：hip/body = `hips`、chest = `torso`、head、fists（facing 方向較前的當 fistFront）、shoulders、shoulderBack。
   7 個配件（香蕉、帽子、口袋、袖口、褲管）已經有 runtime physics，不交給 kit 的頭髮烘焙（不然會動兩次），所以沒有 hair。size = 1.5（kit 自己估的）。
3. 原本的 9 支動畫丟掉：它們是 FK key，手臂 IK mix 1 會把手釘住。

| | idle | punch | celebrate | hit |
|---|---|---|---|---|
| kit 流暢度分數（joint／rigid 相同） | 80（4 警告） | 100 | **35（13 警告）** | 100 |
| 手肘最大彎（相對 setup） | 53° | 76° | **168–171°** | 30° |
| 肩膀最大 | 14° | 49° | 15° | 6° |
| 腳踝離開靴子（脛骨尖到腳） | 11.6 px | 16.0 px | 17.5 px | 16.6 px |
| 藝術閘門不過的格數（joint vs rigid） | 42／61 vs 61／61 | 22／26 vs 26／26 | 37／37 vs 35／37 | 5／27 vs 11／27 |

qa（4.3.13）：兩版 L1–L9、G3、M1（0 px）、P3 都過。joint 的 G1 有 16 個摺疊（celebrate 右前臂），rigid 0；兩版 P1 都不過（16.1 px，punch 的右袖口）。
藝術閘門（每格、4.3 runtime 的頂點）：joint **沒有任何 G4 接縫**，不過的是 G5 小洞（每格最多 9 個）、G2 面積（右腳踝 0.86、手肘 0.92，
celebrate 手肘 0.79）、celebrate 的 G1；rigid 的 G4 接縫最大 196 px（punch 右肘）、188（celebrate 右肘）、55（idle 左肘）、膝蓋 9–22 px、
右肩 79 px，另有 G6a 18–36 px、G6b NCC 0.39–0.50。kit 的警告幾乎都是同一件事：腿和手臂畫得太直，臀部往上或拳頭拉近時膝蓋、手肘
「啪」一下伸直或折起（脛骨、前臂的加速度尖峰）；celebrate 另有一個褲管 physics 骨。

試過但沒有採用：關掉 kit 的 IK 保護，idle 變 20 分（16 警告）；腿 IK 加 stretch，kit 的警告完全一樣。

## 看圖（`sheets/*.png`，每支動畫 rigid／joint 兩列，另有手臂、腿的 2 倍放大）

- **punch**：猴子是正面角色，「往前」就是往畫面右邊，所以右手是往旁邊舉到水平（肩 49°），不像出拳。兩版右腋下都露出一塊深色楔形（軀幹畫在手臂下的陰影），
  袖子上緣在領口附近裂開一條。rigid 的手肘斷成兩根直棒、接縫缺口很明顯；joint 的手肘是連續的弧，好很多。
- **celebrate**：這個角色不能用。lift 120 × 1.5 只把垂下的拳頭抬到胸口，兩骨 IK 只能把前臂往上折 170°。rigid 的手藏到袖子後面，只剩碎片；
  joint 的前臂和手在袖子前面皺成一團（G1）。垂手的角色要的是「肩膀舉起」的舉手動作，還要另畫舉手的圖，超過 P2 量到的肩膀預算。
- **idle**：joint 看起來可以接受：手肘彎得圓順、蹲下時膝蓋往外。rigid 的手肘有缺口。但第 0 格就不是原畫的姿勢：IK 保護把幾乎打直的手臂拉到 95% 以內，
  拳頭被收到腰邊。
- **hit**：被打退的感覺有出來。rigid 的褲管離開靴子（16.6 px）；joint 的褲管跟著拉長，看不出斷。
- 兩版都有、跟骨架無關的：手的圖裡帶著褲子碎片，手一離開大腿就露出綠色三角形（P2 的補圖需求）；左腰下方露出紅色碎片，
  是軀幹圖裡被手臂蓋住的袖子顏色。

## 還沒解決

1. **physics 兩版不同**，最多 4.9 px（30 fps）；60 fps 以上約 2 px。這是 4.3 runtime 的行為，emitter 改不了。來源骨架的袖口 physics 在 4.2、4.3 都過不了 P1。
2. **C1 只有 4.2**：4.3 檔案靠 4.2 輸出的 C1 加上 equiv43（只在 physics none 時證明相同）。
3. **預設和這個角色對不上**：celebrate 會摺手；出拳變側舉；腿和手臂太直，臀部上升就留下 12–18 px 的腳踝缺口、膝蓋會啪一下伸直；
   idle 一開始就不是原畫姿勢。要改的是角色（膝蓋、手肘先彎一點）或給垂手角色的預設，不是銜接器。
4. 正面角色的 `facing` 是隨便選的（`--facing 1`）。
5. 銜接器會丟掉骨架原本的動畫；要保留，得在那些動畫裡把手臂 IK 的 mix key 成 0。
6. 補圖需求還在：手裡的褲子碎片、腋下的陰影、軀幹圖裡的袖子顏色。

測試：`python3 -m pytest tests -q` 240 個、`cd qa && npm test` 87 個、`cd motionkit && npm test` 44 個，全部通過。
