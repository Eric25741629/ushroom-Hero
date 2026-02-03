import time
import datetime
import img_tools
def sea(ip, d):
    if "fc65396d" in ip:
        d.click(45, 360)
    else:
        d.click(45, 320)
    time.sleep(10)
    d.click(50, 775)
    time.sleep(2)
    d.click(284, 712)
    time.sleep(2)
    d.click(519, 28)  # click white
    time.sleep(2)
    d.click(267, 881)
    time.sleep(2)
    # 星期一~星期五 早上10點到晚上12點

    if 'emulator-5568' not in ip:
        d.click(200, 917)
        time.sleep(2)
        d.click(263, 785)
        time.sleep(2)
        d.click(519, 28)  # click white
        d.click(519, 28)  # click white
        time.sleep(1)
        d.click(500, 900)  # close
        time.sleep(3)
        d.click(70, 913)
        time.sleep(2)
        d.click(297, 929)
        time.sleep(3)
        d.click(249, 814)
        time.sleep(3)
        d.click(500, 900)  # close
        time.sleep(1)
    if datetime.datetime.now().weekday() < 5 and 10 <= datetime.datetime.now().hour < 24:
        for i in range(2):
            d.swipe(100,400,400,400)
        if img_tools.click_str_by_server(d,"資源"):
            img_tools.click_str_by_server(d,"駐守")
            img_tools.click_str_by_server(d,"開始航行")
            time.sleep(3)
        d.swipe(100,400,400,400)
        if img_tools.click_str_by_server(d,"遺跡"):
            img_tools.click_str_by_server(d,"進攻")
            img_tools.click_str_by_server(d,"開始航行")
            time.sleep(5)    
    d.click(500, 900)  # close

if __name__ == "__main__":
    from uiautomator2 import connect
    ip = "7fe98fc6"
    d = connect(ip)
    sea(ip, d)