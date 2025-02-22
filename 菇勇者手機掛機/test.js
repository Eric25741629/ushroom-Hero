"auto";

// 1. 先請求截圖權限（首次使用需要授權）
if (!requestScreenCapture()) {
    toast("請授權截圖權限");
    exit();
}

// 2. 偵測是否出現公告
function detectAnnouncement() {
    // 截取螢幕
    var img = captureScreen();
    // 讀取模板圖（事先準備好 /sdcard/announcement_top.png）
    var template = images.read("/sdcard/autojs/knowing.png");
    if (!template) {
        log("讀取模板圖失敗，請確認路徑是否正確");
        return false;
    }

    // 在截圖中尋找模板圖
    var pos = images.findImage(img, template, {
        threshold: 0.8  // 相似度閾值，可依需求微調
    });

    // 用完即關閉圖檔，節省記憶體
    img.recycle();
    template.recycle();

    if (pos) {
        log("偵測到公告彈窗，座標: " + JSON.stringify(pos));
        return true;
    } else {
        log("未偵測到公告彈窗");
        return false;
    }
}

// 範例：若偵測到公告就嘗試點擊「關閉」按鈕
if (detectAnnouncement()) {
    // 這裡就要看「關閉」按鈕在螢幕上哪裡，或者再做一次影像比對
    // 假設你已知「關閉」按鈕座標是 (1000, 500)，可以直接：
    click(1000, 500);
    sleep(500);
}
