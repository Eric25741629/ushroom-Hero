---
created: 2026-07-30T15:22:24.914Z
title: Complete web H5 WS credential bootstrap
area: auth
priority: P1
files:
  - game_actions/ws_phase.py:575
  - game_actions/ws_phase.py:596
  - game_actions/ws_phase.py:771
  - utils/ws_ticket_refresh.py:55
  - ws_token/creds.py:30
  - ws_token/creds.py:90
  - bot_config.json:515
---

## Problem

`emulator-5558` 已能用 Playwright H5 正常登入並執行腳本，且設定
`ws_token.bootstrap_token=true`，但每次喚醒的 WS-first 階段仍因缺少
`auth_state/_auth_capture_emulator-5558.json` 而整輪降級 Playwright。

目前 WS-first 在瀏覽器啟動前執行。web_h5 缺 capture 時，只有 ADB 可達才會用
原生 App 冷啟動種完整憑證；ADB 不可達時直接進 `load_creds()` 並失敗。瀏覽器登入後
的 `refresh_from_device()` 可以讀取新 ticket，但初次建檔只得到 page 可見欄位，缺少
`uname` / `plat` 等 `Creds` 必填欄位，因此設計上仍是 partial seed，下一輪也不能登入
純 WS。現場 log 至少自 2026-07-29 起反覆出現相同降級。

使用者預期既然 H5 session 已在執行，腳本應在同一輪被動擷取完整 WS 登入資料，無需
手動執行 `tools/adb_token_login.py`。

## Solution

調查 H5 登入階段能取得完整 `role_login` payload 的位置，優先從既有 Playwright page
內被動擷取，不另開第二條 WS、不踢目前 session。可評估攔截登入封包、讀取登入快取的
其他模組，或把不會變動但 page 不可見的欄位從可信來源合併進 capture。

完成條件：

- 無 capture 的 web_h5 裝置成功登入 H5 後，自動產生 `load_creds()` 可讀的完整檔案。
- 下一次喚醒可直接完成 WS-first，不再出現 `no captured creds`。
- 擷取失敗仍維持 Playwright fallback，不可漏跑任務或打斷 wake loop。
- 補上完整 seed、partial seed、讀取失敗和既有 capture refresh 的目標測試。
