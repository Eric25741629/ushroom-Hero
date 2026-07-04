# 儀表板設計系統 + a11y/響應式 + 飛寵收藏 (branch: feat/ui-design-system)

> worktree `.claude/worktrees/design-system`,與正在跑的掛機(:5002)隔離。基底 main @ a12c6144。
> 合併使用者多則需求:設計系統/元件庫 + a11y(低視力/純鍵盤/多裝置)+ UX 審計 + 互動狀態正確性 + 飛寵收藏功能 + 審查 skill。
> 工作模式:每階段 = 「實作 → 對抗式複審/審計」工作流;審計先行。

## 總目標 (end-to-end goal)
把儀表板轉為統一、無障礙、響應式、狀態完備的設計系統:
1. 抽出可重用元件庫(tokens/components/app.js + head partial),a11y 與全互動狀態內建。
2. 6 個 template 全部走元件庫,順帶修各頁響應式/對比/鍵盤/語意/狀態缺陷。
3. 飛寵「收藏1/2/3」命名群組(自家+搭檔混合,手挑/隨機自動配)當旗艦新 UI。
4. 留下審查 skill 強制「新 UI 走元件庫 + a11y + 響應式 + 全狀態」。
完成標準 = 架構+實作+測試+live 驗證(桌面/平板/手機 + 鍵盤 + 語意)+ 各審核通過 + 合併 + 清 worktree。

## 證據基線(understand workflow,file:line 見 session)
- 無共用 static css/js;washi 主題複製 6 份/3 套命名 + ~111 hardcoded hex;按鈕4/modal4/table3/badge7;唯一 toast/apiGet 在 fly_pet(canonical);ws-session ~75 行 tools↔inventory 逐字重複。
- dashboard = iframe shell;子頁各自獨立 document → lib 每頁各自 link。
- 全域 no-store 會擋新 static;`_get_frontend_version()` 可當 ?v=;inventory/tools 沒傳。
- inline onclick 多 → 搬 JS 保留 window 全域名;modal class:dashboard `active` / 其餘 `show`。
- 三套通知:fly_pet toast / tools+inventory setStatus / dashboard alert(25 處)。
- 飛寵收藏缺口=命名持久化特定飛寵清單(自家 instance id + 搭檔 role_id 混合);現有 配種方案=criteria、設為基底/A/B=暫時填表;繁殖 send_66_27(base,A,B);搭檔 send_66_24→RolePetListBack data.list。

## 使用者回報(must-fix)
- 飛寵頁淡底配淡字,看不清楚 → tokens 強制文字達 AA + 修 .flypet-gallery --fg-* 淡色值。
  live 量化(問題冊 §3.2a):root-cause 4 個 token 涵蓋 ~5,900/6,776 失敗實例:
  1) .ec-1..7 詞條標籤粉彩文字(1.2–2.9:1)→ 各加深 ≥4.5(blue#1d6fb8/yellow#8a6d00/teal#0f766e/red#b91c1c/purple#6d28d9/pink#a21caf),色相移邊框。
  2) 灰字 #a59a87(2.77:1)→ #6f6657。 3) 橘字 #e06539(3.2–3.45)→ #b4471f。 4) 空星 #ddd2bd(1.45)→ #b59f78+輪廓。

## Phase 0 隔離 [done]
- [x] worktree + 分支

## Phase 1 基線審計(審計先行,launch + 走訪 + 評分問題冊)
- [ ] infra:從 worktree 用替代埠(:5003)安全啟動控制台,不撞 :5002 / 不干擾掛機(查 standalone 啟動 + 避免 device side-effect)
- [ ] live 捕捉:6 核心頁 × 桌面1440/平板768/手機375 截圖 + DOM + a11y tree + console;鍵盤 Tab/Enter/Space/Esc 走訪 + 焦點順序/可見性
- [ ] 平行審計工作流(對截圖/DOM/CSS/HTML 靜態分析 + live 結果):響應式(重疊/溢出/截斷/水平捲)、對比度+字體縮放、鍵盤+焦點、a11y 語意(img alt/icon-btn aria-label/form label/狀態 aria-live/role)、UX 流程(資訊層級/摩擦/缺回饋/易錯步驟)、狀態盤點(loading/empty/error 是否存在)
- [ ] 產出:依嚴重度(CRITICAL/HIGH/MED/LOW)評分的問題冊 + 具體修正 + 對應頁/元件

