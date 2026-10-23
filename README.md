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
| P2 讓圖撐得住（猴子現有的圖） | ⚠️ 5 項驗收：②③④ 通過，⑤ 部分通過，① 未過（剛性掃描比 README 手量嚴）。③ 肘 96 / 92 / 98 / 90°（固定 G2 視窗，含單一三角形；之前 78 / 64 / 62 / 58），頂點 0.53 倍格網，手 0 px。深蹲 0.05–0.13H、重心左右 ±40 px：摺疊 137 → 22 → **0**（S3：帶內權重剪枝 0.005、`--fit-crouch` 逐腿挑膝蓋長度和腳踝 ramp 次方），頂點 0.52 倍格網。袖口／褲管跟著輔助骨（S1 配件複本）：左袖 physics 位移 17 → 54 px（原版 58），褲管 mix 按原版擺幅調低。腿的預算改用 IK 姿勢量（S2，`budget.json` 的 `ik`；最早的限制是左髖胯下的縫，0.02H）。審查 F1–F6、R1–R3 已修 | `research/P2_REPORT.md` |
| P3 補畫單（猴子） | ⚠️ 補畫單產生器完成（`spinegen/artreq.py`）：掃 249 個規劃姿勢的蒙皮遮擋者，猴子 24 項（22 要畫、2 請確認），每項有遮罩、截圖、中英提示詞；預期的碎片、腋下抹色與楔形、胯下的縫、肘窩收窄、celebrate／舉手換圖都在。自動補畫、姿勢表、`fist_front` 還沒做 | `research/P5_REPORT.md` 第一部分 |
| P5 第二隻角色（典獄長） | ⚠️ 生成器重建：頂點 2634 → 606、B2 0.996、帶過來的 4 支動畫 G1 0、左肘 6 → 102 / 98°、膝 IK 姿勢 95°、蹲 0.05–0.13H ±40 px 0 摺疊、physics limit 60/80 → 5000。改了 7 處一般性的程式（猴子 rig 逐位元不變）。未過：P1_FPS_ROBUST（limit 修好後 4.03 px）、寫實比例的蹲像 O 型腿、拿警棍的肘 setup 折 163° 只能剛性 | `research/P5_REPORT.md` 第二部分 |

P3、P5 的東西：
- `spinegen/artreq.py`（補畫單：規劃姿勢、露出／縫／擠扁／碎片／換圖、遮罩、截圖、提示詞、給美術的 README）。
- `research/p5_view.py`（原版與重建並排的 setup、動畫、強制姿勢圖）。

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
- `research/p2_sheets.py`（rigid／joint 對照表）、`research/p2_crouch_view.py`（蹲下的對照圖）：P2_REPORT 看圖用；
  `research/p2_band_variants.py`（改模板的關節帶形狀、重建、量肘或深蹲）：A1、A2 的變體表；
  `research/p2_crouch_grid.py`（spine-core 深蹲表）、`research/p2_physics_motion.py`、`research/p2_physics_view.py`（physics 帶動的頂點位移與看圖）。

```bash
cd qa && npm ci && npm test        # 55 tests
python3 -m pytest tests -q         # 259 tests（第三方檔案不存在時自動跳過）
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
