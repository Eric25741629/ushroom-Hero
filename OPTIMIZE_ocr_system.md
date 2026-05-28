# OCR 子系統優化建議

> 分析日期：2026-05-27
> 分析範圍：ocr_server.py、Open_gold_paddle_ocr.py、cnn_model.py、benchmark_screenshot.py、test_minigame_ocr.py、OCR/、OCR_model/、OCR_train/

---

## 一、現有架構概覽

```
┌─────────────────┐    HTTP/JSON    ┌──────────────────────┐
│  Open_gold_      │ ──────────────▶│  ocr_server.py       │
│  paddle_ocr.py   │◀──────────────│  (Flask, port 5001)  │
│  (V1, client)    │               │                      │
└─────────────────┘               │  ┌──────────────────┐ │
                                  │  │ OCRWorkerPool     │ │
                                  │  │ (N workers)       │ │
                                  │  └───────┬──────────┘ │
                                  └──────────┼────────────┘
                                             │
                                  ┌──────────▼────────────┐
                                  │  PaddleOCR v5         │
                                  │  PP-OCRv5_server_rec  │
                                  │  PP-OCRv5_server_det  │
                                  │  (local / official)   │
                                  └───────────────────────┘
```

**主要端點：**
- `POST /analyze_skill` — 技能組合辨識
- `POST /analyze_stage` — 關卡階段文字
- `POST /ocr` — 通用 OCR（回傳原始結果 + 座標）
- `GET /health` — 健康檢查

**模型配置：**
- 辨識模型：`OCR_model/v2`（PP-OCRv5_server_rec）
- 偵測模型：`OCR_model/det_v2`（PP-OCRv5_server_det）
- 支援 TensorRT 動態形狀加速
- 本地模型失敗時回退至 PaddleOCR 官方模型

---

## 二、問題診斷

### 2.1 訓練資料嚴重不足

`OCR_train/` 僅有 **24 組** .jpg/.txt 樣本，全部來自低信心度截圖（score 0.200~0.224）。這是最大的瓶頸：

- 樣本數量遠不足以微調 PP-OCRv5_server_rec（通常需要數千~數萬筆）
- 樣本信心度集中在 0.20~0.22 區間，缺乏 0.22~0.80 的中間地帶資料
- 標註格式為純文字（每行一個文字區域），缺少座標/邊界框資訊
- 樣本內容混雜：遊戲專用詞（「閃避」「暴擊」）與一般文字（「VIP」「公告」）混在一起

### 2.2 後處理規則維護成本高

`ocr_server.py` 和 `Open_gold_paddle_ocr.py` 各自維護一套文字修正規則，存在重複：

| 位置 | 規則數量 | 問題 |
|------|---------|------|
| `ocr_server.py` → `normalize_text()` | ~10 條 replace | 規則較少，只處理常見誤辨 |
| `ocr_server.py` → `STAGE_TEXT_REPLACEMENTS` | ~10 條 | 只在 stage 解析使用 |
| `Open_gold_paddle_ocr.py` → `REPLACEMENTS` | ~20 條 | 更完整，但與上者不同步 |
| `Open_gold_paddle_ocr.py` → `AFFIX_DICT` | 7 個詞條 | alias 系統較完善 |

兩處規則不同步，可能導致同一張圖片在不同呼叫路徑下得到不同結果。

### 2.3 OCR Worker Pool 效能隱憂

```python
# 單 worker 時使用全域鎖
if self.worker_count == 1:
    with ocr_lock:
        return self._single_engine.predict(input=img_roi)
```

- 預設 `OCR_WORKERS=1`，所有請求串行排隊
- 多 worker 時每個 worker 各自載入一份模型，記憶體消耗線性增長
- 缺乏請求去重/快取機制：相同圖片可能被重複推理

### 2.4 低信心截圖收集策略粗糙

```python
# ocr_server.py 中
MIN_OCR_FAIL_SCORE = 0.2
MAX_OCR_FAIL_SCORE = 0.8
# 只保存 0.2~0.8 之間的截圖，上限 10000 張
```

- 沒有按文字類型分類收集（技能詞 vs 數字 vs 一般 UI 文字）
- 沒有去重機制（同一位址/同一文字的截圖會重複保存）
- 標註 .txt 只有文字內容，缺少 OCR 回傳的 score、bbox 等元資料

### 2.5 硬編碼 ROI 與像素比對

`Open_gold_paddle_ocr.py` 中大量硬編碼：

```python
_SKILL_ROI = (slice(634, 744), slice(291, 367))
_ROLLED_ROI_BOXES = [(645, 675, 295, 439), (696, 724, 295, 439)]
LAMP_SELL_PAGE_PIXEL_PROFILES = (...)
```

