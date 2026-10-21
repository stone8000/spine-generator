# 範例程式

| 檔案 | 內容 |
|---|---|
| `fighters.ts` | 兩側角色控制（Spine 4.3）：待機 track 0、反應 track 1、additive 呼吸 track 2、`impact` 事件、雙人錯開 0.17 s 出拳 |
| `reelMotion.ts` | 轉輪運動模型：反拉、加速、巡航、煞車、過衝、回彈，參數擬合自錄影 |
| `reelMotion.test.ts` | 9 個測試：停輪時間、停在整格、速度連續、對照實測曲線 |
| `測試結果.txt` | 最近一次測試輸出（9/9 通過，含模型 vs 實測逐幀對照） |
| `型別檢查結果.txt` | 對 spine-pixi-v8 4.3.13 + pixi.js 8.22.0 的型別檢查（0 錯誤） |

## 執行

需要 Node.js 22.18 以上（可直接執行 TypeScript）。

```bash
npm install
npm test
npm run typecheck
```

## 接到遊戲裡

- `fighters.ts` 的 `setupFighters()` 示範完整接法；`reelEvents`、`fx` 換成你們自己的轉輪與特效模組。
- `reelMotion.ts` 是純函式、不依賴 Pixi，可以放進任何轉輪實作：每幀呼叫 `reelPosition(t, plan)` 取得捲動格數，乘上符號高度就是像素位移。
- Spine 素材命名約定：`spines/fighterL.skel`、`spines/fighterL.atlas`，動畫名稱 `idle`、`punch`、`breath`，事件名稱 `impact`。
