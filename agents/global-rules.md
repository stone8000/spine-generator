## 角色動作／Spine 動畫：用 spine-motion-kit

角色動作、Spine 4.3 動畫、角色圖層套動作的需求，預設用 `spine-motion` 指令（repo：{{KIT}}），不要從零手寫 Spine JSON。
最常用：`spine-motion character <交件資料夾>` → 輸出 Spine 4.3 JSON、報告.md、預覽.gif。
完整指引：{{KIT}}/AGENTS.md、{{KIT}}/skills/spine-character/SKILL.md

- **大盜猴是樣板角色**：說到大盜猴就要和 {{KIT}}/examples/characters/bandit-monkey/monkey.json 一模一樣（`npm test` 會檢查），不要改它來配合工具。
- 開新角色製程：先列要做的動作，再定方便拆圖做動作的生圖姿勢（docs/生圖交付規格.md 第 0 步），寫進需求單後才生圖。需求單從 {{KIT}}/docs/需求單範本.md 開始填。
- 大圖騰一律要有框，和小圖同款（同一款框、同樣大小）。
- 拆圖：部件重疊處不要描邊；拳套等手部道具疊在頭前面。
- 回覆 Stone 一律用繁體中文。