## Phase 2 元件庫地基(a11y + 全狀態內建)
- [ ] static/lib/tokens.css — 單一 washi token + 三套舊命名 alias + --scrim/--focus-ring/--shadow-*;對比度依 P1 修正(達 WCAG AA)
- [ ] static/lib/components.css — .btn/.modal/.data-table/.chip/.badge/.card/.status-pill/.toast/.sheet/.tabs/.form-control + 全狀態(hover/focus-visible/active/disabled/loading skeleton/empty/error)+ .spinner + reduced-motion + 響應式 helper
- [ ] static/lib/app.js — apiGet/apiPost/toast(aria-live)/openModal(focus-trap+Esc+還焦)/closeModal/confirmDialog/setLoading/esc/$/$$/setStatus/loadWebDevices/debounce/throttle/pollJob/renderLog/createWsSession/startFrontendVersionWatch/applyEmbedClass(window 全域保留)
- [ ] templates/_assets_head.html — fonts + lib link(?v=)+ defer;include 進 6 頭;含 skip-link/viewport meta 檢查
- [ ] control_panel_app.py no-store 放行 /static/ + immutable;routes 傳 frontend_version;_get_frontend_version 追蹤 lib
- [ ] tests/test_ui_library.py 契約測試
- [ ] 配 review 工作流:對抗式檢查 token 對比/元件狀態/a11y/全域名不破

## Phase 3 遷移(平行一檔一代理:換 lib + 套用 P1 各頁修復)
- [ ] inventory / tools_optimize / fly_pet / dashboard / readme_viewer / fly_pet_login
- [ ] 每頁:換 lib classes/helpers(行為不變)+ 修該頁響應式/對比/鍵盤/語意 + 補 empty/error/loading 狀態
- [ ] dashboard 高風險長尾(30 inline fetch/25 alert)安全範圍內改,其餘標 `ponytail:` debt
- [ ] 每頁配 review:契約測試 + live 該頁三尺寸 + 鍵盤

## Phase 4 飛寵收藏功能(建在 lib 上,旗艦新 UI)
- [ ] makeDeviceStore 收斂 localStorage;flypetGroups [{id,name,members:[{src:own,id}|{src:partner,role_id,id,config_id}]}]
- [ ] 卡片/detail 加入收藏 + 收藏管理面板(收藏1/2/3 CRUD,標「手選飛寵」避免撞 圖鑑收藏)
- [ ] 配種表單:從收藏填 A/B + 隨機自動挑(過濾 cooldown/breeding/locked);搭檔來源解析 + 失效標記
- [ ] 釐清搭檔可否當親代 + #bBase/#bFlyA/#bFlyB 槽位語意(必要時 live 看表單)
- [ ] 全狀態 + a11y;tests/test_fly_pet_groups.py;live 端到端(建立/加入/挑選/隨機配)

## Phase 5 跨切面複審(對改後重跑審計 + 殘留風險)
- [ ] 重跑響應式/a11y/UX/狀態審計 against worktree 改後版;確認 P1 問題冊逐項解決
- [ ] live 鍵盤全程走訪 + 語意(screen-reader semantics)+ 三尺寸無重疊/溢出/水平捲
- [ ] 記錄:問題 / 修復 / 殘留風險(交付文件 docs/)

## Phase 6 強制 + 收尾
- [ ] control_panel/_pb_walker_js.py 合併 3 份 protobuf walker
- [ ] 審查 skill `dashboard-ui-review`(合併三次「加審查 skill」:設計系統合規 + a11y + 響應式 + 互動狀態;含 checklist)
- [ ] 對抗式 code review(全 diff)+ 修 CRITICAL/HIGH
- [ ] 分段 commit(只 stage 本任務檔)
- [ ] 合併回 main + 移除 worktree(design-system + 已合併殘留 dashboard-nav-unify)

## Phase 7 效能優化(最後執行)
- [ ] 以效能工程師審視:瓶頸 / 低效邏輯 / 不必要重渲染
- [ ] 已知方向:5 個 setInterval 輪詢、字體載入 6 次、新 static immutable 快取(地基已修)、iframe 重複下載、apiFetch 去抖、渲染批次
- [ ] 回傳說明 + 優化後程式碼;量測前後(可用 Lighthouse perf / 載入數)

## Review
(待填:問題冊、修復、殘留風險、最終摘要)
