---
created: 2026-08-05T22:12:21.978Z
title: Build unauthenticated headless H5 battle calculator
area: runtime
files:
  - ws_token/rogue_fight.py:352
  - battle/weekly_trials.py:248
  - battle_calc/browser.py
---

## Problem

萬神 pure_ws 目前仍沿用已登入帳號的主 web_h5 初始化流程，之後才另外建立 B 頁。
這會多開不必要的登入瀏覽器，也可能讓同一帳號的 H5 session 與 Python WS client
同時在線。真正的戰鬥計算只需要一個不登入帳號、載入遊戲引擎的 headless H5 B 頁。

## Solution

建立獨立的未登入 headless H5 計算 runtime。Python `WSGameClient` 專責帳號的
enter、combat、result 與 over 協議；B 頁只接收戰鬥資料並回傳模擬結果。萬神專用
排程應直接啟動 WS + B 頁，正常路徑不初始化主 web_h5；只有明確允許的 fallback
才冷啟登入 H5。
