# 實驗（SOTA.md 附錄 [E1]–[E11]）

研究 workflow 跑的小實驗，原樣保留以便重現。全部是我們自己的程式；Spine runtime 由 npm 安裝（devDependency，需有 Spine 授權者安裝），不進 repo。

| 代號 | 檔案 | 量什麼 |
|---|---|---|
| E1 | `py/elbow.py` | 合成手臂：LBS、半角輔助骨、BBW 的翻面與面積（需 `triangle`、`libigl`；`triangle` 僅限內部原型） |
| E2 | `py/meshcmp.py` | CDT 與 22 px 格網的頂點數與 overdraw（讀 Hard Time 的 warden/prisoner） |
| E3 | `spine42/probe.mjs`、`spine42/qa_probe.mjs` | spine-core 4.2.120：摺疊、physics 對 fps、決定性 |
| E4 | `spine42/order.mjs` | 約束 `order` 缺號時不執行 |
| E5 | `spine43/t.mjs` | 4.2 檔在 4.3 runtime 下掉約束或拋錯 |
| E6 | `spine42/phys0.mjs` | 0 長度骨的 rotate physics |
| E7 | `py/exp1.py`、`py/exp2.py` | 典獄長 `fore_r`：31 個 30 fps 線性 key 對 5 個解析 bezier key |
| E8 | `py/exp3.py` | Cartoon Animation Filter 的對稱性 |
| E9 | `py/exp4.py` | 二階動態 (f, ζ, r) |
| E10 | `py/exp5.py` | Spine physics 的 (f, ζ) 換算 |
| E11 | `spine42/foldtest.mjs` | 摺疊偵測器的健全性測試 |

```bash
cd research/experiments/spine42 && npm install && node foldtest.mjs <skeleton.json> <skeleton.atlas>
```
