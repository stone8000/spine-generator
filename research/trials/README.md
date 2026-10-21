# 試驗：同一組圖層、同一副骨架、同一段動作，硬圖 vs 網格（2026-10-21）

## 做法

| 步驟 | 誰做 | 檔案 |
|---|---|---|
| 圖層 | motion kit 的合成拳擊手（`motionkit/examples/layers/good`，程式畫的，無版權問題） | — |
| 骨架 | motion kit `buildRig`（Spine 4.3），另加手肘、膝蓋的半角輔助骨 | `motionkit/scripts/trial/rig.ts` |
| 動作 | motion kit 預設：`fighter-idle-bounce`、`punch`、`celebrate`、`jump`、`hit-react`（流暢度檢查 0 錯 0 警告） | 同上 |
| 每一格的骨骼 | 官方 `@esotericsoftware/spine-core` 4.3.13 播放，IK、約束、physics 全算 | 同上 → `frames.json` |
| 附件 | 硬圖：motion kit 的整張畫布 region；網格：`spinegen.mesh.build` + `spinegen.weights.compute` | `research/trials/skin_trial.py` |

兩版只有附件不同。

```bash
cd motionkit && npm ci && node scripts/trial/rig.ts <work>
cd .. && python3 research/trials/skin_trial.py <work> motionkit/examples/layers/good
node motionkit/scripts/trial/verify-skinned.ts <work>     # 用官方 4.3 runtime 讀 skinned.json
```

## 結果

1. **半角輔助骨在官方 4.3 runtime 完全照設計運作**（`probe-half.ts`）：transform 約束
   `source: 前臂, localSource, localTarget, properties.rotate.to.rotate.scale 0.5`，前臂直接轉或被 IK 帶動，
   輔助骨都剛好在一半角度（誤差 0.00°）。這是 SOTA §8 第 1 項，之前只在 numpy 驗證過。
2. **`skinned.json` 能被官方 4.3 runtime 讀進來**：15 個網格、24 骨、8 約束、5 段動作；runtime 算的網格頂點與
   Python 蒙皮最多差 0.014 px（局部座標寫到小數 3 位）。
3. **第一版的錯，量出來了**：上臂靠肩膀那段的權重從胸口漸變到上臂、但肩膀沒有輔助骨。`celebrate` 上臂相對胸口轉超過
   100°，線性蒙皮把整段上臂擠成一團（最多 4 個翻面三角形）。修正：肩膀與髖部維持硬接，只有手肘、膝蓋做網格加輔助骨。
   修正後 5 段動作翻面三角形全是 0。
4. **在這個角色上，網格版沒有明顯贏過硬圖版。** 兩版關節破洞都是 0。這組圖是照「切件」設計的：每段兩端畫成圓頭、
   互相重疊，motion kit 的預設動作幅度也在安全範圍內。網格版的手肘彎成連續弧線，比較像真的手臂；硬圖版是兩根
   圓頭棒子接在一起，比較乾淨。前臂靠手肘那段輪廓有一點波浪，是網格版目前的瑕疵。

## 學到的事

- **網格不是一律比較好。** 該不該做網格，要每個關節量過才知道：
  - 「圓頭、重疊」的切件畫法，硬接就撐得住；
  - 「連續畫成一整塊」的畫法（袖子、外套、猴子那種一張圖切開的、真實的 AI 插畫），才需要網格和輔助骨。

  這正是 `spinegen.budget` 要做的事：每個關節量出硬接與網格各自撐得住的角度，自動選。
- 肩膀要做網格，就需要肩膀的輔助骨，或把漸變帶縮窄到不會跨到大角度。
- 下一個該試的角色，是「連續畫法」的真實插畫：猴子（第三方，只在本機研究），或照 `motionkit/docs/生圖交付規格.md`
  生的一張 AI 角色。

輸出（不進 repo）：`/home/user/spinegen-work/trial/`：`sheet_<動作>.png`、`zoom_arms.png`（第一版的錯）、
`zoom_v2.png`（修正後）、`metrics.json`、`skinned.json`。
