# 範例角色

用來驗證「圖層 → 自動建骨架 → 套動作」整條流程的角色，也可以直接拿來在工作室試「從圖層建立角色」。

| 角色 | 來源 | 重點 |
|---|---|---|
| [`bandit-monkey/`](bandit-monkey/) | AI 生圖的真實交件（14 層） | 自訂疊圖順序（`layers.json`）、手動關節、慶祝限制版參數 |
| [`boxer/`](boxer/) | 照規格用程式畫的測試角色（16 層，含頭髮） | 完全不手動調整就跑得出 6 個動作，用來抓求解器的 bug |

渲染腳本在 [`scripts/trial/`](../../scripts/trial/)：`render_gif.py dump` 把 PNG 轉成 alpha，`render_frames.ts` 用工具本身的程式建骨架、產生動畫、做流暢度與接縫檢查並算出每幀變換，`render_gif.py gif` 再用真實圖層合成 GIF，`sheet.py` 把 GIF 排成逐幀總覽。
