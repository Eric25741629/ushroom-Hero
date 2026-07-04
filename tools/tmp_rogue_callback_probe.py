"""一次性探測:只用 cocos 場景樹 callback(不用 OCR/座標)驅動萬神試煉(rogue),
驗證 callback 定位能否完整跑一場。**不碰 battle/weekly_trials.py。**

用法:
  python tools/tmp_rogue_callback_probe.py state          # 看當前 active view
  python tools/tmp_rogue_callback_probe.py dump <viewName> # dump 某 view 的按鈕+label
  python tools/tmp_rogue_callback_probe.py click <label>   # 用 callback 觸發含該 label 的按鈕
  python tools/tmp_rogue_callback_probe.py clickpath <a/b/c># 用 callback 觸發指定路徑節點

輸出一律走 logs/_rgcb.log(避免 inline 顯示卡頓)。
"""
import sys, json, time

sys.dont_write_bytecode = True
from playwright.sync_api import sync_playwright

PORT = 9230
HOST = "acenetgame"
LOG = "logs/_rgcb.log"


def log(*a):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(" ".join(str(x) for x in a) + "\n")


def get_page(b):
    return next(p for c in b.contexts for p in c.pages
               if HOST in (p.url or "") and "pwa-sw" not in p.url)


# 觸發含 label 的最上層按鈕(反 z-order 走 NormalView + TopView)
CLICK_LABEL = r"""(label) => {
  const strOf=n=>{for(const c of(n._components||[])){if(c&&typeof c.string==='string')return c.string;}return '';};
  let hit=null;
  const walk=(n,d)=>{
    if(!n||!n.active||d>16||hit)return;
    const bc=n.getComponent&&n.getComponent('cc.Button');
    if(bc){let txt='';(function w(m,dd){if(dd>3||txt)return;const s=strOf(m);if(s)txt=s;for(const c of(m.children||[]))w(c,dd+1);})(n,0);
      if(txt.includes(label))hit=n;}
    const kids=n.children||[];for(let i=kids.length-1;i>=0;i--)walk(kids[i],d+1);
  };
  const ui=cc.director.getScene().getChildByName('UIRoot');
  for(const root of ['TopView','NormalView']){
    const r=ui.getChildByName(root); if(!r)continue;
    for(let i=r.children.length-1;i>=0&&!hit;i--)walk(r.children[i],0);
    if(hit)break;
  }
  if(!hit)return {ok:false,reason:'not found'};
  const path=(function p(x){return x.parent?p(x.parent)+'/'+x.name:x.name})(hit);
  const ep=hit.eventProcessor||hit._eventProcessor;
  const tgt=ep&&(ep.bubblingTarget||ep.bubblingTargets);
  const infos=tgt&&tgt._callbackTable&&tgt._callbackTable['click']&&tgt._callbackTable['click'].callbackInfos.filter(Boolean)||[];
  let n=0;for(const ci of infos){try{ci.callback.call(ci.target,hit.getComponent('cc.Button'));n++;}catch(e){return {ok:false,reason:'err '+e,path};}}
  return {ok:true,invoked:n,path};
}"""

DUMP_VIEW = r"""(viewName) => {
  const ui=cc.director.getScene().getChildByName('UIRoot');
  let v=null;
  for(const root of ['TopView','NormalView']){
    const r=ui.getChildByName(root); if(!r)continue;
    (function f(n){if(!n||v)return;if(n.name===viewName&&n.active)v=n;(n.children||[]).forEach(f);})(r);
  }
  if(!v)return {found:false};
  const strOf=n=>{for(const c of(n._components||[])){if(c&&typeof c.string==='string'&&c.string.trim())return c.string;}return '';};
  const out=[];
  const walk=(n,path,d)=>{if(!n||d>7)return;const s=strOf(n);const isBtn=n.getComponent&&n.getComponent('cc.Button');
    if(s||isBtn||/btn|Btn/.test(n.name||''))out.push({path,name:n.name,active:n.active,btn:!!isBtn,s});
    (n.children||[]).forEach(k=>walk(k,path+'/'+(k.name||'?'),d+1));};
  walk(v,viewName,0);return {found:true,nodes:out};
}"""

ACTIVE = r"""()=>{
  const ui=cc.director.getScene().getChildByName('UIRoot');
  const nv=ui.getChildByName('NormalView'); const tv=ui.getChildByName('TopView');
  return {normal:(nv.children||[]).filter(c=>c.active).map(c=>c.name),
          topActive:tv?(tv.children||[]).filter(c=>c.active).map(c=>c.name):[]};
}"""


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "state"
    arg = sys.argv[2] if len(sys.argv) > 2 else None
    open(LOG, "a").close()
    pw = sync_playwright().start()
    b = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{PORT}")
    page = get_page(b)
    ts = time.strftime("%H:%M:%S")
    if cmd == "state":
        log(ts, "STATE", json.dumps(page.evaluate(ACTIVE), ensure_ascii=False))
        page.screenshot(path="logs/_rgcb_state.png")
    elif cmd == "dump":
        log(ts, "DUMP", arg, json.dumps(page.evaluate(DUMP_VIEW, arg), ensure_ascii=False))
    elif cmd == "click":
        r = page.evaluate(CLICK_LABEL, arg)
        time.sleep(0.4)
        log(ts, "CLICK", arg, json.dumps(r, ensure_ascii=False),
            "| after:", json.dumps(page.evaluate(ACTIVE), ensure_ascii=False))
        page.screenshot(path="logs/_rgcb_after.png")
    else:
        log(ts, "unknown cmd", cmd)
    log("---done---")


if __name__ == "__main__":
    main()
