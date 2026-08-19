import time
import numpy as np
import os
import img_tools
from tools import click_white
from utils.logging_utils import logger


_GOODS_REWARD_VIEWS = ("GoodsGetView", "GoodsGetView2")


def close_goods_reward(page, timeout: float = 5.0) -> bool:
    """用 ``uiMgr.close`` 關閉「恭喜獲得」獎勵 popup。

    這個 popup 的標題是圖片，不一定存在可讀的 ``cc.Label``，所以不能
    用 OCR/文字搜尋當判斷依據。先用已確認的 view name 關閉，再輪詢 node
    inactive 作為結果證據；web_h5 失敗時回 False，不回 OCR。
    """
    views = list(_GOODS_REWARD_VIEWS)
    try:
        result = page.evaluate(r"""(views) => {
          const um = window.uiMgr;
          if (!um || typeof um.getView !== 'function' || typeof um.close !== 'function')
            return {found:false, closed:[], err:'uiMgr_close_unavailable'};
          const found = [], closed = [];
          for (const name of views) {
            try {
              const v = um.getView(name);
              if (v && v.node && v.node.active) {
                found.push(name);
                um.close(name);
                closed.push(name);
              }
            } catch (e) {}
          }
          return {found: found.length > 0, closed};
        }""", views) or {}
    except Exception as exc:
        logger.warning("Cocos 關閉恭喜獲得 popup 例外: %s", exc)
        return False

    if not result.get("found"):
        # popup 可能已被其他流程剛好關掉；這是成功的冪等結果。
        return True

    deadline = time.monotonic() + max(0.5, float(timeout))
    while time.monotonic() < deadline:
        try:
            active = page.evaluate(r"""(views) => {
              const um = window.uiMgr;
              if (!um || typeof um.getView !== 'function') return [];
              return views.filter(name => {
                try {
                  const v = um.getView(name);
                  return !!(v && v.node && v.node.active);
                } catch (e) { return false; }
              });
            }""", views) or []
        except Exception as exc:
            logger.warning("Cocos 驗證恭喜獲得 popup 關閉失敗: %s", exc)
            return False
        if not active:
            logger.info("Cocos 已關閉恭喜獲得 popup: %s", result.get("closed"))
            return True
        time.sleep(0.2)

    logger.warning("Cocos 恭喜獲得 popup 關閉 timeout: %s", result.get("closed"))
    return False


def claim_open_reward(page) -> bool:
    """直接由 Cocos node 領取目前已開啟的離線獎勵 popup。

    只處理已由 PageDetector 確認的 ``outlinePopView``。找不到按鈕時
    回 False 讓 web_h5 上層有限重試，不使用 OCR 猜座標。
    """
    try:
        result = page.evaluate(r"""() => {
          const find=(r,p)=>{
            let n=r;
            for(const x of p){
              if(!n||!n.children)return null;
              n=n.children.find(c=>(c.name||'')===x);
              if(!n)return null;
            }
            return n;
          };
          const b=find(cc.director.getScene(),
            ['UIRoot','NormalView','outlinePopView','root','content','btnStart']);
          if(!b || !b.activeInHierarchy)
            return {ok:false,err:'offline_reward_btn_not_active'};
          if(typeof b.hasEventListener === 'function' && !b.hasEventListener('click'))
            return {ok:false,err:'offline_reward_click_listener_missing'};
          b.emit('click', b);
          return {ok:true,node:b.name};
        }""") or {}
    except Exception as exc:
        logger.warning("Cocos 領取離線獎勵例外: %s", exc)
        return False
    if not result.get("ok"):
        logger.warning("Cocos 領取離線獎勵失敗: %s", result.get("err"))
        return False
    time.sleep(2)
    logger.info("Cocos 領取離線獎勵成功: %s", result)
    return True

def reward(d, easyocr_reader=None):
    """
    領取獎勵邏輯 (維持硬座標，使用 PaddleOCR/大腦判定)
    """
    # 使用原本的硬座標點擊進入
    d.click(162, 725)
    time.sleep(3)
    
    img = d.screenshot(format='opencv')
    
    # 這裡可以選擇用 OCR 判定或維持原本的顏色判定
    # 既然您希望保留前後端，我們可以用 OCR 判定是否出現領獎字樣
    # 但點擊位置維持硬座標
    result = img_tools.wait_for_any_text(d, ["領取", "放置獎勵"], timeout=2, click_if_found=False)
    
    if result:
        logger.info(f"偵測到獎勵介面: {result}")
        # 原本的硬座標顏色採樣點
        if abs(np.sum(img[328, 135])-np.sum([206, 237, 247])) > 12:
            if not os.path.exists("reward_get"):
                os.makedirs("reward_get")
            click_white(d)
            time.sleep(1)
            
        # 使用原本的硬座標點擊領取
        d.click(330, 725)
        time.sleep(2)
        click_white(d)
        time.sleep(1)
