# 最強的 Spine 生成器：外部最佳做法與我們的架構（SOTA，2026-10-20）

**依據。** 本文依據四個方向的研究與逐條事實查核：Spine 格式、自動綁骨、動作、業界現況。另外用了本 repo 自己的量測：[`FINDINGS.md`](FINDINGS.md)、[`beat_table.json`](beat_table.json)、[`beats.py`](beats.py)、[`../spinegen/curves.py`](../spinegen/curves.py)。

**採信規則。**
- 查核判為 WRONG 或 UNSUPPORTED 的說法，一律改用查核後的更正版本。
- 只看到搜尋摘要、沒讀到原文的說法，標「未驗證」。
- 「本地量測」指我們自己的檔案或實驗，代號 [E1]–[E11]，完整路徑見文末附錄。

**限制。** 本輪共用的 WebSearch 額度已用完，所以沒有再補新的外部搜尋，只用研究包裡已查核過的資料。

---

## 1. 一句話結論

**最強的 Spine 生成器要做到四件事：**
- **以官方 runtime 當裁判。** 用版本釘死的 spine-core 4.2 逐格、逐混合點檢查。
- **把「圖撐得住多大角度」量成每個關節的預算。** 骨架用模板，配上跨關節加權網格、半角輔助骨、兩骨腿 IK，physics 用 (f, ζ) 指定。
- **動作由節拍文法產生。** 每個節拍邊界直接解析寫出一個 bezier key，不做取樣再擬合。
- **超出預算由同一份動作計畫依序處理。** 順序是：分攤、只經過、draw order、換圖、修正 deform、補畫單。只要任何一格出現破洞、翻面、殘影或腳滑，就不出檔。

---

## 2. 外部最佳做法總表

「我們目前」欄位的數字，除非另外註明，都來自 `FINDINGS.md`，或已查核的本地檔案。