- ROI 座標依賴特定解析度（540×960），換設備/解析度即失效
- 像素比對用於判斷頁面狀態（`is_lamp_sell_page`、`is_lamp_ready_page`），對遊戲更新極度脆弱

### 2.6 圖片解碼重試邏輯分散

`analyze_skill` 和 `analyze_stage` 各自實作圖片解碼重試，且 `analyze_stage` 中有 bug：

```python
# analyze_stage 裡先做了一次 base64.b64decode，然後又在迴圈裡重做
img_data = base64.b64decode(data['image'])  # ← 第一次，結果被丟棄
nparr = np.frombuffer(img_data, np.uint8)
img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
# 然後又進迴圈重試...
```

---

## 三、優化建議

### 3.1 訓練資料管理（優先級：🔴 高）

#### 3.1.1 建立系統化的資料收集流程

```python
# 建議：在 save_low_confidence_screenshot 中加入結構化 metadata
def save_low_confidence_screenshot(img, poly, text, score, stage_type):
    # ... 現有邏輯 ...
    
    # 新增：保存 metadata JSON
    meta = {
        "text": text,
        "score": score,
        "bbox": [int(x1), int(y1), int(x2), int(y2)],
        "stage_type": stage_type,
        "timestamp": timestamp,
        "image_shape": list(img.shape),
    }
    meta_file = filename.replace(".jpg", ".json")
    with open(meta_file, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False)
```

#### 3.1.2 分類收集策略

按用途分資料夾，優先收集高價值樣本：

```
OCR_train/
├── skill_names/      # 技能詞：暴擊、連擊、閃避、擊暈...（最重要）
├── numbers/          # 數字：機率百分比、傷害值、等級
├── stage_names/      # 關卡名稱：反爆、連閃、技回...
├── ui_text/          # UI 文字：公告、VIP、加速...
└── general/          # 其他
```

#### 3.1.3 去重與增量收集

- 對相同文字+相似 bbox 的截圖做去重（hash 或 perceptual hash）
- 設定每日收集上限，避免磁碟爆滿
- 收集到一定量後自動觸發訓練提醒

#### 3.1.4 微調訓練流程

當收集到 **500+ 筆**技能相關樣本後，可考慮微調：

```bash
# OCR/test.py 中已有訓練指令框架：
python PaddleOCR/tools/train.py \
  -c configs/rec/PP-OCRv5/PP-OCRv5_server_rec.yml \
  -o Global.pretrained_model=./PP-OCRv5_server_rec_pretrained.pdparams \
  Train.dataset.label_file_list='["dataset/rec/rec_gt_train.txt"]' \
  Eval.dataset.label_file_list='["dataset/rec/rec_gt_val.txt"]' \
  Global.character_dict_path=ppocr/utils/dict/my_min_dict.txt
```

**關鍵：** `my_min_dict.txt` 應只包含遊戲中實際出現的字元（數字、技能用字、常用標點），縮小辨識空間可提升準確率。

### 3.2 統一後處理層（優先級：🔴 高）

#### 3.2.1 抽取共用修正模組

建立 `ocr_postprocess.py`，統一所有文字修正邏輯：

```python
# ocr_postprocess.py
REPLACEMENTS = [
    # 合併 ocr_server.py 和 Open_gold_paddle_ocr.py 的所有規則
    (" ", ""), ("\n", ""), ("\t", ""),
    ("攻擎", "攻擊"), ("擎量", "擊暈"), ("擊量", "擊暈"),
    ("暴馨", "暴擊"), ("暴撃", "暴擊"), ("爆擊", "暴擊"),
    ("撃", "擊"), ("學", "擊"), ("舉", "擊"),
    ("量", "暈"), ("額", "額"), ("閃遊", "閃避"),
    ("回复", "回復"), ("??", "連擊"), ("連?", "連擊"),
    # ... 其餘規則
]

AFFIX_DICT = {
    "技能暴擊": {"code": "技", "aliases": ["技能暴擊", "技能爆擊", "技暴"]},
    "反擊": {"code": "反", "aliases": ["反擊", "反"]},
    # ...
}

def normalize_text(text: str) -> str:
    """統一文字修正入口"""
    ...

def text_to_skill_code(text: str) -> Optional[str]:
    """統一技能代碼解析"""
    ...
```

#### 3.2.2 ocr_server.py 改用共用模組

```python
from ocr_postprocess import normalize_text, text_to_skill_code
```

### 3.3 推理效能優化（優先級：🟡 中）

#### 3.3.1 啟用 TensorRT 加速

`inference.yml` 已配置 TensorRT 動態形狀，但需確認實際啟用：

