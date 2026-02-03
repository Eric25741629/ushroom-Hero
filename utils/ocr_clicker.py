def click_str(str1: str, d, easyocr_reader):
    img = d.screenshot(format='opencv')
    # if not os.path.exists("other_str"):
    #     os.makedirs("other_str")
    # cv2.imwrite("other_str/other_str_{}.jpg".format(time.time()), img)
    result = easyocr_reader.readtext(img)
    for i in result:
        if str1 in str(i[1]):
            [x1, x2, x3, x4] = i[0]
            center = [int((x1[0]+x3[0])/2), int((x1[1]+x3[1])/2)]
            d.click(center[0], center[1])
            return True
    return False
