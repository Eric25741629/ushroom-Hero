# Dashboard 分頁互切 + 統一暖色風格

分支：`worktree-dashboard-nav-unify`（worktree，base = feat/overnight-2026-06-14 tip 51666d9a，含暖色 washi dashboard）

## 使用者決策
- 切換方式：**內嵌 iframe 分頁**（沿用 dashboard 既有 `page-readme` 的 `war-room-shell`+`war-room-frame` lazy iframe 範式），側欄常駐、無重載。
- 統一風格：**全部暖色**，三子頁(飛寵/倉庫/工具)向主控 washi/珊瑚橘看齊，主控不動。

## 暖色 canonical 映射（鏡像自 dashboard.html :root）
| 角色 | 值 |
|------|----|
| 頁底 bg | #f4efe6 |
| 凸面 panel/card | #ffffff |
| 凹面 surface2 | #faf6ee |
| 三級面/input | #fbf7ee |
| 四級面 | #f0e9da |
| 細線 border | #ece3d3 |
| 強線 border2 | #ddd2bd |
| 主文字 ink | #2b2620 |
| 次文字 muted | #6f6657 |
| 淡文字 | #a59a87 |
| 強調 accent(coral) | #e0653a |
| accent hover/deep | #c14f2a |
| accent wash | #fbe6dc |
| 成功/online mint | #3fb389 |
| warn amber | #e0a939 |
| danger rose | #e26b86 / deep #b8455f |
| info sky | #4aa0dd |
| coral 上文字 | #fff |
| modal 遮罩 | rgba(43,38,32,.45) |
| 陰影 | rgba(70,52,28,.12~.22) |
| 字體 | body=Manrope, 標題=Sora（+Google Fonts link） |

## 待辦
- [ ] D1 dashboard.html：3 個 `<a href>` nav → `switchPage('flypet'/'inventory'/'tools')` button
- [ ] D2 dashboard.html：新增 3 個 `page-*` iframe 容器（仿 page-readme）
- [ ] D3 dashboard.html：switchPage() 加 3 分支 + lazy src + loaded flags
- [ ] D4 commit 里程碑（dashboard 接線）
- [ ] R1 inventory.html：:root 暖色重映射 + 硬編色修正 + 字體 + embed 隱藏自身 header
- [ ] R2 tools_optimize.html：同上
- [ ] R3 fly_pet.html（+ fly_pet_login.html）：同上（surface 變數多、style block 最大）
- [ ] V  驗證：4 頁渲染、iframe 切換、embed 模式隱藏 header、暖色一致
- [ ] commit + 收尾

## embed 模式（子頁在 iframe 內隱藏自身 header）
每子頁 `<head>` 加：`<script>if(window.top!==window.self)document.documentElement.classList.add('embedded');</script>`
CSS：`html.embedded header,html.embedded .topbar{display:none}`（fly_pet 是 .topbar，inventory/tools 是 header）

## Review
（完成後補）
