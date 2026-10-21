# spine-generator

目標：一套從角色美術自動產出 **Spine 4.2 骨架＋動作**的生成器，大動作也要流暢
（出拳、跳起歡呼、雙手舉過頭、往盤面衝），而且要能自己驗證「沒破、沒殘影、沒翻面」。

起點是兩套已經有的東西：

- `stone8000/hacksaw-character-motion`：香蕉猴產生器、自己寫的 Spine 播放器，以及 235 隻參考角色的動作包（研究用）
- `chenyuping1998/wp@c1ecdb9`：照 Iron Jaw（Xposed，三星）做法重做的 Hard Time 骨架產生器

## 進度

| 階段 | 狀態 | 入口 |
|---|---|---|
| 研究：量化基準、節拍表、外部最佳做法 | ✅ | `research/FINDINGS.md`、`research/beat_table.json`、`research/SOTA.md` |
| P1 裁判與輸出骨幹 | ✅ 6 項驗收全過，兩輪反查後修正 | `research/P1_REPORT.md`、`qa/README.md` |
| P2 讓圖撐得住（猴子現有的圖） | ⚠️ 5 項驗收：②③④ 通過，⑤ 部分通過，① 未過（剛性掃描比 README 手量嚴） | `research/P2_REPORT.md` |

P1 的東西：
- `qa/`：Node 裁判，精確鎖定官方 `@esotericsoftware/spine-core@4.2.120`（與 `spine-canvaskit@4.2.120`），
  `node qa/run.mjs check|dump`，實作 L1–L9、G1、G3、M1、P1、P3、C1 閘門。`qa43/` 用 4.3.13 檢查版本相容性。
- `spinegen/ir.py`（版本無關的中介表示）、`spinegen/spine42_reader.py`、`spinegen/emit/spine42.py`、
  `spinegen/lbs.py`（numpy 版 Spine 4.2 骨骼與蒙皮，C1 與官方 runtime 差 ≤ 5e-5 px）。
- `spinegen/oracle_check.py`（C1）、`spinegen/reemit_check.py`（讀進來再輸出，與原檔差 ≤ 1e-5 px）。

P2 的東西：
- `spinegen/mesh.py`（貼著圖的 CDT 網格、關節帶列、lean 設定）、`spinegen/weights.py`（bone heat 加分段 ramp）、
  `spinegen/rigbuild.py`（模板骨架、半角輔助骨、腿 IK、逐關節 rigid／ramp／helper）、`spinegen/ingest_spine.py`、
  `spinegen/atlas.py`、`templates/biped_mascot.json`。
- `spinegen/raster.py`、`spinegen/art_gates.py`（G1 G2 G4 G5 G6）、`spinegen/budget.py`（逐關節 θ_soft／θ_hard，
  加上 artreq 項目與對照表）。

```bash
cd qa && npm ci && npm test        # 55 tests
python3 -m pytest tests -q         # 215 tests（第三方檔案不存在時自動跳過）
```

## 目前的發現

細節見 [`research/FINDINGS.md`](research/FINDINGS.md)，這裡只列重點：

- 參考角色裡 ≥ 60° 的四肢大動作有 1474 段，其中 **74% 靠跨關節加權網格**、40% 會換前後順序、18% 會換圖，
  只有 17% 是剛性片硬轉。猴子的手和前臂是剛性片，所以肩膀上限只有 26°、手肘 22°。
- 猴子沒有 IK（腿不能動）、被擋住的地方沒有補畫，大姿勢也不會換圖：這三件事各自就能把動作幅度鎖住。
- Iron Jaw 的順暢靠三件事：骨架密（45–59 根骨、全加權網格、IK 釘腳、10–18 條 physics），
  關鍵幀少（待機每根骨 2–4 個 key，每往外一節晚 0.067 秒），
  反應有固定節拍（0.333 蓄力 → **0.1 出手** → 停到 0.667 → 1.0 收，0.2 秒混回全域時鐘）。
- Hard Time 已經照抄了結構，但動作還是小（囚犯四肢 3.4°），而且 key 是 30 fps 線性取樣，沒辦法再手調。

## 外部最佳做法與定案架構

完整版見 [`research/SOTA.md`](research/SOTA.md)：四個方向的研究、逐條來源查核，以及整合後的架構、動作規格語言、
33 個 QA 閘門與 P1–P8 實作計畫。下面是最早的提案，留作對照。

## 生成器架構（最早的提案）

```
美術（一張全身圖或分層 PSD）
  │
  ├─ A. 進件與補畫需求單：切線、關節、被擋住的部分要補哪些圖、大姿勢要另畫哪幾張（舉手、縮短透視的拳頭）
  ├─ B. 自動綁骨：關節從圖層幾何讀出；每層都做加權網格，關節處平滑過渡；
  │      腿兩節加膝目標 IK（釘腳）；會晃的東西自動找出來掛 physics 鏈
  ├─ C. 量動作預算：每個關節自動掃 0→90°，量破洞、面積比、翻面，得出每個關節「這張圖撐得住的角度」
  ├─ D. 動作合成：用規則合成，不手寫 key
  │      全域時鐘與相位、每往外一節的延遲、蓄力／出手／停／收的節拍、彈簧式 overshoot 與 settle；
  │      超出預算就自動排換圖或換前後順序；數值從 235 隻參考角色和 Iron Jaw 量出來
  │      → 擬合成少量 bezier key（可在 Spine 編輯器裡手調），不輸出 30 fps 取樣
  ├─ E. 閘門：官方 runtime 算骨骼；靜止時零破洞、零翻面、腳的位置誤差、循環接縫、殘影
  └─ F. 預覽：GIF、逐格接觸表、瀏覽器 viewer
```

## 路線圖

已改用 `research/SOTA.md` §7 的 P1–P8（每階段都有驗收數字）。下表是原本的 M 編號，對照用：

| 階段 | 內容 | 驗收 |
|---|---|---|
| M0 ✅ | 量化基準：`analyze_motion.py`、`big_moves.py` | 本文件與 `FINDINGS.md` |
| M1 ✅ | 從參考角色抽動作節拍：`beats.py` → `beat_table.json`（198 隻、1308 段）；曲線壓縮器 `spinegen/curves.py` | 節拍表、壓縮器測試 |
| M2 | 生成器核心 B：設定檔加分層圖，產出 Spine 4.2 JSON（加權網格、IK、physics） | 能重建 Hard Time 兩隻，數字一致 |
| M3 | 動作合成 D：規則加上 bezier 擬合 | 跟 30 fps 取樣比，誤差 < 0.5°，key 數減少 10 倍以上 |
| M4 | 動作預算 C：自動掃角度，超出預算時排換圖或換順序 | 猴子在既有圖上得到跟手量一樣的 26°／22° |
| M5 | 補畫需求單 A：自動產出切線圖、補畫提詞、大姿勢另畫清單 | 猴子重切，雙手舉過頭的 cheer 肩膀 ≥ 90° 且零破洞 |
| M6 | 閘門 E 與預覽 F 整合成一條指令 | 一條指令從美術跑到可以出貨的 Spine 加 QA 報告 |

## 跑分析

```bash
pip install numpy
python research/analyze_motion.py <skeleton.json 或資料夾> [--csv out.csv] [--anims]
python research/big_moves.py <packs 資料夾> [--min 60] [--json out.json]
```

⚠ 這個 repo 是 public。Hacksaw 和 Iron Jaw 的原始骨架、動作包、貼圖都**不要**放進來；`research/data/` 已經被 git 忽略。