```bash
# 確認 PaddleOCR 是否以 TensorRT 模式初始化
# 在 ocr_server.py 的 create_ocr_engine() 中加入：
OCR_LOCAL_ENGINE_CONFIG["use_tensorrt"] = True  # 若環境支援
```

#### 3.3.2 請求級快取

對短時間內的相同圖片做快取（hash-based）：

```python
from functools import lru_cache
import hashlib

_cache = {}
_CACHE_TTL = 30  # 秒

def cached_predict(img_roi, cache_key=None):
    if cache_key is None:
        cache_key = hashlib.md5(img_roi.tobytes()).hexdigest()
    now = time.time()
    if cache_key in _cache:
        result, ts = _cache[cache_key]
        if now - ts < _CACHE_TTL:
            return result
    result = ocr_pool.predict(img_roi)
    _cache[cache_key] = (result, now)
    return result
```

#### 3.3.3 調整 Worker 數量

建議根據 GPU 數量和記憶體調整：

```bash
# 環境變數
OCR_WORKERS=2          # 有 GPU 時建議 2~4
OCR_INFER_TIMEOUT=15   # 降低超時，快速失敗
```

#### 3.3.4 圖片預處理優化

在送入 OCR 前做適當的預處理，可提升辨識率同時降低推理成本：

```python
def preprocess_for_ocr(img):
    """針對遊戲 UI 文字的預處理"""
    # 1. 放大到合適尺寸（PP-OCRv5 最佳輸入高度 48px）
    h, w = img.shape[:2]
    if h < 32:
        scale = 32 / h
        img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    
    # 2. 增強對比度（遊戲 UI 常有半透明背景）
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    l = clahe.apply(l)
    img = cv2.merge([l, a, b])
    img = cv2.cvtColor(img, cv2.COLOR_LAB2BGR)
    
    return img
```

### 3.4 錯誤率改善（優先級：🟡 中）

#### 3.4.1 針對性詞彙增強

遊戲中高頻但 OCR 容易出錯的詞彙，可製作「校正字典」：

```python
CORRECTION_DICT = {
    # OCR 誤辨 → 正確詞（基於編輯距離 + 語境）
    "暴馨": "暴擊",
    "擊量": "擊暈",
    "閃遊": "閃避",
    "攻擎": "攻擊",
    # 數字相關
    "O": "0",  # 字母 O → 數字 0
    "l": "1",  # 小寫 l → 數字 1
}

def post_correct(text, context="skill"):
    """基於上下文的後修正"""
    for wrong, right in CORRECTION_DICT.items():
        text = text.replace(wrong, right)
    return text
```

#### 3.4.2 結果置信度加權

在 `extract_ocr_results` 中，對多個文字區域的結果做交叉驗證：

```python
def extract_skill_with_confidence(ocr_results):
    """帶置信度的技能提取"""
    candidates = []
    for r in ocr_results:
        text = r.get("text", "")
        score = r.get("score", 0.0)
        skill = text_to_skill_code(text)
        if skill:
            candidates.append((skill, score, text))
    
    if not candidates:
        return None, 0.0
    
    # 按置信度排序，取最高
    candidates.sort(key=lambda x: x[1], reverse=True)
    return candidates[0][0], candidates[0][1]
```

#### 3.4.3 多模型投票（進階）

若資源允許，可同時使用 PaddleOCR 和 Tesseract 做投票：

```python
def ocr_vote(img_roi):
    """多引擎投票"""
    results_paddle = ocr_pool.predict(img_roi)
    # results_tesseract = tesseract_ocr(img_roi)  # 可選
    # 取兩者一致的結果，或取置信度較高者
    return results_paddle
```

### 3.5 架構層面優化（優先級：🟢 低）

#### 3.5.1 統一圖片解碼

將散落在各端點的圖片解碼邏輯統一為 middleware：

```python
@app.before_request
def decode_image_middleware():
    """統一圖片解碼"""
    if request.endpoint in ('analyze_skill', 'analyze_stage', 'ocr_general'):
        data = request.get_json(silent=True) or {}
        if 'image' in data:
            img, err = decode_base64_image(data['image'])
            if img is None:
                return jsonify({'success': False, 'message': f'圖片解碼失敗: {err}'}), 400
            request._ocr_image = img
```

#### 3.5.2 ROI 配置外部化

將硬編碼的 ROI 座標移到配置檔：

```yaml
# ocr_rois.yaml
resolution: [540, 960]
skill_roi: [291, 367, 634, 744]   # x1, y1, x2, y2
rolled_rois:
  - [295, 439, 645, 675]
  - [295, 439, 696, 724]
original_rois_computer:
  - [292, 439, 420, 450]
  - [292, 439, 460, 490]
original_rois_phone:
  - [292, 439, 400, 430]
  - [292, 439, 450, 480]
```

