"""web_h5 共用 Cocos UI 文字/節點操作。

所有 JavaScript 都在單次 ``page.evaluate`` 內執行並回傳 JSON；不把頁面內
function 帶回 Python。ADB 呼叫端不應使用本模組，仍保留既有 OCR/座標流程。
"""
from __future__ import annotations

import time
from typing import Any, Iterable, Optional


_SNAPSHOT_JS = r"""([rootName]) => {
  if (typeof cc === 'undefined' || !cc.director) return {err:'no_cc', texts:[], views:[]};
  const scene = cc.director.getScene();
  if (!scene) return {err:'no_scene', texts:[], views:[]};
  const stack=[scene]; let root=null;
  while(stack.length){
    const n=stack.pop(); if(!n) continue;
    if(!root && (!rootName || n.name===rootName) && n.activeInHierarchy) root=n;
    (n.children||[]).forEach(c=>stack.push(c));
  }
  root = root || (!rootName ? scene : null);
  if(!root) return {err:'root_not_found', texts:[], views:[]};
  const texts=[], views=[], todo=[root];
  while(todo.length){
    const n=todo.pop(); if(!n || !n.activeInHierarchy) continue;
    if(/View$/.test(n.name||'')) views.push(n.name);
    const label=n.getComponent ? n.getComponent(cc.Label) : null;
    if(label && String(label.string||'').trim()) texts.push(String(label.string).trim());
    (n.children||[]).forEach(c=>todo.push(c));
  }
  return {texts, views};
}"""

_CLICK_TEXT_JS = r"""([text, rootName, exact, occurrence]) => {
  if (typeof cc === 'undefined' || !cc.director) return {clicked:false, err:'no_cc'};
  const scene=cc.director.getScene(); if(!scene) return {clicked:false, err:'no_scene'};
  const findRoot=()=>{
    if(!rootName) return scene;
    const s=[scene]; while(s.length){const n=s.pop(); if(!n)continue;
      if(n.name===rootName && n.activeInHierarchy) return n;
      (n.children||[]).forEach(c=>s.push(c));}
    return null;
  };
  const root=findRoot(); if(!root) return {clicked:false, err:'root_not_found'};
  const hits=[], s=[root];
  while(s.length){const n=s.pop(); if(!n || !n.activeInHierarchy)continue;
    const l=n.getComponent ? n.getComponent(cc.Label) : null;
    const value=l ? String(l.string||'').trim() : '';
    if(value && (exact ? value===text : value.includes(text))) hits.push({node:n,value});
    (n.children||[]).forEach(c=>s.push(c));}
  const hit=hits[occurrence||0]; if(!hit) return {clicked:false, err:'text_not_found'};
  let target=hit.node; let candidate=null;
  for(let i=0;i<8 && target;i++,target=target.parent){
    candidate=target;
    const button=target.getComponent ? target.getComponent(cc.Button) : null;
    const hasClick=target.hasEventListener ? target.hasEventListener('click') : false;
    if(hasClick){
      try{target.emit('click', target); return {clicked:true, text:hit.value, node:target.name};}
      catch(e){return {clicked:false, err:String(e), text:hit.value};}
    }
  }
  try {
    const w=candidate.worldPosition; const canvas=document.querySelector('canvas');
    if(w && canvas){ const r=canvas.getBoundingClientRect(); const ds=cc.view.getVisibleSize();
      return {clicked:false, fallback:{x:r.left+w.x/ds.width*r.width,
        y:r.top+(ds.height-w.y)/ds.height*r.height}, text:hit.value}; }
  } catch(e) {}
  return {clicked:false, err:'click_target_not_found', text:hit.value};
}"""

_CLICK_NODE_JS = r"""([name, rootName, occurrence]) => {
  if (typeof cc === 'undefined' || !cc.director) return {clicked:false, err:'no_cc'};
  const scene=cc.director.getScene(); if(!scene) return {clicked:false, err:'no_scene'};
  let root=scene;
  if(rootName){root=null; const roots=[scene]; while(roots.length){const n=roots.pop(); if(!n)continue;
    if(n.name===rootName && n.activeInHierarchy){root=n;break;}
    (n.children||[]).forEach(c=>roots.push(c));}}
  if(!root) return {clicked:false, err:'root_not_found'};
  const hits=[], stack=[root]; while(stack.length){const n=stack.pop(); if(!n||!n.activeInHierarchy)continue;
    if(n.name===name) hits.push(n); (n.children||[]).forEach(c=>stack.push(c));}
  const target=hits[occurrence||0]; if(!target) return {clicked:false, err:'node_not_found'};
  try{target.emit('click', target); return {clicked:true, node:target.name};}
  catch(e){return {clicked:false, err:String(e)};}
}"""


class CocosUI:
    """薄封裝：提供可測試、帶 timeout 的 Cocos 文字操作。"""

    def __init__(self, page: Any) -> None:
        self.page = page

    def snapshot(self, root: Optional[str] = None) -> dict:
        return self.page.evaluate(_SNAPSHOT_JS, [root]) or {}

    def texts(self, root: Optional[str] = None) -> list[str]:
        return list(self.snapshot(root).get("texts") or [])

    def has_text(self, text: str, *, root: Optional[str] = None) -> bool:
        return any(text in value for value in self.texts(root))

    def has_any_text(self, texts: Iterable[str], *, root: Optional[str] = None) -> Optional[str]:
        values = self.texts(root)
        for text in texts:
            if any(text in value for value in values):
                return text
        return None

    def click_text(
        self,
        text: str,
        *,
        root: Optional[str] = None,
        exact: bool = False,
        occurrence: int = 0,
    ) -> bool:
        result = self.page.evaluate(
            _CLICK_TEXT_JS, [text, root, bool(exact), int(occurrence)]
        ) or {}
        if result.get("clicked"):
            return True
        fallback = result.get("fallback") or {}
        if fallback:
            try:
                self.page.mouse.click(float(fallback["x"]), float(fallback["y"]))
                return True
            except Exception:
                return False
        return False

    def click_node(
        self, name: str, *, root: Optional[str] = None, occurrence: int = 0
    ) -> bool:
        result = self.page.evaluate(_CLICK_NODE_JS, [name, root, int(occurrence)]) or {}
        if result.get("clicked"):
            return True
        fallback = result.get("fallback") or {}
        if fallback:
            try:
                self.page.mouse.click(float(fallback["x"]), float(fallback["y"]))
                return True
            except Exception:
                return False
        return False

    def wait_for_text(
        self,
        texts: Iterable[str],
        *,
        root: Optional[str] = None,
        timeout: float = 8.0,
        poll: float = 0.25,
    ) -> Optional[str]:
        deadline = time.monotonic() + timeout
        wanted = tuple(texts)
        while time.monotonic() < deadline:
            found = self.has_any_text(wanted, root=root)
            if found:
                return found
            time.sleep(poll)
        return None

    def wait_until_text_gone(
        self,
        text: str,
        *,
        root: Optional[str] = None,
        timeout: float = 8.0,
        poll: float = 0.25,
    ) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self.has_text(text, root=root):
                return True
            time.sleep(poll)
        return False
