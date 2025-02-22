def click_str(str1: str):
    img = d.screenshot(format='opencv')
    result = easyocr_reader.readtext(img)
    for i in result:
        if str1 in str(i[1]):
            [x1, x2, x3, x4] = i[0]
            center = [int((x1[0]+x3[0])/2), int((x1[1]+x3[1])/2)]
            d.click(center[0], center[1])
            return True
    return False
# 副本的英文名稱


def battle_instance(battel_names: str):
    img = d.screenshot(format='opencv')
    result = easyocr_reader.readtext(img)
    find = False
    for i in result:
        if battel_names in i[1]:
            [x1, x2, x3, x4] = i[0]
            find = True
            break
    # 找到中心點
    if not find:
        print("沒有找到")
        return
    center = [int((x1[0]+x3[0])/2)+279, int((x1[1]+x3[1])/2)+70]
    print(center)
    d.click(center[0], center[1])
    time.sleep(1)
    get_time = time.time()
    if battel_names == "守衛":
        while (time.time()-get_time < 5):
            d.click(221, 772)
        click_white()
        time.sleep(1)
        d.click(267, 903)
        time.sleep(3)
        click_str("確定")
        return
    elif battel_names == "暗黑試煉":
        for i in range(3):
            click_str("快速通關")
            click_str("確認")
            click_white()
            click_str("重置")
            time.sleep(1)
            click_str("確定")
            time.sleep(1)
        d.click(272, 878)
        return
    elif battel_names == "探秘焚焰神殿":
        while (time.time()-get_time < 5):
            d.click(235, 739)
        time.sleep(1)
        click_white()
    else:
        while (time.time()-get_time < 5):
            d.click(225, 702)
        time.sleep(1)
        click_white()


def all_battle_instance(d):
    battel_names = ["突襲神燈小偷", "挑戰冰巢龍穴", "守衛", "顛倒時序塔", "探秘焚焰神殿", "暗黑試煉"]
    d.swipe(0.5, 0.2, 0.5, 0.8)
    time.sleep(3)
    for i in battel_names:
        battle_instance(i)
        time.sleep(5)
        d.swipe(0.5, 0.8, 0.5, 0.6)
        time.sleep(5)


battel_names = ["突襲神燈小偷", "挑戰冰巢龍穴", "守衛", "顛倒時序塔", "探秘焚焰神殿", "暗黑試煉"]
