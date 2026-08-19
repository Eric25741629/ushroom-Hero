import time
import numpy as np
import os
import img_tools
from tools import click_white
from utils.logging_utils import logger


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