#### 3.5.3 像素比對 → CNN 分類器替代

`cnn_model.py` 已有 10 類的 SimpleCNN，但目前只用於畫面分類。可擴展為：

- 用 CNN 判斷當前頁面狀態（取代 `is_lamp_sell_page` 的像素比對）
- 增加更多頁面狀態類別
- 比硬編碼像素值更抗遊戲更新

### 3.6 監控與可觀測性（優先級：🟢 低）

#### 3.6.1 加入 Prometheus 指標

```python
from prometheus_client import Counter, Histogram, Gauge

ocr_requests = Counter('ocr_requests_total', 'Total OCR requests', ['endpoint', 'status'])
ocr_latency = Histogram('ocr_latency_seconds', 'OCR inference latency')
ocr_confidence = Histogram('ocr_confidence', 'OCR confidence scores', buckets=[0.1, 0.2, 0.3, 0.5, 0.7, 0.9, 1.0])
```

#### 3.6.2 結構化日誌

```python
import structlog
log = structlog.get_logger()

log.info("ocr_result", 
    text=result["text"], 
    score=result["score"], 
    endpoint=request.endpoint,
    latency_ms=elapsed_ms)
```

---

## 四、優先順序建議

| 順序 | 項目 | 預期效果 | 工作量 |
|------|------|---------|--------|
| 1 | 統一後處理層 | 消除兩套規則不同步的 bug | 半天 |
| 2 | 系統化訓練資料收集 | 為後續微調打基礎 | 1 天 |
| 3 | 圖片預處理增強 | 直接提升低信心度樣本辨識率 | 半天 |
| 4 | 請求級快取 | 減少重複推理，降低延遲 | 半天 |
| 5 | ROI 配置外部化 | 提升可維護性，支援多解析度 | 1 天 |
| 6 | 微調訓練（資料充足後） | 針對性提升遊戲文字辨識率 | 2~3 天 |
| 7 | TensorRT 加速確認 | 推理速度提升 2~5 倍 | 半天 |
| 8 | 監控指標 | 可觀測性，方便後續調優 | 1 天 |

---

## 五、快速修復清單

以下是可以立即修復的小問題：

1. **`analyze_stage` 中的圖片解碼 bug**：移除多餘的第一次 `base64.b64decode`，改用 `decode_base64_image()` 共用函式
2. **`ocr_server.py` 的 `SKILL_MAP` 重複 key**：`'反擊': '反'` 和 `'反': '反'` 合併為單一映射
3. **`Open_gold_paddle_ocr.py` 標記為 DEPRECATED**：確認所有設備已遷移到 V2 後可刪除
4. **`benchmark_screenshot.py` 中的 `time.sleep(5)` bug**：這是已知的 bugged method，benchmark 本身已標記為模擬用
5. **`test_minigame_ocr.py` 依賴 `img_tools`**：確認 `img_tools.analyze_skill_via_http` 是否已統一使用共用模組

---

## 六、附錄：關鍵程式碼位置速查

| 功能 | 檔案 | 函式/區塊 |
|------|------|----------|
| OCR 引擎初始化 | `ocr_server.py` | `create_ocr_engine()` |
| Worker Pool | `ocr_server.py` | `class OCRWorkerPool` |
| 技能解析 | `ocr_server.py` | `text_to_skill()`, `get_skill_combo()` |
| 文字修正（V1） | `ocr_server.py` | `normalize_text()`, `normalize_stage_text()` |
| 低信心截圖保存 | `ocr_server.py` | `save_low_confidence_screenshot()` |
| 文字修正（V2） | `Open_gold_paddle_ocr.py` | `REPLACEMENTS`, `normalize_text()` |
| 技能詞典 | `Open_gold_paddle_ocr.py` | `AFFIX_DICT`, `ALIAS_TO_CODE` |
| 機率比對 | `Open_gold_paddle_ocr.py` | `compare_skill_pairs()` |
| 面板解析 | `Open_gold_paddle_ocr.py` | `extract_panel_and_entries()` |
| 訓練指令 | `OCR/test.py` | PaddleOCR train command |
| 模型設定 | `OCR_model/v2/inference.yml` | PP-OCRv5_server_rec |
| 偵測設定 | `OCR_model/det_v2/inference.yml` | PP-OCRv5_server_det |
| CNN 分類器 | `cnn_model.py` | `SimpleCNN`, `predict_image()` |
| 截圖 Benchmark | `benchmark_screenshot.py` | Playwright latency 測試 |
