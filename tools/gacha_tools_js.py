"""Injected-JS payload for the dashboard 「工具 優化類」gacha (抽卡) section.

PURE WS — sends the game's own draw command `0x0902` straight through the live
WebSocket (``netManager._cnet.sendMessage``) and reads the reply, the SAME path
as ``utils.web_game_api.WebGameAPI.call_raw``. It does NOT drive the cocos UI
(no button clicks, no result animation / popup), so a batch of draws is near
instant. Protocol live-decoded 2026-06-15 (小寶, CDP 9226):

    0x0902 (2306)  TX {1:type, 2:count}  RX {1:header, repeated 2:items}
        type 1=技能 2=同伴; count 15/35/999 (server deducts ticket cost)
    0x0402 (1026)  push carrying the spent ticket's NEW remaining qty
        ticket item_id: 技能=1012, 同伴=1013 (entry {1:item_id, 3:new_qty})
    0x0201 (513)   server reject (e.g. insufficient tickets) -> {1:error_code}

``DRAW_ONCE_JS`` returns a JSON string ``{ok, drawn, remaining, rejected,
error_code}``: ``drawn`` = #(top-level field-2 groups) in the 0x0902 reply,
``remaining`` = ticket balance read from the accompanying 0x0402 push (null if
not seen), ``rejected`` = a 0x0201 came back instead of a draw result. The
caller drives the drain ladder off ``remaining`` (no timeout-probing).
"""

# async (args) => JSON string. args = [type, count, ticketId, timeoutMs].
DRAW_ONCE_JS = r"""
async (args) => {
  const [type, count, ticketId, timeoutMs] = args;
  const nm = window.netManager;
  if (!nm || !nm._cnet) return JSON.stringify({ok:false, drawn:0, err:'no_cnet'});
  const sock = nm._cnet;
  if (typeof sock.sendMessage !== 'function' || typeof sock.reciveMsg !== 'function')
    return JSON.stringify({ok:false, drawn:0, err:'no_methods'});
  const CMD = 0x0902, ERR = 0x0201, INV = 0x0402;
  const enc = (v) => { const o=[]; v=v>>>0; while(v>0x7f){o.push((v&0x7f)|0x80); v>>>=7;} o.push(v&0x7f); return o; };
  const body = new Uint8Array([0x08, ...enc(type), 0x10, ...enc(count)]);
  const toU = (b) => { try {
    if (b instanceof Uint8Array) return b;
    if (b && b.buffer) return new Uint8Array(b.buffer, b.byteOffset||0, b.byteLength);
    if (Array.isArray(b)) return Uint8Array.from(b);
  } catch(e){} return null; };
  // top-level protobuf field walker -> cb(field, wire, value:int|Uint8Array)
  const walkTop = (u, cb) => { let i=0; while(i<u.length){
    let tag=0,s=0; while(i<u.length){const x=u[i++]; tag|=(x&0x7f)<<s; if(!(x&0x80))break; s+=7;}
    const f=tag>>>3, w=tag&7;
    if(w===0){ let v=0,vs=0; while(i<u.length){const x=u[i++]; v|=(x&0x7f)<<vs; if(!(x&0x80))break; vs+=7;} cb(f,0,v); }
    else if(w===2){ let ln=0,ls=0; while(i<u.length){const x=u[i++]; ln|=(x&0x7f)<<ls; if(!(x&0x80))break; ls+=7;} cb(f,2,u.subarray(i,i+ln)); i+=ln; }
    else if(w===1){ i+=8; } else if(w===5){ i+=4; } else break;
  } };
  const countItems = (u) => { let n=0; walkTop(u,(f,w)=>{ if(f===2&&w===2) n++; }); return n; };
  const ticketQty = (u) => { let q=null; walkTop(u,(f,w,v)=>{ if(f===2&&w===2){
    let id=null,n=null; walkTop(v,(sf,sw,sv)=>{ if(sf===1&&sw===0)id=sv; else if(sf===3&&sw===0)n=sv; });
    if(id===ticketId && n!==null) q=n; } }); return q; };
  const errCode = (u) => { let ec=null; walkTop(u,(f,w,v)=>{ if(f===1&&w===0) ec=v; }); return ec; };
  return await new Promise((resolve) => {
    const orig = sock.reciveMsg.bind(sock);
    let done=false, drawn=null, remaining=null;
    const finish = (obj) => { if(done) return; done=true; try{ sock.reciveMsg=orig; }catch(e){} resolve(JSON.stringify(obj)); };
    const settle = () => { if(drawn!==null) finish({ok:true, drawn:drawn, remaining:remaining, rejected:false}); };
    const tid = setTimeout(() => {
      if(drawn!==null) finish({ok:true, drawn:drawn, remaining:remaining, rejected:false});
      else finish({ok:false, drawn:0, err:'timeout'});
    }, timeoutMs||6000);
    sock.reciveMsg = function (c, b) {
      try {
        if(!done){
          if(c===CMD){ const u=toU(b); drawn = u?countItems(u):0; clearTimeout(tid); setTimeout(settle, 220); }
          else if(c===ERR){ const u=toU(b); clearTimeout(tid); finish({ok:false, drawn:0, rejected:true, error_code: u?errCode(u):null}); }
          else if(c===INV && ticketId){ const u=toU(b); if(u){ const q=ticketQty(u); if(q!==null) remaining=q; } }
        }
      } catch(e){}
      return orig(c, b);
    };
    try { sock.sendMessage(CMD, body); }
    catch(e){ clearTimeout(tid); finish({ok:false, drawn:0, err:String(e)}); }
  });
}
"""
