"auto";

// 引入 Android 類別
importClass(android.app.KeyguardManager);
importClass(android.content.Context);

// 定義要啟動的應用包名
var appPackage = "com.mxdzz.tw.and";

// 每 8 小時執行一次 (8 * 60 * 60 * 1000 毫秒)
var interval = 8 * 60 * 60 * 1000;

// 設定下一次啟動的時間（從腳本啟動時計算）
var nextLaunchTime = Date.now();

while (true) {
    if (Date.now() >= nextLaunchTime) {
        // 1. 如果螢幕亮著，持續等待直到休眠
        while (device.isScreenOn()) {
            log("螢幕亮著，等待休眠中...");
            sleep(1000);
        }

        // 2. 使用 KeyguardManager 判斷是否處於鎖定狀態
        var km = context.getSystemService(Context.KEYGUARD_SERVICE);
        if (km.inKeyguardRestrictedInputMode()) {
            log("裝置處於鎖定狀態，開始解鎖...");
            // 喚醒螢幕
            device.wakeUp();
            sleep(500);

            // 根據螢幕尺寸動態計算滑動解鎖的起點與終點
            var screenWidth = device.width;
            var screenHeight = device.height;
            // 例如：從螢幕下方中間滑動到上方中間
            var startX = screenWidth / 2;
            var startY = screenHeight * 0.85;
            var endX   = screenWidth / 2;
            var endY   = screenHeight * 0.15;

            log("解鎖滑動: 從 (" + startX + ", " + startY + ") 到 (" + endX + ", " + endY + ")");
            swipe(startX, startY, endX, endY, 300);
            sleep(1000);
        }

        // 啟動應用
        app.launch(appPackage);
        id("app2").findOne().click()
        log("應用已於 " + new Date() + " 啟動");

        // 計算下一次啟動的時間
        nextLaunchTime += interval;
    }
    sleep(20000);
    // /sdcard/autojs/knowing.png
    let pos = detectAnnouncement(templatePath = "/sdcard/autojs/knowing.png");
    if (pos) {
        // 發現公告彈窗後，嘗試點擊已知「關閉」按鈕座標
        click(1000, 500);
        sleep(500);
        // 再次確認是否成功關閉，也可以再呼叫 detectAnnouncement() 來驗證
        log("嘗試關閉公告彈窗");
    } else {
        log("無公告彈窗，繼續流程...");
    }
    pos = detectAnnouncement(templatePath = "/sdcard/autojs/2hourreward.png.png");
    if (pos) {
        // 發現公告彈窗後，嘗試點擊已知「關閉」按鈕座標
        click(1000, 500);
        sleep(500);
        // 再次確認是否成功關閉，也可以再呼叫 detectAnnouncement() 來驗證
        log("嘗試關閉公告彈窗");
    } else {
        log("無公告彈窗，繼續流程...");
    }
}