| 環節 | 現在最強的做法 | 我們目前 | 差距 | 採用方案（演算法／套件／授權） | 來源 |
|---|---|---|---|---|---|
| **輸出格式與版本** | 目標版本當成建置參數，每個版本一支 serializer。<br>4.2 讀頂層 `ik`／`transform`／`path`／`physics`，四類共用一條 `order` 序列，而且必須 0..N−1 連續；有缺號的約束不會跑，也不報錯。<br>4.3 只讀 `constraints[]`（`type` 多了 `slider`）。4.2 檔丟進 4.3：沒有任何地方引用約束時，會靜默掉光約束；動畫或 skin 用名字引用約束時，直接拋錯。<br>runtime 不驗 `spine` 版本字串。<br>physics 預設值不同：4.2 是 inertia 1、damping 1，4.3 是 0.5、0.85。<br>atlas 的 UV 用 `size:` 宣告值算，不看 PNG 實際尺寸。 | Hard Time 寫 `4.2.43`。<br>猴子 `monkey.json` 宣告 `"spine":"4.1.20"`，卻帶 4.2 的頂層 `physics`（本地量測）。<br>沒有版本閘門。<br>播放器用 spine-pixi-v8 4.2.74。 | 版本寫錯也能安靜載入。<br>npm `latest` 已經是 4.3.13，不指定版本直接安裝就會裝到另一版。 | **預設輸出 4.2**（Iron Jaw、Hard Time、我們的播放器都是 4.2）。<br>QA 精確釘 `@esotericsoftware/spine-core@4.2.120`。<br>4.3 serializer 另外寫（第 8 階段）。<br>emitter 規則：明寫每個欄位；重新編號 `order`；寫 `inherit` 而不是 `transform`；明寫 `referenceScale`。<br>atlas 用自己的 packer；備選 libGDX TexturePacker，設 `legacyOutput=false`（Apache-2.0）。 | [SkeletonJson 4.2](https://github.com/EsotericSoftware/spine-runtimes/blob/4.2/spine-ts/spine-core/src/SkeletonJson.ts)、[SkeletonJson 4.3](https://github.com/EsotericSoftware/spine-runtimes/blob/4.3/spine-ts/spine-core/src/SkeletonJson.ts)、[Skeleton.ts 4.2](https://github.com/EsotericSoftware/spine-runtimes/blob/4.2/spine-ts/spine-core/src/Skeleton.ts)、[npm spine-core](https://registry.npmjs.org/@esotericsoftware/spine-core)、[TextureAtlas.ts](https://github.com/EsotericSoftware/spine-runtimes/blob/4.2/spine-ts/spine-core/src/TextureAtlas.ts)、[TexturePacker.java](https://github.com/libgdx/libgdx/blob/master/extensions/gdx-tools/src/com/badlogic/gdx/tools/texturepacker/TexturePacker.java)、[spine-versioning](https://es.esotericsoftware.com/spine-versioning)、[E4][E5] |
| **分件** | **分層 PSD**：讀 Spine 官方 PSD tag（`[bone]` `[slot]` `[skin]` `[mesh]` `[merge]` `[ignore]` `[folder]` `[trim]` `[overlay]` `[name:]` `[path:]`）。編輯器內建匯入支援的 tag 是 PhotoshopToSpine 的超集。<br>**單張圖**有三種工具，都不是「關節化、跨關節重疊」的分件：<br>・See-through：最多 23 類語意 RGBA 層，自帶補畫與 draw order；code 是 Apache-2.0；偏動漫風；類別裡沒有上臂／前臂之分。<br>・SAM 3：用文字或範例提示產生遮罩，授權是 SAM License。<br>・Qwen-Image-Layered：Apache-2.0，可以遞迴拆層，但不能指定部位。 | 猴子用手寫多邊形切 12 件（`cut_cast_layers.py`），切線剛好落在關節上。<br>Hard Time 用 PSD 層。 | 分件不自動。<br>相鄰部件不重疊。<br>不讀 tag。 | PSD 輸入：用 `psd-tools` 1.25（MIT）讀層與官方 tag。<br>單張圖：**SAM 3 遮罩加骨架引導的關節切線**。每段往父段延伸，重疊至少 1.0 倍局部肢體半寬。<br>See-through 只當動漫風的可選前端，借用它「用 depth K-means 分前後」的想法。<br>道具與特效用 Qwen-Image-Layered 拆。 | [PSD tags](https://github.com/EsotericSoftware/spine-scripts/blob/master/photoshop/README.md)、[Import PSD](https://esotericsoftware.com/spine-import-psd)（摘要）、[See-through](https://github.com/shitagaki-lab/see-through)、[SAM 3](https://github.com/facebookresearch/sam3)、[Qwen-Image-Layered](https://github.com/QwenLM/Qwen-Image-Layered)、[psd-tools](https://pypi.org/project/psd-tools/) |
| **補畫（被擋住的部分）** | 研究型 amodal 補全多半不能商用，或解析度太低：pix2gestalt 是 CC BY-NC 4.0、256 px、要 22–28 GB VRAM。<br>實務做法是遮罩式影像編輯：用 Qwen-Image-Edit-2511（以 20B MMDiT 為基礎，Apache-2.0）補遮罩內，貼回時只改遮罩內。<br>FLUX.1 Kontext [dev] 的模型只准非商用用途。<br>Iron Jaw 的做法是把每塊被擋住的部分都畫完整。 | 猴子用 `UNDERLAY = 26 px` 往下延伸補邊色。<br>Hard Time 手補了 8 張（P1–P4、W1–W4）。 | 補多大不是由動作算出來的，所以大動作一定露洞。 | **先做動作計畫，再由掃描求出遮罩。** 把每個遮擋者照「蒙皮後」的形狀，沿所有計畫中的動作和 IK 極值逐格光柵化，取聯集；減掉 rest 時的可見部分；再交上底層的合理範圍。結果就是 inpaint 遮罩。<br>Qwen-Image-Edit-2511 在本機跑，羽化 4–8 px 貼回。遮罩外 ΔE > 3，或 SAM 重切的輪廓不符，就拒收。<br>美術簽核後才進 rig。<br>這個掃描法是我們自己的方法，還沒驗證；必須掃「蒙皮後」的形狀，不能用剛性輪廓。 | [pix2gestalt](https://github.com/cvlab-columbia/pix2gestalt)、[Qwen-Image](https://github.com/QwenLM/Qwen-Image)、[Qwen-Image-Edit-2511](https://huggingface.co/Qwen/Qwen-Image-Edit-2511)、[FLUX.1-dev license](https://github.com/black-forest-labs/flux/blob/main/model_licenses/LICENSE-FLUX1-dev)、`FINDINGS.md` §3 |
| **骨架估計** | 人形插畫：SDPose（MIT code，自稱在 HumanArt／COCO-OOD 上最好；權重授權未載明）；DWPose／RTMW（Apache-2.0，可透過 rtmlib 使用）。<br>吉祥物：Molmo 2 指點（Apache-2.0）加 SAM 3 加 medial axis。<br>「模板骨架加擬合」比從零推論穩。Textoon 就是套在 Live2D 底模上。<br>沒有開源的 2D 任意角色綁骨器；RigNet、UniRig 都是 3D。 | 猴子靠圖層名前綴加手填的 `rig.json`。<br>Hard Time 是手設的。 | 沒有自動找關節。<br>沒有信心值，所以無法判斷「這個關節不可靠，不要做大動作」。 | **模板庫**：biped、chibi、quadruped、floating mascot。骨名、輔助骨、IK、physics 鏈和動作庫都事先驗證過，新角色只要擬合。<br>PSD 輸入：關節取相鄰層重疊區的中心，或讀 `[bone]` tag。<br>單張圖：SDPose 與 DWPose 兩者相差 ≤ 4% 身高才採用；否則投影到遮罩的 medial axis（scikit-image，BSD-3）。吉祥物改用 Molmo 2 指點。<br>提供人工疊圖修正介面，參考 Animated Drawings 的 joint-fix UI。<br>每個關節記錄信心值；信心低的關節不給大動作。 | [SDPose-OOD](https://github.com/T-S-Liang/SDPose-OOD)、[DWPose](https://github.com/IDEA-Research/DWPose)、[rtmlib](https://github.com/Tau-J/rtmlib)、[Molmo 2](https://github.com/allenai/molmo2)、[skimage.morphology](https://scikit-image.org/docs/stable/api/skimage.morphology.html)、[Textoon](https://github.com/Human3DAIGC/Textoon)、[UniRig](https://github.com/VAST-AI-Research/UniRig)、[Animated Drawings](https://github.com/facebookresearch/AnimatedDrawings) |
| **網格** | 輪廓追蹤，簡化，再做 constrained Delaunay 加品質細分（最小角、最大面積）。<br>本地量測 [E2]：CDT（eps 2 px、q28、a1500）比我們的 22 px 格網少約 60% 頂點（典獄長 2634 → 966）。透明 overdraw 從 5.8–51.9% 降到 0.6–9.7%，只漏掉 ≤ 0.7% 的不透明像素。<br>缺點是肢體太稀（上臂只有 36 點），關節處要另外加頂點列。 | Hard Time 用 `grid_mesh()` 22 px 格網，`hull: 0`。<br>猴子多數部件是 region。 | 格網的邊界點落在輪廓外，權重會被外插。<br>關節處沒有可以折的邊。<br>`hull` 是 0，編輯器無法正確顯示外框（未驗證）。 | 流程：OpenCV `findContours`，`approxPolyDP` 1.5–2 px，再用 **PythonCDT 2.0.2**（MPL-2.0：`refineTriangles` 插 Steiner 點，`eraseOuterTrianglesAndHoles` 處理洞）。<br>每個關節在一條帶內插 2–3 排垂直於骨軸的頂點列。帶寬是 1.0–1.5 倍局部半寬，半寬用 `distanceTransform` 量。<br>hull 頂點排在最前面，並寫入 `hull`。<br>Shewchuk 的 Triangle 只准內部原型使用（商用散布要作者授權）。 | [CDT](https://github.com/artem-ogre/CDT)、[PythonCDT](https://pypi.org/project/PythonCDT/)、[Triangle README](https://raw.githubusercontent.com/libigl/triangle/master/README)、[OpenCV contours](https://docs.opencv.org/4.x/d4/d73/tutorial_py_contours_begin.html)、[E2] |
| **權重** | Spine 4.2 runtime 只有線性混合蒙皮（LBS）。兩骨差 Δθ 時，50/50 的頂點會被拉向關節，係數是 cos(Δθ/2)；只要權重是 50/50，演算法改不了這點；但哪些頂點、多少頂點拿到這種權重，由演算法決定。DQS 和 Optimized Centers of Rotation 都要改 runtime 才能用。<br>形狀感知權重有兩種：<br>・BBW：libigl `igl.bbw`，MPL-2.0。據 Spine 開發者說，Spine 的自動權重源自 BBW，但他自己也說不確定哪些來自論文。<br>・Pinocchio bone heat：Blender 有參考實作，含 tie 規則、可見性測試、0.05／0.025 剪枝。<br>本地實驗 [E1]（numpy 模型）：<br>・半角輔助骨加分段權重：60°／90°／120° 都是 0 翻面，面積保留 0.98／0.97／0.94，外側半徑 1.00。<br>・BBW 留 44 px 自由帶：120° 是 0 翻面，但面積只剩 0.79；150° 還有 7 個翻面。 | Hard Time v2 的四肢，每個頂點只綁 1 根骨，等於剛性卡；跨肘、膝、肩的頂點數是 0。<br>`chain_weights` 有四個問題：用形狀外的歐氏距離；fade 固定 25–40 px；用 `x < midline` 硬切左右腿；權重保留到 1e-3。 | 參考角色有 74.3% 的大動作靠跨關節加權網格撐住，我們是 0%。 | **關節用半角輔助骨。** 每個肘、膝各加一根 `*_mid`，由 4.2 transform constraint 驅動：`local:true, relative:true, mixRotate:0.5, rotation: −子骨 setup 角`。`mixX`、`mixY`、`mixScaleX`、`mixScaleY`、`mixShearY` 全部明寫成 0（預設是 1，會把前臂位移加進來）。`order` 排在 IK 之後。<br>權重用**分段 ramp**：上段到輔助骨、輔助骨到下段，寬度 1.0–1.5 倍局部半寬。<br>**軀幹、尾巴、披風這類複雜形狀用 bone heat。** 2D 的可見性定義為「頂點到骨的線段整條留在圖層多邊形內」。`igl.bbw` 當備選。<br>最後剪到每頂點 ≤ 3 個 influence，門檻 0.02，再重新歸一。<br>超過 120° 的角度交給換圖或修正 deform。 | [Attachment.ts](https://github.com/EsotericSoftware/spine-runtimes/blob/4.2/spine-ts/spine-core/src/attachments/Attachment.ts)、[TransformConstraint.ts](https://github.com/EsotericSoftware/spine-runtimes/blob/4.2/spine-ts/spine-core/src/TransformConstraint.ts)、[Optimized CoR](https://la.disneyresearch.com/wp-content/uploads/Real-time-Skeletal-Skinning-with-Optimized-Centers-of-Rotation-Paper.pdf)、[libigl bbw.h](https://github.com/libigl/libigl/blob/main/include/igl/bbw.h)、[BBW paper](https://homes.cs.washington.edu/~jovan/papers/jacobson-2011-bbw.pdf)、[Spine forum BBW](https://uk.esotericsoftware.com/forum/d/3356-bounded-biharmonic-weights)（摘要）、[Blender meshlaplacian.cc](https://github.com/blender/blender/blob/main/source/blender/editors/armature/meshlaplacian.cc)、[Pinocchio](https://people.csail.mit.edu/ibaran/papers/2007-SIGGRAPH-Pinocchio.pdf)、[E1] |
| **約束與 physics** | Iron Jaw：45–59 根骨；兩腿 IK 加 stretch 釘腳；10–18 條 physics；待機只 key 9–13 根骨。<br>4.2 兩骨 IK：有 `softness`（接近伸直時減速，防膝蓋彈直）和 `stretch`。只有兩根骨都是 `inherit: normal` 時才生效；父骨沒有父骨會拋錯。<br>4.2 physics 是固定步長的半隱式 Euler 彈簧：`v += (wind − offset·strength)·massInv·dt; offset += v·dt; v *= damping^(60·dt)`。x、y、rotate、scaleX、shearX 五個權重預設全是 0。<br>0 長度骨上的 rotate physics 會亂甩 44–82°（本地量測 [E6]）。 | Hard Time：每段單骨 IK，共 4 條。physics `limit` 寫 60–80（預設是 5000）；inertia 0.5 時，骨速超過約 120 單位／秒就飽和，大動作的 follow-through 被掐掉。strength 沒有寫。<br>猴子：0 條 IK，7 條 physics（檔案卻宣告成 4.1.20）。 | 腿做不了大動作，腳也釘不住。<br>physics 被節流。 | **每條腿一條 2-bone IK。** 目標骨掛在 root；`softness` 是腿長的 5–15%；`bendPositive` 依左右設定；只在要拉長時開 stretch；用 IK `mix` 時間軸切換 FK 與 IK。手臂預設用 FK。<br>**physics 用 (f, ζ) 指定**，換算公式：`strength = mass·(2πf)²`、`damping = exp(−2ζ·2πf/60)`。例：strength 100、damping 0.85 對應 f 1.59 Hz、ζ 0.49，模擬過衝 19%（[E10]）。<br>其他 physics 設定：inertia 0.5；`limit ≥ 5000`；每根 physics 骨長 ≥ 0.02H；明寫 `referenceScale`。<br>**每往外一節的延遲用 key 時間平移來做。** Iron Jaw 的 0.067 s 是在待機手臂上用 key 做出來的，不是 physics。 | [IkConstraint.ts](https://github.com/EsotericSoftware/spine-runtimes/blob/4.2/spine-ts/spine-core/src/IkConstraint.ts)、[PhysicsConstraint.ts](https://github.com/EsotericSoftware/spine-runtimes/blob/4.2/spine-ts/spine-core/src/PhysicsConstraint.ts)、[SkeletonJson 4.2](https://github.com/EsotericSoftware/spine-runtimes/blob/4.2/spine-ts/spine-core/src/SkeletonJson.ts)、`iron-jaw-transcript.md`、`build_cast_spine.py` L92–133、[E6][E10] |
| **大姿勢：換圖與前後順序** | 參考角色的 1474 段大動作裡：40.2% 在動畫中改 draw order，18.0% 換圖，6.0% 用 deform key。<br>「由角度驅動修正」的做法：<br>・Spine 4.3 slider：用骨的轉角、位移、縮放或 shear 驅動一支動畫。<br>・Moho Smart Bones，14.4 版能匯出成 glTF morph target（未驗證）。<br>・Cartoon Animator 每個角度點存一組變形、換圖和前後順序（未驗證）。<br>・Live2D 的參數鍵形。 | 猴子：0 次 draw order key，2 次換圖。流程裡沒有任何一步會去要「舉手」那張圖。 | 雙手舉過頭、拳頭打向鏡頭，在現有流程裡做不出來。 | **姿勢表（pose table）。** 每個大姿勢一列，記錄：骨變換、修正 deform、換圖、draw order、有效角度區間。<br>動作合成時，角度一跨過門檻，就在同一時刻自動插入 attachment 與 drawOrder key。門檻有 ±5–10° 的遲滯；換圖放在 strike 段角速度最高的那一格。<br>4.2 烘焙成 key；4.3 後端改寫成 slider。<br>另畫的部件可以用 Qwen-Image-Edit-2509 的關鍵點條件生成（品質未驗證），再用關節關鍵點的相似變換對回骨架。 | `FINDINGS.md` §2、[4.3 CHANGELOG](https://github.com/EsotericSoftware/spine-runtimes/blob/4.3/CHANGELOG.md)、[Slider.ts](https://raw.githubusercontent.com/EsotericSoftware/spine-runtimes/4.3/spine-ts/spine-core/src/Slider.ts)、[Moho 14.4](https://digitalproduction.com/2025/11/14/moho-14-4-goes-on-a-side-quest-2d-meets-game-engines/)（未驗證）、[CA 角度點](https://manual.reallusion.com/Cartoon-Animator/Content/Resources/4.0/03_Actor/Head_Types/9_Angle_Points_Settings_for_360_Head.htm)（未驗證）、[Qwen-Image-Edit-2509](https://huggingface.co/Qwen/Qwen-Image-Edit-2509/blob/main/README.md)（摘要） |
| **動作來源** | **文字到動作：**<br>・NVIDIA Kimodo：code 是 Apache-2.0，SOMA 權重是 NVIDIA Open Model License；可加關鍵姿勢、末端、2D 路徑約束；30 Hz；文字編碼器是 LLM2Vec-Llama-3-8B。<br>・HY-Motion 1.0：授權排除 EU、UK、南韓，連輸出都不能在區外使用；MAU 超過 100 萬要另外申請。<br>・MDM、MoMask：程式碼 MIT，但訓練資料 HumanML3D／AMASS 是非商用。<br>**影片：** RTMW（rtmlib，Apache-2.0）抽 2D 姿勢；1€ filter（BSD-3）去抖；Animated Drawings 依部位分組選投影平面重定向（MIT，repo 已封存）。<br>這些來源產生的都是寫實的時間結構，不是卡通節拍。 | 猴子：手寫 key。<br>Hard Time：規則函式取樣。<br>已有 235 隻參考角色的節拍表。 | 沒有可以擴充的動作庫。<br>外部來源都要重新定時才能用。 | 三個來源，依序：<br>① **自有節拍文法，用節拍表當先驗。** 這是預設，全部自有。<br>② 錄影（動畫師自己演），依序經過：rtmlib RTMW、1€ filter、分組平面投影（frontal 或 sagittal 擇一）、重定時到節拍、擬合 key。<br>③ Kimodo 只當「參考姿勢產生器」，等法務確認 SOMA 權重與 Llama 3 的授權後才試用。<br>禁用清單：HY-Motion；SMPL 系（WHAM、4D-Humans、GVHMR）；用 HumanML3D 訓練的權重；LAFAN1 與 Bandai Namco（只准內部調參）。 | [Kimodo](https://github.com/nv-tlabs/kimodo)、[HY-Motion License](https://raw.githubusercontent.com/Tencent-Hunyuan/HY-Motion-1.0/master/License.txt)、[HumanML3D](https://github.com/EricGuo5513/HumanML3D)、[MoMask](https://github.com/EricGuo5513/momask-codes)、[rtmlib](https://github.com/Tau-J/rtmlib)、[1eurofilter](https://registry.npmjs.org/1eurofilter)、[AD retarget config](https://github.com/facebookresearch/AnimatedDrawings/blob/main/examples/config/README.md)、[GVHMR LICENSE](https://raw.githubusercontent.com/zju3dv/GVHMR/main/LICENSE) |
| **動作合成** | 參考 235 隻的節拍是**不對稱的**：<br>・react：0.20 s 到達最深蓄力（0.33 倍振幅），strike 0.167 s。<br>・big_win：0.367 s，0.51 倍。<br>・attack：strike 0.108 s，峰速中位 602°/s；**中位數的 attack 沒有蓄力**（windup 比 0.0023）。<br>・驅動骨幾乎不過衝（中位 0.003）。<br>・Iron Jaw `active`：0.333 蓄力，0.1 打出，停到 0.667，1.0 收回。<br>已知方法的限制與可用之處：<br>・Cartoon Animation Filter（LoG）在時間上對稱。實驗 [E8] 顯示 anticipation 等於 overshoot（0.40/0.40），做不出上面的不對稱節拍。<br>・二階動態（t3ssel8r）在 r<0 時的「預備」0.03 s 內就到底，是速度跳變，不是蓄力（[E9]；影片細節未驗證）。<br>・Holden 有精確的彈簧閉式解（MIT）。<br>・2D 的等體積規則是 sx·sy = 1。4.3 的 physics `ScaleYMode.Volume` 可以直接做。 | 猴子：Catmull-Rom 加 Fritsch-Carlson，每個 key 只有一條共用切線，不能斷切線。<br>Hard Time：30 fps 線性取樣，每秒 300+ 個 key。0.1 s 的出手只有 2 個內部取樣，偏離意圖曲線 5.04°（[E7]）。 | 節拍沒有寫成規則。<br>產生的 key 無法手調。 | **節拍圖。** 只 key 驅動骨：髖、胸、頭、上肢、IK 目標，共 9–13 根。每個節拍邊界一個 key，值取自姿勢，把手依緩動型別解析寫出（§4）。<br>跟隨骨：用離線二階跟隨器算出，再擬合成 ≤ 6 個 key；或交給 runtime physics。<br>跳躍：每個飛行段一段 cubic；拋物線可以用 cubic 精確表示。<br>squash 一律面積守恆。<br>Cartoon filter 與二階動態只用在擷取來的動作上。 | `beat_table.json`、`beats.py`、`iron-jaw-transcript.md`、[Cartoon Animation Filter](https://www.microsoft.com/en-us/research/?p=157386)（未驗證）、[t3ssel8r](https://www.youtube.com/watch?v=KPoeNZZ6H4s)（未驗證）、[Spring-It-On](https://github.com/orangeduck/Spring-It-On)、[Lasseter 1987](https://www.evl.uic.edu/aej/527/papers/lasseter.pdf)（引文本次未重核）、[ConstraintData 4.3](https://github.com/EsotericSoftware/spine-runtimes/blob/4.3/spine-ts/spine-core/src/ConstraintData.ts)、[E7][E8][E9] |
| **曲線壓縮** | Schneider 1990 的 cubic 擬合，實作有 fitCurves（MIT）。<br>Salient Poses 用 DP 選可編輯的關鍵姿勢，大約保留 10% 的幀（未驗證）。<br>ACL 用骨周圍「虛擬頂點」的位移量誤差（未驗證）。<br>Spine runtime 把每段 bezier 近似成 10 段折線（`BEZIER_SIZE = 18`）。 | `spinegen/curves.py`：照 runtime 的折線評估；時間把手固定在 1/3；所有斜率一次用最小平方解；只在極值種 key。<br>`compress` 本地量測：典獄長 idle 671 → 40 key，react 372 → 102。 | 時間把手不能動。<br>不在節拍邊界種 key，所以會漏掉 hold。<br>容差用度數，不是像素。 | **合成動作完全不擬合，直接解析寫出。** [E7]：5 個解析 key 取代 31 個線性 key，誤差 0.52°，全部來自 runtime 的折線近似。<br>只有擷取或模擬來的動作才用 `curves.fit`，並補三件事：<br>・在節拍邊界種 key。<br>・snap 和 settle 段放開時間把手。<br>・容差改成「最遠加權頂點的像素」：`tol_deg = px_tol / lever_px × 57.3`。例：300 px 的手臂、0.5 px 容差，約等於 0.1°。<br>擷取動作再加一個 Salient-Poses 式的 DP 選 key。 | [Animation.ts](https://github.com/EsotericSoftware/spine-runtimes/blob/4.2/spine-ts/spine-core/src/Animation.ts)、[fitCurves](https://github.com/volkerp/fitCurves)、[Salient Poses](https://scribblethink.org/Work/Pdfs/roberts_OptimalAndInteractiveKeyframeSelectionForMotionCaptureJournalversion.pdf)（未驗證）、[ACL](https://dev.epicgames.com/documentation/unreal-engine/animation-compression-library-in-unreal-engine)（未驗證）、`spinegen/curves.py`、`research/data/*.compressed.json`、[E7] |
| **執行期混合** | 4.2 的 `defaultMix = 0`，也就是硬切。<br>track 0 用 `MixBlend.first`；上層 track 預設 `replace`。<br>`add` 的屬性必須先被下層 key 過，否則每一格都會累加。<br>混合的 alpha 是線性的。<br>draw order 與換圖在混出時不套用，除非調高 threshold。<br>4.3 改成 `TrackEntry.additive` 布林值，並加了 `mixInterpolation`（非線性混合）。<br>混合時間不存在 skeleton JSON 裡。 | 猴子：`mixDuration 0.16`，one-shot 播完回 idle。<br>Hard Time：共用時鐘。 | 混合規則沒有輸出成資料，前端各寫各的。<br>沒有測混合中段。 | 側檔 **`states.json`**：<br>・idle 在 track 0，永遠不被替換（全域時鐘）。<br>・反應在 track 1：mix-in 0.1–0.2 s，結束時呼叫 `addEmptyAnimation(1, 0.2)`。<br>・小反應（點頭、聳肩）用 additive；全身動作用 replace。<br>・additive 會動到的每根骨，都必須被 idle key 過。<br>・反應的頭尾 0.1–0.2 s 先做好 ease，因為 4.2 的混合是線性的。 | [AnimationState.ts](https://github.com/EsotericSoftware/spine-runtimes/blob/4.2/spine-ts/spine-core/src/AnimationState.ts)、[AnimationStateData.ts](https://github.com/EsotericSoftware/spine-runtimes/blob/4.2/spine-ts/spine-core/src/AnimationStateData.ts)、[AnimationState 4.3](https://github.com/EsotericSoftware/spine-runtimes/blob/4.3/spine-ts/spine-core/src/AnimationState.ts) |
| **QA** | rigc（MIT，約 15 star，2026-08 起開發）：50 條具名斷言，不全過就不寫檔。render 提供 folds、seam（gap／ghost）、overlaps、pass-through、angle-range 等讀數。輸出是 4.3。<br>官方 spine-core 可以在 Node 裡直接做幾何檢查。<br>spine-canvaskit 是官方的無瀏覽器渲染，除了雙色 tint 以外全部支援；spine-canvas 不畫 mesh。<br>physics 對 fps 敏感：典獄長 react 在 30／60 fps 時，對 240 fps 的偏差是 1.39／0.90 px（[E3]）。 | spineplay（Python）：只對 3.8／4.0 驗證過；讀 `transform`，不讀 `inherit`；找不到 4.1 以後 deform 的位置；沒有 physics。<br>猴子的預算是靠肉眼每 10° 掃一次。 | 沒有能信任的 4.2 裁判。<br>四肢是剛性卡，所以「0 摺疊」不代表品質好。 | **Node 加 spine-core@4.2.120（精確 pin）當裁判。** spine-canvaskit@4.2.120 產生接觸表與 APNG。<br>numpy 版 LBS 只用在預算搜尋的內圈，結論一律回 spine-core 重算。<br>使用具名閘門（§5）；任何一條硬閘門失敗，就不寫出貨檔。<br>無頭 Chrome（`--enable-unsafe-swiftshader`）只在沙箱 CI 做端到端測試。<br>spineplay 不當 4.2 的裁判。 | [rigc](https://github.com/firejune/rigc)、[spine-ts README](https://github.com/EsotericSoftware/spine-runtimes/blob/4.2/spine-ts/README.md)、[headless.js](https://github.com/EsotericSoftware/spine-runtimes/blob/4.2/spine-ts/spine-canvaskit/example/headless.js)、[SwiftShader M139](https://developer.samsung.com/browser/policy/enable-unsafe-swift-shader.html)、`spineplay/spine.py`、[E3][E11] |

---

## 3. 架構

### 3.1 模組與資料流

```
character.yaml（輸入定義：美術來源、模板、面向、客戶合約、要哪些動作）
   │
   ├─ ingest   (Py)  PSD 或單張圖 ─────────────────────► build/layers.json + build/layers/*.png
   ├─ pose     (Py)  layers + 模板 ────────────────────► build/joints.json（含信心值）
   ├─ rig      (Py)  layers + joints + templates/<id> ─► build/rig.json（版本無關的 IR）
   │                 └ mesh（PythonCDT）、weights（heat/ramp/helper）、IK、physics(f,ζ)、pose table
   ├─ budget   (Py, numpy LBS)  rig.json ──────────────► build/budget.json（每關節 θ_soft/θ_hard 與受限原因）
   ├─ motion   (Py)  motions/*.yaml + rig + budget ───► build/motion.json（IR：key、換圖、drawOrder、deform、事件）
   │                                                    └► build/artreq.json + build/masks/*.png（超出預算時）
   ├─ artreq   (Py)  artreq.json ──► 美術或 Qwen-Image-Edit-2511（本機）──► 新圖層 ──► 回到 ingest（迴圈）
   ├─ emit     (Py)  rig.json + motion.json ───────────► out/<name>.json（Spine 4.2）、.atlas、.png、states.json
   ├─ qa       (JS/Node, spine-core 4.2.120 pinned) ──► out/qa/report.json（具名閘門）、contact sheets
   └─ preview  (JS, spine-canvaskit 4.2.120) ─────────► out/preview/*.apng、viewer.html（spine-pixi-v8 4.2.120）
```

**迴圈。**
- `budget → motion → artreq → ingest → rig → budget`：預算不夠時，先改圖，再重新量預算。
- `qa`：失敗時，`emit` 不留下 `out/<name>.json`，只留下 `qa/report.json` 和接觸表（fail by name, write nothing）。

**原則。**
- `rig.json` 和 `motion.json` 是**與 Spine 版本無關**的中介表示（IR）。
  - 權重存成 setup 時的世界座標，emitter 再換算成各骨的 setup 局部座標。
  - 約束存成語意型別（`ik2`、`half_angle`、`spring(f, ζ)`），由 emitter 展開。
- 4.2 與 4.3 只差在 `emit/spine42.py` 和 `emit/spine43.py` 兩支 serializer。

### 3.2 檔案格式（schema 以 `spinegen-*/1` 命名，全部是 JSON；動作規格用 YAML）

**`layers.json`**：在猴子現有的 `source_layers.json`（`canvas`、`layers[{z,name,file,x,y,w,h}]`）之上擴充，向下相容。

```json
{
  "schema": "spinegen-layers/1",
  "canvas": [560, 912],
  "source": {"kind": "psd", "file": "monkey.psd", "sha256": "…"},
  "layers": [
    {"name": "right_arm_0_upper_arm", "file": "layers/right_arm_0_upper_arm.png",
     "x": 380, "y": 210, "w": 120, "h": 236, "z": 20,
     "role": "upper_arm_r", "tags": ["mesh"],
     "overlap": {"into": "torso_0_trunk", "px": 34},
     "hidden": {"painted": true, "by": "artist", "approved_by": "ad@studio", "mask": "masks/torso_under_arm_r.png"},
     "variants": [{"name": "arm_up_r", "file": "layers/arm_up_r.png", "valid": {"arm_r.shoulder": [110, 175]}}]}
  ]
}
```

**`joints.json`**：

```json
{"schema": "spinegen-joints/1", "template": "biped_mascot", "H": 886,
 "joints": {"shoulder_r": {"xy": [402, 232], "conf": 0.93, "src": "layer_overlap"},
            "elbow_r":    {"xy": [455, 340], "conf": 0.71, "src": "sdpose+dwpose", "fixed_by": "human"}}}
```

**`rig.json`**（IR，節錄）：

```json
{"schema": "spinegen-rig/1", "template": "biped_mascot", "H": 886, "facing": "board_right",
 "bones": [{"name": "upperarm_r", "parent": "clav_r", "role": "arm_r.shoulder", "xy": [402,232], "len": 118, "rot": -92, "sign": 1}],
 "helpers": [{"name": "elbow_mid_r", "kind": "half_angle", "of": "forearm_r", "parent": "upperarm_r"}],
 "ik": [{"name": "leg_r", "bones": ["thigh_r", "shin_r"], "target": "ik_foot_r", "softness": 0.10, "bend": "+"}],
 "springs": [{"bone": "banana", "rotate": 1.0, "f": 1.67, "zeta": 0.71, "inertia": 0.5}],
 "meshes": [{"slot": "upper_arm_r", "image": "right_arm_0_upper_arm", "hull": 24,
             "verts": [[402.1, 228.0]], "tris": [[0, 1, 2]],
             "weights": [[["clav_r", 0.25], ["upperarm_r", 0.75]]]}],
 "pose_table": {"arm_up_r": {"when": {"arm_r.shoulder": [110, 175]},
                             "attachment": {"upper_arm_r": "arm_up_r"},
                             "draworder": {"upper_arm_r": {"below": "head"}}}}}
```

**`budget.json`**：

```json
{"schema": "spinegen-budget/1",
 "joints": {"arm_r.shoulder": {"+": {"soft": 48, "hard": 70, "limit_by": "hole@torso_0_trunk"},
                               "-": {"soft": 30, "hard": 45, "limit_by": "fold@upper_arm_r"}},
            "arm_r.elbow":    {"+": {"soft": 95, "hard": 120, "limit_by": "area@elbow_band"}}}}
```

**`artreq.json`**：

```json
{"schema": "spinegen-artreq/1",
 "items": [
  {"id": "torso_under_arm_r", "kind": "inpaint", "layer": "torso_0_trunk", "mask": "masks/torso_under_arm_r.png",
   "why": "cheer_arms_up.launch arm_r.shoulder 0→165°", "status": "open"},
  {"id": "arm_up_r", "kind": "new_part", "pose": {"arm_r.shoulder": 150, "arm_r.elbow": 15},
   "valid": {"arm_r.shoulder": [110, 175]}, "ref": "masks/arm_up_r_keypoints.png", "status": "open"}]}
```

**`states.json`**：混合表與狀態圖，給前端用；Spine JSON 本身沒有這些資訊。

```json
{"schema": "spinegen-states/1", "runtime": "spine-ts 4.2.x", "default_mix": 0.2,
 "states": {"idle":    {"track": 0, "loop": true},
            "nod":     {"track": 1, "blend": "additive", "mix_in": 0.1, "mix_out": 0.2, "interruptible": true},
            "punch_r": {"track": 1, "blend": "replace",  "mix_in": 0.1, "mix_out": 0.2, "interruptible": false}},
 "exit": "addEmptyAnimation(1, mix_out)"}
```

**`qa/report.json`**：

```json
{"schema": "spinegen-qa/1", "runtime": "@esotericsoftware/spine-core@4.2.120", "pass": false,
 "gates": [{"id": "G1_FOLDS", "pass": true, "value": 0, "max": 0},
           {"id": "M1_FOOT_PLANT", "pass": false, "value": 2.4, "max": 1.0,
            "worst": {"anim": "punch_r", "t": 0.45, "bone": "ik_foot_l"}}]}
```

**輸出**：`out/<name>.json`（Spine 4.2 JSON）、`out/<name>.atlas`、`out/<name>.png`、`out/states.json`、`out/qa/`、`out/preview/`。
- `.skel` 二進位檔不在核心流程裡。需要時在有授權的機器上用 Spine CLI 匯出（§6）。

### 3.3 Python 與 JS 的分工

| 部分 | 語言 | 為什麼 |
|---|---|---|
| ingest、pose、rig、budget、motion、artreq、emit、CLI | Python 3.12 | numpy、OpenCV、PythonCDT、libigl、psd-tools 和各種模型都是 Python；`spinegen/curves.py` 和 research 腳本也已經是 Python。 |
| qa、preview、瀏覽器端到端 | JS（Node ≥ 20） | **官方 runtime 就是裁判**，不移植，也不重寫。spine-core 做數值檢查；spine-canvaskit 做渲染，照 `headless.js` 的做法輸出 APNG。 |
| 介面 | — | Python 用子程序呼叫 `node qa/run.mjs --skeleton out/x.json --atlas out/x.atlas --states out/states.json --ir build/rig.json --motion build/motion.json --budget build/budget.json --fps 30,60,120,240`。qa 讀 IR 來比對計數、預算和節拍標記。 |
| 版本 | — | `qa/package.json` 精確釘 `"@esotericsoftware/spine-core": "4.2.120"`、`"@esotericsoftware/spine-canvaskit": "4.2.120"`，放在 devDependencies。4.3 輸出另開一個 `qa43/`，釘 4.3.13。 |

指令：`spinegen build characters/monkey/character.yaml --target 4.2 --out out/monkey`。
- 這一條指令從美術一路跑到 QA。
- 輸出可重現：模型與 inpaint 固定 seed；`skeleton.hash` 等於 IR 的 sha256。

---

## 4. 動作合成的設計細節

### 4.1 動作規格語言 `spinegen-motion/1`

**通道用解剖語意寫，不寫 Spine 骨名。** 模板負責把通道對應到骨、正負號和 setup 偏移。所以同一份規格可以套到面向不同、鏡像過的角色。

| 通道 | 意義 | 單位 |
|---|---|---|
| `root.{x,up}` | 整隻角色移動。只在滯空或刻意移位時使用；腳踩地的拍子裡**不准 key root** | H（身高） |
| `hips.{fwd,up}`、`chest.{lean,squash,stretch}`、`head.{tilt}` | 身體 | H、°、比例 |
| `arm_*.{clav,shoulder,elbow,wrist}` | `shoulder` 正值等於往頭的方向抬；`elbow` 正值等於彎曲 | ° |
| `legs.{tuck}`、`feet: planted 或 follow` | 腿由 IK 目標驅動，膝蓋自動彎 | H |

**語法規則。**
- 姿勢值都相對於 setup pose。
- 某一拍沒寫到的通道，維持前一個姿勢；emitter 只在值有變化時才寫 key。
- `mirror` 表示左右鏡像；`setup` 表示回到 setup pose。

**完整範例一：出拳 `punch_r`**。節拍用 Iron Jaw `active`。參考資料裡 attack 中位數沒有蓄力，所以 `windup` 可以設成 0 拍。

```yaml
spec: spinegen-motion/1
name: punch_r
template: biped_mascot
category: attack              # 沒寫的參數取 beat_table.json 的 attack 中位數
length: 1.0
aim: board                    # board：打向盤面（側向）；camera：打向鏡頭
layer: {track: 1, blend: replace, mix_in: 0.10, mix_out: 0.20, interruptible: false}

beats:                        # 每拍的結束時刻，以及這一拍用的緩動
  windup:  {end: 0.333, ease: smooth}
  strike:  {end: 0.433, ease: snap}                   # 0.1 s 打出去
  hold:    {end: 0.667, ease: drift, drift: 0.04}     # 動態停格：沿出拳方向再走 4% 振幅
  recover: {end: 1.000, ease: smooth}

poses:                        # 每拍結束時的姿勢；角度 °，位移以 H 計
  windup:
    hips:  {fwd: -0.02H, up: -0.015H}
    chest: {lean: -8}
    head:  {tilt: 4}
    arm_r: {clav: -5, shoulder: -20, elbow: 95}
    arm_l: {shoulder: 30, elbow: 80}                  # 護臉
    feet:  planted
  strike:
    hips:  {fwd: 0.06H, up: -0.03H}                   # 身體前衝，腳由 IK 釘住
    chest: {lean: 12}
    head:  {tilt: -3}
    arm_r: {clav: 8, shoulder: 85, elbow: 5}
    arm_l: {shoulder: 15, elbow: 110}
  hold: {}                                            # drift 自動產生
  recover: setup

followers:                    # 驅動骨以外：離線二階跟隨器 → 擬合成 ≤ 6 key
  default:     {lag: 0.025, f: 3.0, zeta: 0.55, r: 0.5}   # attack chain_lag 中位 0.025 s
  arm_r.wrist: {lag: 0.033, overshoot: 0.12}

squash:                       # 只放在葉骨上，sx·sy = 1
  - {bone: hand_r, during: strike, stretch: 1.12, along: velocity}
  - {bone: hand_r, at: strike.end, squash: 0.92}

art:                          # 姿勢表的觸發規則；預算政策還會自動補更多
  - when: "arm_r.shoulder > 60"                       # 手臂越過胸口中線
    draworder: {arm_r: {above: torso}}
    hysteresis: 8
    at: crossing
  - when: "aim == camera and arm_r.shoulder > 70"
    swap: {hand_r: fist_front}                        # 打向鏡頭才換縮短透視的拳頭
    at: crossing

physics: {reset: false}       # 反應裡不放 reset（會跳），除非 root 瞬移
events:
  - {name: hit, at: strike.end}
budget: {policy: [redistribute, transit_only, draworder, swap, deform, request]}
```

**完整範例二：雙手舉過頭的歡呼 `cheer_arms_up`**（big_win）。

```yaml
spec: spinegen-motion/1
name: cheer_arms_up
template: biped_mascot
category: big_win             # 先驗：蓄力 0.367 s / 0.51×、strike 0.167 s、hold 0.30 s
length: 2.0
layer: {track: 1, blend: replace, mix_in: 0.15, mix_out: 0.20, interruptible: false}

beats:
  crouch:  {end: 0.367, ease: smooth}                      # 蓄力：蹲下，手往下後收
  launch:  {end: 0.534, ease: snap}                        # 0.167 s 蹬地，雙手往上甩
  air:     {end: 0.900, ease: ballistic, g: 3.2H/s2}       # 滯空 0.366 s，頂點 0.054H
  land:    {end: 1.000, ease: snap}
  hold:    {end: 1.550, ease: wiggle, channel: "arm_*.elbow", cycles: 2, amp: 12}   # 舉著搖拳
  recover: {end: 2.000, ease: smooth}

poses:
  crouch:
    hips:  {up: -0.05H}
    chest: {lean: 10, squash: 0.94}
    head:  {tilt: 6}
    arm_l: {clav: -8, shoulder: -18, elbow: 35}
    arm_r: mirror
    feet:  planted
  launch:
    hips:  {up: 0.01H}
    chest: {lean: -8, stretch: 1.06}
    head:  {tilt: -10}
    arm_l: {clav: 15, shoulder: 165, elbow: 15}            # 先分攤：肩帶 +15°
    arm_r: mirror
    feet:  planted                                         # 蹬地那一格腳還在地上
  air:
    root:  {up: 0.054H}                                    # ballistic 自動算頂點時刻與落點
    legs:  {tuck: 0.03H}
    feet:  follow
  land:
    root:  {up: 0}
    hips:  {up: -0.03H}
    chest: {squash: 0.95}
    feet:  planted
  hold:
    hips:  {up: 0}
    chest: {lean: -4}
  recover: setup

followers:
  default: {lag: 0.067, f: 2.0, zeta: 0.45, r: 0.8}        # Iron Jaw 每節 0.067 s
physics:
  gust: {at: launch, wind: 0.6, until: land}               # 一個 wind key 吹動所有 windGlobal 的 physics
art:
  - when: "arm_*.shoulder > 105"
    swap: {upper_arm_*: arm_up_*}                          # 姿勢表：arm_up 有效 110–175°
    draworder: {upper_arm_*: {below: head}}
    hysteresis: 10
    at: crossing
events:
  - {name: jump, at: launch.end}
  - {name: land, at: land.end}
budget: {policy: [redistribute, transit_only, draworder, swap, deform, request]}
```

**跳躍的數字怎麼來。** 飛行時間 T = 0.366 s，頂點高度 h = 0.054H（猴子現有 cheer 跳 48 px，H = 886）。代入 g = 8h/T²，得 g ≈ 3.2H/s²。

### 4.2 節拍、延遲、彈簧參數

**節拍先驗。** 取自 `beat_table.json` 各類別的中位數，規格沒寫的值就用這些。

| 類別 | n | 最深蓄力時刻 | 蓄力比 | strike | hold | recover | 第一節子骨延遲 | 驅動骨過衝 |
|---|---|---|---|---|---|---|---|---|
| attack | 47 | 0.10 s | 0.0023（中位數沒有蓄力；p75 0.47） | 0.108 s | 0.033 | 0.058 | 0.025 | 0.005 |
| react | 135 | 0.20 | 0.33 | 0.167 | 0.054 | 0.10 | 0.054 | 0.005 |
| big_win | 162 | 0.367 | 0.51 | 0.167 | 0.30 | 0.133 | 0.129 | 0.002 |
| trigger | 123 | 0.20 | 0.21 | 0.175 | 0.071 | 0.096 | 0.0 | 0.005 |
| Iron Jaw `active` | — | 0.333（蓄力段長度） | — | 0.10 | 到 0.667 | 到 1.0 | 待機手臂每節 0.067 | — |

**注意事項**（來自 `beats.py` 的定義與查核）：
- `windup_t` 是「從片段開始到最深反向動作」的時刻，不是蓄力段的長度。
- `chain_lag` 只量第一個子骨，而且 7 類裡有 5 類的 p25 是負值，也就是子骨常常比父骨先動。所以這張表只能當先驗，不是定律。
- 格鬥遊戲的 hitstop 幀數**沒有查證成功**，不用。Iron Jaw 本身也完全不震畫面（`iron-jaw-transcript.md`）。

**緩動型別與 bezier 把手。** 時間把手預設放在 t₀ + dt/3 和 t₀ + 2dt/3；Δ = v₁ − v₀。

| ease | `cy1` | `cy2` | 起速／終速 | 用途 |
|---|---|---|---|---|
| `smooth` | v₀ | v₁ | 0 ／ 0 | 蓄力、收回、drift |
| `snap`（參數 s，預設 1） | v₀ + s·Δ | v₁ | 3sΔ/dt ／ 0 | strike（s = 1 就是 ease-out cubic） |
| `accel` | v₀ | v₀ | 0 ／ 3Δ/dt | 落下、甩出 |
| `linear` | v₀ + Δ/3 | v₀ + 2Δ/3 | Δ/dt | 等速 |
| `ballistic` | v₀ + u₀·dt/3 | v₁ − u₁·dt/3 | u₀ ／ u₁ | 滯空（二次曲線可以用 cubic 精確表示） |
| `drift` | 同 smooth，Δ = drift × 前一拍振幅 | | 0 ／ 0 | 動態停格，不完全停住 |
| `wiggle` | 每個循環 2 個 smooth key | | | hold 時搖拳、晃動 |

- Spine 的曲線是**逐段**存的：每段的兩個把手互相獨立，所以在節拍邊界「斷切線」是免費的（蓄力的終速 0，接 strike 的起速 3Δ/dt）。
- curve 是 4 個絕對數字 `[cx1, cy1, cx2, cy2]`，單位是秒和屬性本身的單位。translate 這種有兩個值的 timeline，要 8 個數字（[SkeletonJson 4.2 readCurve](https://github.com/EsotericSoftware/spine-runtimes/blob/4.2/spine-ts/spine-core/src/SkeletonJson.ts)）。

**跟隨骨。**
- 每節延遲：attack 0.025 s、react 0.05 s、big_win／win 0.067 s。最後一個值取 Iron Jaw，因為 big_win 的中位 0.129 s 分布太散。
- 二階跟隨器，參數依角色決定：
  - 驅動骨的跟隨：f 約 3 Hz、ζ 0.8–1。
  - 末端骨：f 約 2 Hz、ζ 0.4–0.5、r 0–1。
- 過衝放在末端，5–20%；驅動骨過衝 ≤ 3%。
- 跟隨器離線以 240 Hz 跑，再用 `curves.fit` 擬合；種子放在節拍邊界加上延遲的位置。

**physics 預設值。** mass = 1；公式見總表；換算出的值是連續時間的近似。第 4 階段要在 spine-core 裡量步階響應，確認 f、ζ 誤差 ≤ 10%。

| 部位 | f (Hz) | ζ | 推得 strength | 推得 damping | inertia | 權重 |
|---|---|---|---|---|---|---|
| 頭髮、耳朵 | 1.6 | 0.45 | 101 | 0.860 | 0.5 | rotate 0.2（Iron Jaw 是 0.15–0.2） |
| 衣襬、袖口 | 2.0 | 0.50 | 158 | 0.811 | 0.5 | rotate 0.3 |
| 道具（帽、香蕉） | 2.5 | 0.60 | 247 | 0.730 | 0.5 | rotate 1.0 |
| 尾巴 | 1.2 | 0.35 | 57 | 0.916 | 0.5 | rotate 0.5 |
| 對照：猴子現有的香蕉 | 1.67 | 0.71 | 110 | 0.78 | 0.5 | rotate 1 |

**squash 與 stretch。**
- stretch：在峰速那一格，沿速度方向拉長，倍率 1 + k·|ω|，四肢上限 1.15、道具上限 1.3；另一軸補 1/s。落地或撞擊時再 squash。
- 只放在葉骨上。父骨做非等比縮放會讓轉過的子骨 shear，而兩骨 IK 又要求 `inherit: normal`。所以 IK 鏈上**絕不放 scale key**。
- 軀幹的 squash 用「胸骨沿脊椎平移，加上 belly 葉骨補 scaleX」來做，不在父骨上縮放（[SkeletonJson 4.2 inherit](https://github.com/EsotericSoftware/spine-runtimes/blob/4.2/spine-ts/spine-core/src/SkeletonJson.ts)）。

**重心。** 腳踩地的拍子裡，重心（以 alpha 面積加權）要落在兩腳之間。這條是警告級的 QA，不是硬閘門。Cascadeur 的原理描述**未驗證**，這裡只取「重心在支撐上、滯空走拋物線」這個常識性規則。

### 4.3 超出美術預算時怎麼辦

`budget` 對每個關節、每個方向量出兩個角度：
- **θ_soft**：0 摺疊、關節帶面積 ≥ 0.9、0 破洞、0 殘影。hold 段只能用到這個角度。
- **θ_hard**：只要求 0 摺疊、0 破洞。只准在 strike 或 transit 段經過，不准停留。這就是猴子 README 裡 throwit「只經過、不停留」的形式化。

規格中的角度超過 θ_soft 時，依 `budget.policy` 的順序處理，每一步都要再過一次 QA：

1. **redistribute（分攤）**：把超出的角度依權重往鏈的上游分。順序是肩帶（clav）、胸（後仰或前傾）、髖；每根骨都不超過自己的 θ_soft。例：舉手 165° 分成肩帶 15°、胸後仰 8°，肩膀剩 142°。
2. **transit_only（只經過）**：strike 段允許到 θ_hard；hold 段的姿勢改寫回 θ_soft 以內。
3. **draworder**：限制原因是 seam 殘影，或部件疊到別的部件時，在門檻被跨過的那一格插 drawOrder key。去程門檻 θ，回程門檻 θ − hysteresis。
4. **swap（換圖）**：姿勢表裡有涵蓋這個角度的 attachment（例如 `arm_up_r` 有效 110–175°），就在跨過門檻的那一格換圖，並在同一個時刻插 drawOrder key。換圖時刻選在角速度 ≥ 200°/s 的格子，動作快、眼睛看不出換圖；跨門檻那一格太慢，就移到最近的快格。回程有 10° 遲滯。
5. **deform（修正 deform）**：strike 末或 hold 姿勢剩下的形變誤差，用離線 ARAP 解算，扣掉 LBS 的結果，差值寫成 deform key（`offset` 稀疏寫法）。ARAP 可參考 Animated Drawings 的 `arap.py`（MIT）。對 4.2 加權網格來說，deform 是每個 influence 的骨局部偏移，所以修正會跟著骨走。
6. **request（補畫單）**：上面五步都不夠時，寫進 `artreq.json`：遮罩、需要的姿勢角度、用在哪支動作的哪一拍。圖還沒到之前，幅度先夾在 θ_soft，並在 QA 報告標 `clamped_by_art`。動作永遠可以出貨，只是幅度比較小。

來源：[Animated Drawings arap.py](https://github.com/facebookresearch/AnimatedDrawings/blob/main/animated_drawings/model/arap.py)、[Attachment.ts（deform 加在每個 influence 上）](https://github.com/EsotericSoftware/spine-runtimes/blob/4.2/spine-ts/spine-core/src/attachments/Attachment.ts)、`gobananas_monkey/README.md`。

### 4.4 怎麼輸出少量 bezier key

1. **驅動骨**：每個「有寫到」的節拍邊界一個 key。值由語意通道經模板換算（正負號、setup 偏移），曲線依下一段的緩動型別解析寫出。**不取樣，也不擬合。**
2. **translate**：x 和 y 共用時間點；curve 要 8 個數字。
3. **跟隨骨與 physics 烘焙**：240 Hz 模擬，再用 `curves.fit` 擬合。種子放在節拍邊界加上延遲；容差是最遠加權頂點的 0.5 px；snap 和 settle 段放開時間把手。
4. **單段 snap 超過 40°**：在中點加一個 key，讓 runtime 折線和意圖曲線的差距 ≤ 0.5°。依據：[E7] 量到 58° 在 0.1 s 內的那一段，誤差是 0.52°。
5. **驗算**：用 `curves.evaluate`（與 runtime 的折線一致）在 240 Hz 下比對意圖曲線，最遠頂點誤差 ≤ 0.5 px。
6. **reaction 的頭尾**：保留明確的「回到 setup」key。replace 混合需要它；在 additive 下它是差值 0。不要對反應動畫跑編輯器的 Clean Up（據論壇說法，只有最低的 track 才安全；未驗證）。

`punch_r` 的上臂（`upperarm_r`）實際輸出如下。drift 是 4% × 105° = 4.2°。

```json
"upperarm_r": {"rotate": [
  {"time": 0,     "value": 0,     "curve": [0.111, 0, 0.222, -20]},
  {"time": 0.333, "value": -20,   "curve": [0.3663, 85, 0.3997, 85]},
  {"time": 0.433, "value": 85,    "curve": [0.511, 85, 0.589, 89.2]},
  {"time": 0.667, "value": 89.2,  "curve": [0.778, 89.2, 0.889, 0]},
  {"time": 1,     "value": 0}
]}
```

- 5 個 key。同一個動作照 Hard Time 的 30 fps 取樣要 31 個 key，而且偏離 5.04°（[E7]）。
- draw order 的時刻：snap 去程跨過 60° 是在 t ≈ 0.371；smooth 回程跨過 52°（60 − 8）是在 t ≈ 0.815。輸出：

```json
"drawOrder": [
  {"time": 0.371, "offsets": [{"slot": "upper_arm_r", "offset": 4}, {"slot": "forearm_r", "offset": 4}, {"slot": "hand_r", "offset": 4}]},
  {"time": 0.815}
]
```

不帶 `offsets` 的 drawOrder key，表示回到 setup 的順序。

---

## 5. QA 閘門清單與通過門檻

**跑法。**
- 每支動畫在 60 fps 下逐格跑；physics 相關的閘門另外跑 30、120、240 fps。
- 所有比較都取共同時刻 t = k/30。這修正了舊 probe 在 144 fps 下取樣點不對齊的問題。
- 裁判一律是 spine-core@4.2.120。

**門檻怎麼讀。** 有外部依據的門檻會附上來源；其他門檻是**我們定的初值**，第 7 階段用參考角色校準。像素門檻以 H ≈ 900–1800 px、1:1 渲染為準，其他尺寸線性換算。

| ID | 閘門 | 量法 | 門檻 |
|---|---|---|---|
| **L1** VERSION | 版本 | `skeleton.spine` 的 major.minor 等於目標版本；用客戶的確切 runtime 版本載入不拋錯 | 必過 |
| **L2** COUNTS | 計數 | runtime 的 bones、slots、skins、animations、events、ik、transform、path、physics 數量，與 IR 相同 | 差 0 |
| **L3** ORDER | 4.2 約束順序 | `order` 的集合剛好是 {0..N−1} | 必過 |
| **L4** CURVES／NAN | 曲線與數值 | curve 長度等於 4 × 值數；所有 key 與每一格的 world 值都是有限數 | 0 違規 |
| **L5** IK_VALID | IK | IK 骨都是 `inherit: normal`；2-bone IK 的父骨有父骨；子骨 `length > 0` | 0 違規 |
| **L6** PHYSICS_VALID | physics | 每條至少一個非零權重；rotate、shearX、scaleX physics 的骨長 ≥ 0.02H；fps、inertia、strength、damping、mass、limit 全部明寫；limit ≥ 1000 | 0 違規 |
| **L7** ATLAS | atlas | page `size:` 等於 PNG 實際尺寸；pma 與 runtime 設定一致；每個 attachment 都有 region | 0 違規 |
| **L8** MESH | 網格 | `hull > 0` 且 hull 頂點在最前；三角形索引不越界；每頂點 ≤ 3 個 influence；權重和為 1 ± 1e-4 | 0 違規 |
| **L9** TC_MIX | transform constraint | 六個 mix 全部明寫 | 0 違規 |
| **G1** FOLDS | 翻面三角形 | 三角形的 signed area 與 UV 繞向相反（[E3][E11]：強迫胸骨 +150° 會量到 83 個） | **0** |
| **G2** AREA | 面積 | 單一三角形面積比在 [0.3, 3]；關節帶面積保留 ≥ 0.85，hold 段 ≥ 0.90 | 0 違規 |
| **G3** FLIP | 骨翻面 | 每根骨 world 行列式的正負號與 setup 相同，除非有標 `flip` | 0 |
| **G4** SEAM_GAP | 關節破洞 | 每對有關節的 slot，在半徑 1.5 倍肢體半寬的關節圓盤內，數兩部件聯合外殼內 alpha < 0.5 的像素（先做 1 px 侵蝕）。這是 rigc `--seam` 的 gap 定義 | **0 px** |
| **G5** HOLE | 全身破洞 | rest 時不存在、也沒有標記的封閉背景連通塊，面積 ≥ 16 px | **0** |
| **G6** GHOST | 殘影 | (a) 關節處露出的重疊帶（rigc ghost）長度 ≤ 0.25 倍肢體寬；(b) 遮擋者移開後露出的底層像素，與遮擋者 rest 時的像素做 NCC，≤ 0.3。後者用來抓「軀幹上沒補乾淨的手臂殘影」 | 0 違規 |
| **G7** PASS_THROUGH | 穿模 | 衣物落在腿的輪廓內（rigc `--pass-through`） | 0 格（有標記的例外除外） |
| **M1** FOOT_PLANT | 腳釘地 | planted 拍內，腳底接觸頂點的 world 位移 ≤ 1 px，速度 ≤ 5 px/s | 必過 |
| **M2** BUDGET | 預算 | hold 段 ≤ θ_soft；strike 或 transit 段 ≤ θ_hard | 0 違規 |
| **M3** DRIVER_OVERSHOOT | 驅動骨過衝 | 沒有標記的驅動骨，過衝 ≤ 該段振幅的 3%（參考角色中位 0.3%） | 0 違規 |
| **M4** KEYS | key 數 | 合成的驅動 timeline ≤ 被寫到的拍數 + 1，wiggle 每個循環另加 2（參考角色大動作中位 9、p75 14）；整檔 ≤ 35 key/s（Hard Time 是 300+） | 0 違規 |
| **M5** LOOP_SEAM | 迴圈接縫 | 首尾值差 ≤ 0.01° 或 0.01 px；斜率差 ≤ 2% | 0 違規 |
| **M6** AREA_PRESERVE | 面積守恆 | 沒有標 `perspective` 時，\|sx·sy − 1\| ≤ 0.05 | 0 違規 |
| **M7** BEAT_TIMING | 節拍時刻 | strike 段峰速的時刻與規格相差 ≤ 1 格（60 fps，17 ms） | 0 違規 |
| **M8** COM | 重心 | 腳踩地的拍子裡，重心 x 落在兩腳跨距 ±10% 內 | 警告 |
| **M9** PLAYBACK | 播放誤差 | runtime 折線與意圖曲線的差距，最遠頂點 ≤ 0.5 px | 0 違規 |
| **P1** FPS_ROBUST | physics 對 fps | physics 骨尖端相對 240 fps 的偏差：60 fps ≤ 2 px、30 fps ≤ 4 px、120 fps ≤ 1 px。基準：典獄長 react 在 60／30 fps 是 0.90／1.39 px（[E3]） | 必過 |
| **P2** EXCURSION | physics 幅度 | physics 造成的最大角度 ≤ 該關節的 θ_hard；沒有 NaN | 0 違規 |
| **P3** DETERMINISM | 決定性 | 同一組 dt 序列重播兩次，差值為 0（[E3]） | 必過 |
| **P4** RESET | reset key | 反應動畫裡沒有 physics reset key，除非有標記 root 瞬移 | 0 |
| **X1** TRANSITIONS | 混合中段 | `states.json` 裡每一對 from→to，分別從 idle 相位 0、0.25、0.5、0.75，以及來源動畫每一拍的中點切入；G1–G7 加 M1 全過 | 必過 |
| **X2** POP | 混合跳動 | 混合期間每一格的最大頂點位移 ≤ 兩支動畫各自單播時同一時刻最大值的 1.25 倍 + 1 px | 0 違規 |
| **X3** ADDITIVE_BASE | additive 基底 | additive 反應會動到的每一個骨屬性，都被 track 0 的 idle key 過 | 0 違規 |
| **B1** COST | 成本 | 骨 ≤ 80（Iron Jaw 是 45–59）；頂點 ≤ 2000；平均 influence ≤ 2.2；atlas ≤ 2 張 2048²；混合模式切換 ≤ 4 次。依客戶合約可調整 | 0 違規 |
| **B2** JOINT_BAND | 跨關節權重 | 關節帶內至少 90% 的頂點有 ≥ 2 個 influence（Hard Time v2 是 0%） | 必過 |
| **B3** NAMES | 動畫名稱 | 動畫名稱集合與客戶合約完全一致 | 必過 |
| **B4** ART_APPROVED | 美術簽核 | 每張生成的圖層都有簽核紀錄 | 必過 |
| **C1** ORACLE | 內圈校驗 | numpy LBS 與 spine-core 的 world 頂點差 ≤ 0.1 px | 必過 |
| **C2** EDITOR | 編輯器接受（可選） | 在有授權的機器上，`Spine -u 4.2.xx -i out.json -o rt.spine -r` 匯入成功，並能匯出 binary | 有授權時必過 |
| **C3** BROWSER | 瀏覽器端到端（可選） | 無頭 Chrome 用 spine-pixi-v8 4.2.120 載入並播放全部動畫，不出錯 | CI 必過 |

閘門的概念與讀數取自 [rigc](https://github.com/firejune/rigc)；runtime 行為依 [spine-core 4.2](https://github.com/EsotericSoftware/spine-runtimes/tree/4.2/spine-ts/spine-core/src)；CLI 依 [Spine CLI](https://en.esotericsoftware.com/spine-command-line-interface)（摘要）與 [export.sh](https://raw.githubusercontent.com/EsotericSoftware/spine-runtimes/4.3/examples/export/export.sh)。

---

## 6. 授權與風險

| 項目 | 條款 | 我們怎麼做 | 來源 |
|---|---|---|---|
| **Spine Runtimes License**（2025-04-05 更新） | 依 Editor License 第 2 節可以整合 runtime；否則「每個使用者都必須有自己的 Spine Editor 授權」。 | 生成器**不含任何 runtime 程式碼**，JSON 和 atlas 都是自己寫的。spine-core 和 spine-canvaskit 只是 devDependency，由有授權的人員安裝。出貨的工作室自己要有授權。「自己寫 JSON 不算整合」是我們對條文的解讀，不是法律意見，要再向 Esoteric 或法務確認。 | [LICENSE](https://github.com/EsotericSoftware/spine-runtimes/blob/4.2/LICENSE)、[spine-ts README](https://github.com/EsotericSoftware/spine-runtimes/blob/4.2/spine-ts/README.md) |
| Spine 版本方案 | Essential 方案沒有 mesh；Professional 是 $369；營收或募資達 $500k 要買 Enterprise（基本價 $2,499）。以上都是搜尋摘要。 | 要手調我們加權網格的動畫師，至少需要 Professional。Hacksaw 這種規模的公司要 Enterprise。 | [spine-purchase](https://en.esotericsoftware.com/spine-purchase)（摘要） |
| 衍生自 runtime 原始碼的程式 | 移植 runtime（例如 spineplay，或照 SkeletonBinary 寫的 .skel writer）可能被視為衍生作品。 | numpy LBS 只依公開的公式實作，只當內圈。.skel 一律交給 Spine CLI 產生。spineplay 不進 public repo 的核心。 | [LICENSE](https://github.com/EsotericSoftware/spine-runtimes/blob/4.2/LICENSE) |
| Spine CLI 放在 CI | 需要已安裝並啟用的編輯器。Essential／Professional 是一個具名使用者、最多 2 台電腦；Enterprise 禁止讓不受控的使用者共用伺服器安裝（摘要）。CLI 打包目前會忽略 project images 路徑外圖片的 mesh 資訊（issue #1109）。 | 只當可選的 C2 閘門，不讓核心流程依賴它。 | [spine-editor-license](https://en.esotericsoftware.com/spine-editor-license)（摘要）、[issue #1109](https://github.com/EsotericSoftware/spine-editor/issues/1109) |
| 網格與權重套件 | PythonCDT 2.0.2 的授權是 `MPL-2.0 AND BSD-3-Clause AND Apache-2.0 WITH LLVM-exception`。libigl 是 MPL-2.0（檔案層級的 copyleft，修改過的檔案要公開）。Shewchuk 的 Triangle 用在商用系統裡要直接向作者取得授權。 | 只連結使用，不修改 MPL 檔案。Triangle 只准內部原型。 | [PythonCDT](https://pypi.org/project/PythonCDT/)、[libigl](https://pypi.org/project/libigl/)、[Triangle README](https://raw.githubusercontent.com/libigl/triangle/master/README) |
| 分件與補畫模型 | SAM 3：自訂的 SAM License，免權利金；有貿易管制與 ITAR 條款；禁止逆向工程；對 Meta 提 IP 訴訟會終止授權。SAM 2 是 Apache-2.0。See-through：code 是 Apache-2.0，權重授權沒有載明，可能繼承 SDXL 的 OpenRAIL++-M（未驗證）。Qwen-Image 系列是 Apache-2.0。FLUX.1 Kontext [dev] 只限非商用。pix2gestalt 是 CC BY-NC。 | 預設用 SAM 3 加 Qwen-Image-Edit-2511；有顧慮時退回 SAM 2。不打包、不轉散布 See-through 的權重，由使用者自己下載並接受條款。禁止使用 FLUX dev 和 pix2gestalt。 | [SAM 3 LICENSE](https://github.com/facebookresearch/sam3/blob/main/LICENSE)、[See-through](https://github.com/shitagaki-lab/see-through)、[Qwen-Image](https://github.com/QwenLM/Qwen-Image)、[FLUX.1-dev license](https://github.com/black-forest-labs/flux/blob/main/model_licenses/LICENSE-FLUX1-dev)、[pix2gestalt](https://github.com/cvlab-columbia/pix2gestalt) |
| 姿勢模型 | SDPose code 是 MIT，權重授權沒有載明。DWPose 與 rtmlib 是 Apache-2.0，但 RTMW 權重用了 14 個資料集訓練，各資料集條款要另外查。X-Pose 只限非商用；bizarre-pose-estimator 是 AGPL-3.0。Molmo 2 checkpoint 是 Apache-2.0（訓練資料限制的說法未驗證）。 | 出貨工具只用 Apache 或 MIT 的模型，權重授權逐一記錄在 `NOTICE`。X-Pose 和 bizarre 只能做離線評估。 | [SDPose-OOD](https://github.com/T-S-Liang/SDPose-OOD)、[DWPose](https://github.com/IDEA-Research/DWPose)、[RTMPose README](https://github.com/open-mmlab/mmpose/blob/main/projects/rtmpose/README.md)、[X-Pose](https://github.com/IDEA-Research/X-Pose)、[bizarre](https://github.com/ShuhongChen/bizarre-pose-estimator)、[Molmo 2](https://github.com/allenai/molmo2) |
| 動作模型與資料 | HY-Motion：排除 EU、UK、南韓，連輸出也不能在區外使用；MAU 超過 100 萬要另外申請。GVHMR：只限教育、研究、非營利。SMPL 系模型預設非商用（未驗證）。HumanML3D／AMASS：非商用、不可轉散布。LAFAN1 是 CC BY-NC-ND 4.0；Bandai Namco 是 CC BY-NC 4.0。Kimodo：code 是 Apache-2.0，SOMA 權重是 NVIDIA OML，文字編碼器屬 Llama 3（OML 全文未能取得）。 | 我們的客戶是瑞典（EU）的工作室，**HY-Motion 一律禁用**。SMPL 系與 HumanML3D 訓練出的權重不得產生任何出貨內容。NC 資料只准內部調參。Kimodo 要等法務確認後才試用。 | [HY-Motion License](https://raw.githubusercontent.com/Tencent-Hunyuan/HY-Motion-1.0/master/License.txt)、[GVHMR](https://raw.githubusercontent.com/zju3dv/GVHMR/main/LICENSE)、[HumanML3D](https://github.com/EricGuo5513/HumanML3D)、[LAFAN1](https://github.com/ubisoft/ubisoft-laforge-animation-dataset)、[Bandai Namco](https://github.com/BandaiNamcoResearchInc/Bandai-Namco-Research-Motiondataset)、[Kimodo](https://github.com/nv-tlabs/kimodo) |
| 第三方程式碼 | spine-animation-ai 是 PolyForm Noncommercial。rigc 是 MIT，但 bench 裡的參考幀只限非商用。Animated Drawings 是 MIT（已封存）。Spring-It-On、fitCurves、StretchyStudio 都是 MIT。 | 不抄 PolyForm 的程式碼；rigc 只借概念，不拿素材；MIT 的程式碼可以附上 notice 後採用。 | [spine-animation-ai](https://github.com/GenielabsOpenSource/spine-animation-ai)、[rigc](https://github.com/firejune/rigc)、[Animated Drawings](https://github.com/facebookresearch/AnimatedDrawings)、[Spring-It-On](https://github.com/orangeduck/Spring-It-On)、[fitCurves](https://github.com/volkerp/fitCurves) |
| **public repo 與美術** | 這個 repo 是 public。 | **以下東西一律不進 repo**：Hacksaw、Iron Jaw、客戶美術、骨架、動作包，以及猴子的美術（版權沒確認前）。`research/data/` 已經被 gitignore。`beat_table.json` 只有彙總統計，可以進。測試素材一律用我們自己程式生成的合成人偶，或 CC0 的圖。Esoteric 的 4.3 `examples/` 沒有標授權，不進 repo。 | `README.md`、`.gitignore`、[4.3 examples](https://github.com/EsotericSoftware/spine-runtimes/tree/4.3/examples) |
| AI 輸出的版權 | 美國版權局 2025-01-29 的報告認為，純 AI 生成、或只靠 prompt 控制的內容不受保護（二手來源，未能直接讀到）。 | 補畫和另畫的圖一律由美術審過、修過，`artreq.json` 記錄誰在什麼時候簽核。 | [Copyright Alliance 摘要](https://copyrightalliance.org/ai-report-part-2-copyrightability/)（未驗證） |
| 客戶 NDA | 把客戶美術送到外部的影片或圖像 API，可能違反 NDA。 | 所有模型都在本機跑；外部服務只能在客戶同意後 opt-in。 | — |
| 技術風險 | 版本漂移：npm `latest` 已經是 4.3。physics 對 fps 敏感。Chrome 從 M139 起要加 `--enable-unsafe-swiftshader` 才能在無 GPU 下開 WebGL。 | QA 精確 pin 版本；用 P1 閘門檢查多種 fps；需要像素一致的行銷用渲染時，自己烘焙 physics；SwiftShader 只在沙箱 CI 開。 | [npm](https://registry.npmjs.org/@esotericsoftware/spine-core)、[E3]、[SwiftShader](https://developer.samsung.com/browser/policy/enable-unsafe-swift-shader.html) |

---

## 7. 分階段實作計畫

順序是刻意安排的。**要讓猴子最快做出「雙手舉過頭」和「出拳」，路徑是 P1 → P2 → P3 → P4**：
- 猴子已經有分層，所以完全跳過 ML 進件（P6）。
- P3 的補畫單在 P2 進行中，就可以先用剛性掃描算出第一版，讓美術提早開工。

第一欄的括號是 README 原本的 M 編號。M0、M1 已完成（`analyze_motion.py`、`big_moves.py`、`beat_table.json`、`spinegen/curves.py`）。

| 階段 | 產出 | 驗收數字 |
|---|---|---|
| **P1 裁判與輸出骨幹**（M2a、M6 的一半） | `qa/`：Node 套件，精確 pin spine-core 與 spine-canvaskit 4.2.120，含 L1–L9、G1、G3、M1、P1、P3、C1。<br>`spinegen/emit/spine42.py`、`spinegen/lbs.py`。 | ① 典獄長：L2 計數吻合（24 骨、20 mesh、4 IK、4 physics）；L8 正確標出 20 個 `hull=0`。<br>② 猴子 `monkey.json`：L1 標出「宣告 4.1.20 卻有 physics」。<br>③ 典獄長用 4.3.13 載入：L2 標出 constraints 是 0。<br>④ 胸骨 +150° 時 G1 ≥ 1 個摺疊（查核量到 83 個）；典獄長 react 是 0。<br>⑤ numpy LBS 與 spine-core 差 ≤ 0.1 px。<br>⑥ 典獄長轉成 IR 再重新輸出，spine-core world 頂點差 ≤ 0.01 px。 |
| **P2 讓圖撐得住（猴子現有的圖）**（M2b、M4） | CDT 網格；關節帶頂點列；heat 加分段 ramp 權重；肘、膝的半角輔助骨；兩骨腿 IK；`budget` 掃描器。<br>猴子的 12 件重綁，保留 `RIGID` 模式做對照。 | ① 剛性模式掃描重現手量的預算：肩 θ_soft 26 ± 4°、肘 22 ± 4°（README M4）。<br>② 合成手臂在 **spine-core** 裡：輔助骨 rig 在 120° 時 0 摺疊、關節帶面積 ≥ 0.9。這一步把 [E1] 的 numpy 結論在官方 runtime 上重做。<br>③ 猴子新 rig：肘 θ_soft ≥ 90°；B2 ≥ 90%；頂點數 ≤ 等效格網的 60%。<br>④ 蹲 0.05H 時，M1 腳滑 ≤ 1 px。<br>⑤ `budget.json` 寫出肩膀受限的原因（預期是 `hole@torso`）。 |
| **P3 補畫單與換圖（猴子）**（M5a） | 掃描「蒙皮後」遮擋者求出遮罩；產出 `artreq.json`、`masks/`；可選本機 Qwen-Image-Edit-2511 自動補畫，含漂移拒收；姿勢表；`arm_up_l/r` 與 `fist_front` 的關鍵點對位。 | ① `cheer_arms_up` 加 `punch_r` 的補畫單 ≤ 6 項，每項都有遮罩和用到它的那一拍。預期的項目：兩側軀幹被手臂擋住的部分、上臂往肩內延伸、`arm_up_l/r`、`fist_front`。<br>② 圖到位後掃肩膀 0 → 165°（在 105° 換圖）：G1、G4、G5、G6 全部是 0。<br>③ 自動補畫在遮罩外的 ΔE ≤ 3。<br>④ B4 全部有簽核紀錄。 |
| **P4 節拍文法（猴子的兩支招牌動作）**（M3） | 規格解析器；節拍圖；解析式 emitter；跟隨器；(f, ζ) 換算 physics 並在 spine-core 驗證；ballistic；換圖與 drawOrder 排程；`states.json`。<br>重新產生原本的 9 支動作，加上 `punch_r`、`cheer_arms_up`。 | **cheer**：肩 ≥ 150°；手腕高於頭頂並維持 ≥ 0.4 s；跳躍頂點 0.054H ± 5%。<br>**punch**：上臂在 0.1 s 的 strike 內轉 ≥ 100°，峰速時刻誤差 ±17 ms；髖前衝 ≥ 0.06H；M1 ≤ 1 px；拳頭跟隨過衝 10–15%；換圖與 drawOrder 在同一個 key。<br>**全部**：G、M、P、X 閘門在 30、60、120 fps 全過；X1 在 4 個 idle 相位和每一拍中點切入都過；M4 每條驅動 timeline 都在上限內。<br>和同一動作的 30 fps 取樣相比：key 數 ≥ 10 倍少，誤差 ≤ 0.5°（README M3）。<br>physics 步階響應的 f、ζ 誤差 ≤ 10%。 |
| **P5 第二隻角色：重建典獄長**（M2c） | 用生成器重建 Hard Time 的典獄長，修掉 physics `limit` 的 bug。囚犯雙手交叉，要先改 rest 姿勢的美術，所以延後。 | B2 從 0% 升到 ≥ 90%；idle ≤ 60 key（現在 671；壓縮器已經能做到 40）；react 四肢 ≥ 60°（現在 2–12°）且全部閘門通過。 |
| **P6 單張圖進件**（M5b） | SAM 3 或 See-through；SDPose 加 DWPose，或 Molmo 2；模板擬合；人工修正關節的介面；rest 姿勢重擺（關鍵點條件編輯，需要美術總監簽核）。 | 10 隻內部保留角色（把分層扁平化後當成單張輸入，不進 repo）：≥ 90% 的關節誤差 ≤ 4% H；idle 下 G 閘門全過；自動產出補畫單。 |
| **P7 Spine-Bench 與一條指令**（M6） | 從 235 隻參考角色中保留 20 隻（只放內部），由生成器重新綁骨，並套上 cheer 和 punch；`spinegen build` 一條指令跑完。 | 全部閘門的通過率；通過閘門時四肢最大角的中位數 ≥ 62°（參考角色中位數）；每條大動作 timeline 的 key 數 ≤ 9（參考中位數）。README 只引用這些量出來的數字。 |
| **P8 4.3 後端** | `emit/spine43.py`：`constraints[]`、slider（修正動畫由角度驅動）、`ScaleYMode.Volume`、`mixInterpolation`、transform 的 `source`／`properties`。 | 同一份 IR 的 4.3 輸出在 spine-core 4.3.13 下全部閘門通過；4.2 的輸出位元組完全不變。 |

---

## 8. 還沒解決的問題

1. **半角輔助骨還沒在官方 runtime 驗證過。** 目前只在 numpy、合成的直手臂上量過 [E1]；ramp40 在 150° 仍然有 4 個翻面。參考角色在大動作裡只有 2.2% 用到 transform constraint，所以這招超出參考做法，必須拿 P2 的 spine-core 結果，和參考角色的畫面品質並排比較。
2. **LBS 在 120–150° 以上一定會塌。** 換圖用的部件要畫在哪個角度、有效區間多寬，目前還沒有自動決定的方法，只能先按姿勢表手設。
3. **破洞與殘影的門檻要校準。** G4–G6 的定義和門檻是我們自己定的，合法的輪廓變化（例如手插腰形成封閉的洞）可能被誤判，要用參考角色校準假陽性率。
4. **AI 分件與補畫在西式卡通的老虎機美術上表現如何，未驗證。** 包括：SAM 3 對「left forearm」這種文字提示的效果；See-through 的動漫偏向；Qwen 編輯的色偏和形變漂移；另畫部件能不能保持角色長相。補畫遮罩的掃描法也還沒驗證。
5. **physics 隨 fps 變化。** 4.2 沒有烘焙 physics 的功能。自己烘焙可以拿到像素一致的結果，但會失去對任意混合的反應。哪些部位要烘焙，需要逐一判斷。
6. **4.2 與 4.3 的 additive 語意不同。** 4.2 是 `MixBlend.add`，4.3 是 `TrackEntry.additive`。我們只能用 `states.json` 表達意圖，前端包裝要客戶自己實作。客戶確切用哪個 runtime 版本也還不知道：我們的播放器是 4.2.74，目前最新的 4.2 是 4.2.120。
7. **寫實動作重定時成卡通節拍，還沒有公認的方法。** Kimodo、MoMask 等模型都產生不出參考角色那種不對稱節拍。時間扭曲加上振幅增益的具體演算法還要設計和驗證。
8. **節拍表本身的限制。** `windup_t` 是時刻不是長度；`chain_lag` 只量第一個子骨；中位數的 attack 沒有蓄力。還需要逐骨、逐節的統計，以及末端骨的過衝統計（目前完全沒量）。
9. **Spine 編輯器能不能接受我們的 JSON**（hull 順序、nonessential 欄位），要等第一次在有授權的機器上跑 CLI 匯入（C2）才知道。CLI 的 `-m`／`--clean` 參數和無頭環境的需求未驗證。
10. **非人形角色。** 四足、尾巴、會飛的吉祥物，模板涵蓋率和 physics 鏈的預設值都還沒做。AnyTop（任意拓樸動作生成）未驗證。
11. **rest 姿勢重擺。** 囚犯雙手交叉這類「選角時就決定了預算」的情況，要先用關鍵點條件編輯重畫 rest 姿勢。品質和美術總監的簽核流程都還沒定。
12. **外部說法還沒查證的部分。** 本輪 WebSearch 額度已用完，以下來源大多無法直接讀取，相關說法都標為未驗證，有網路後要再查：esotericsoftware.com 的 Trace／Generate／Auto 權重文件與 CLI 細節、Moho、Harmony、Character Animator、Cartoon Animator、Cascadeur、hitstop wiki、Mixamo 與 CMU 授權、NVIDIA OML 全文。

---

### 附錄：本地證據

以下實驗的程式碼在 `research/experiments/`（見該目錄 README）：

- [E1] research/experiments/py/elbow.py：合成手臂的 LBS、輔助骨、BBW 對照
- [E2] research/experiments/py/meshcmp.py：CDT 與 22 px 格網的對照
- [E3] research/experiments/spine42/probe.mjs、research/experiments/spine42/qa_probe.mjs：spine-core 4.2.120 的摺疊、physics 對 fps、決定性
- [E4] research/experiments/spine42/order.mjs：`order` 缺號時約束不執行
- [E5] research/experiments/spine43/t.mjs：4.2 檔在 4.3 下掉約束或拋錯
- [E6] research/experiments/spine42/phys0.mjs：0 長度骨的 rotate physics
- [E7] research/experiments/py/exp1.py、research/experiments/py/exp2.py：典獄長 `fore_r` 的 31 個線性 key 對 5 個 bezier key
- [E8] research/experiments/py/exp3.py：Cartoon Animation Filter 的對稱性
- [E9] research/experiments/py/exp4.py：二階動態
- [E10] research/experiments/py/exp5.py：physics 的 (f, ζ) 換算
- [E11] research/experiments/spine42/foldtest.mjs：摺疊偵測器的健全性測試

其他本地檔案：
- research/FINDINGS.md
- research/beat_table.json
- research/beats.py
- spinegen/curves.py
- research/data/warden.compressed.json（只在本地，gitignore）
- /home/user/chenyuping1998/wp/apps/HardTime/static/assets/spines/htCast/warden.json
- /home/user/chenyuping1998/wp/apps/HardTime/design/build_cast_spine.py
- /home/user/hacksaw-character-motion/gobananas_monkey/spines/bananditBandit/monkey.json
- /home/user/hacksaw-character-motion/gobananas_monkey/tools/generate_monkey_spine.mjs
- /home/user/hacksaw-character-motion/spineplay/spine.py
- /home/user/chenyuping1998/wp/.claude/skills/game-reskin/references/iron-jaw-transcript.md
