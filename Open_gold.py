from sympy import N, det
import device as D
import time
import numpy as np
import easyocr
import uiautomator2 as u2
import winsound
import cv2
current_index = 0

#    'emulator-5568']
# 詞條縮寫對照表
abbrev_map = {
    '反擊': '反',
    '暴擊': '爆',
    '連擊': '連',
    '擊暈': '暈',
    '閃避': '閃',
    '回復': '回',
    '技能暴擊': '技',
    # 可依需求擴充
}

def get_abbrev(result_list):
    # 過濾掉不在縮寫表的詞條
    filtered = [abbrev_map.get(word, word) for word in result_list if word in abbrev_map]
    # 只取前兩個
    return ''.join(filtered[:2])
# 初始索引

def open_the_gold(device:D.device, reader):
    now =time.time()
    device.click(447,801)
    time.sleep(2)
    device.click(276,636)
    time.sleep(1)
    while time.time() - now < 1000:
        img = device.capture_screenshot()
        conditions = [abs(np.sum(img[576, 375]) - np.sum([180, 208, 219])) <= 10, abs(np.sum(img[700, 121]) - np.sum([178, 209, 218]))
                    <= 10, abs(np.sum(img[795, 408]) - np.sum([42, 155, 111])) <= 10, abs(np.sum(img[790, 217]) - np.sum([58, 65, 198])) <= 10]
        if all(conditions):
            time.sleep(1)
            img = device.capture_screenshot()
            # 使用 detail=1 來獲取信心程度
            ocr_results_with_confidence = reader.readtext(img[634:744, 291:367], detail=1)
            ocr_area=img[634:744, 291:367]
            # 檢查信心程度並保存低信心截圖
            for detection in ocr_results_with_confidence:
                confidence = detection[2]  # 信心程度
                text = detection[1]        # 識別的文字
                if confidence < 0.8:
                    # 保存低信心程度的截圖
                    import os
                    if not os.path.exists("ocr_fails"):
                        os.makedirs("ocr_fails")
                    timestamp = int(time.time() * 1000)  # 毫秒級時間戳
                    # 使用 easyocr 判斷的區塊範圍保存截圖
                    x1,y1,x2,y2 = map(int, detection[0][0] + detection[0][2] )
                    print(x1,y1,x2,y2)
                    all_stage = ocr_area[y1:y2, x1:x2]
                    filename = f"ocr_fails/now_stage_low_confidence_{confidence:.3f}_{timestamp}.jpg"
                    cv2.imwrite(filename, all_stage)
                    print(f"低信心程度截圖已保存: {filename} (文字: '{text}', 信心: {confidence:.3f})")
            
            # 提取純文字結果
            result = [detection[1] for detection in ocr_results_with_confidence]
            result = [i.replace("學", "擊").replace(
                "舉", "擊").replace("擘", "擊暈") for i in result]
            print(result)
            # 新增：顯示簡寫
            print("簡寫:", get_abbrev(result))
            if ('技能暴擊' in result and '反擊' in result) or ('技能暴擊' in result and '連擊' in result) or ('技能暴擊' in result and '暴擊' in result) or ('技能暴擊' in result and '閃避' in result):
                print("不是需要的")
                d.click(227, 798)
                time.sleep(1)
                img = device.capture_screenshot()
                conditions = [abs(np.sum(img[554, 221]) - np.sum([58, 65, 198]))
                            <= 10, abs(np.sum(img[550, 424]) - np.sum([42, 154, 112])) <= 10]
                if all(conditions):
                    d.click(204, 552)
                    time.sleep(1)
                    continue
            # or ('連擊' in result and '回復' in result)
            elif ('連擊' in result and '擊暈' in result) or ('連擊' in result and '反擊' in result) or ('連擊' in result and '回復' in result):
                print("不是需要的")
                d.click(227, 798)
                time.sleep(1)
                img = device.capture_screenshot()
                conditions = [abs(np.sum(img[554, 221]) - np.sum([58, 65, 198]))
                            <= 10, abs(np.sum(img[550, 424]) - np.sum([42, 154, 112])) <= 10]
                if all(conditions):
                    d.click(204, 552)
                    time.sleep(1)
                    continue
            elif ('擊暈' in result and '閃避' in result) or ('擊暈' in result and '連擊' in result) or ('擊暈' in result and '暴擊' in result):
                print("不是需要的")
                d.click(227, 798)
                time.sleep(1)
                img = device.capture_screenshot()
                conditions = [abs(np.sum(img[554, 221]) - np.sum([58, 65, 198]))
                            <= 10, abs(np.sum(img[550, 424]) - np.sum([42, 154, 112])) <= 10]
                if all(conditions):
                    d.click(204, 552)
                    time.sleep(1)
            elif ('反擊' in result and '擊暈' in result) or ('反擊' in result and '連擊' in result) or ('反擊' in result and '閃避' in result):
                print("不是需要的")
                d.click(227, 798)
                time.sleep(1)
                img = device.capture_screenshot()
                conditions = [abs(np.sum(img[554, 221]) - np.sum([58, 65, 198]))
                            <= 10, abs(np.sum(img[550, 424]) - np.sum([42, 154, 112])) <= 10]
                if all(conditions):
                    d.click(204, 552)
                    time.sleep(1)
                    continue
            elif ('暴擊' in result and '回復' in result) or ('暴擊' in result and '擊暈' in result) or ('暴擊' in result and '回復' in result):
                print("不是需要的")
                time.sleep(5)
                d.click(227, 798)
                time.sleep(1)
                img = device.capture_screenshot()
                conditions = [abs(np.sum(img[554, 221]) - np.sum([58, 65, 198]))
                            <= 10, abs(np.sum(img[550, 424]) - np.sum([42, 154, 112])) <= 10]
                if all(conditions):
                    d.click(204, 552)
                    time.sleep(1)
                    continue
            else:
                d.click(518,16)
                print("是需要的")
                time.sleep(1)
                d.click(419,720)
                time.sleep(3)
                d.click(272,796)
                time.sleep(1)
                d.click(281,350)
                time.sleep(2)
                img = device.capture_screenshot()
                now_stage =img[328:385,147:371]
                all_stage =img[328:832,147:371]
                if now_stage.shape[0] == 0 or now_stage.shape[1] == 0:
                    print("當前階段圖片無法識別，請檢查設備或截圖範圍")
                    continue
                
                # 安全地處理 OCR 結果，加入信心程度檢查
                now_ocr_results_with_confidence = reader.readtext(now_stage, detail=1)
                all_ocr_results_with_confidence = reader.readtext(all_stage, detail=1)
                
                # 檢查 now_stage 的信心程度
                for detection in now_ocr_results_with_confidence:
                    confidence = detection[2]
                    text = detection[1]
                    if confidence < 0.8:
                        import os
                        if not os.path.exists("ocr_fails"):
                            os.makedirs("ocr_fails")
                        timestamp = int(time.time() * 1000)
                        # 使用 easyocr 判斷的區塊範圍保存截圖
                        x_min, y_min, x_max, y_max = map(int, detection[0][0] + detection[0][2])
                        x1,x2,y1,y2 = map(int, detection[0][0] + detection[0][2])
                        ocr_area = all_stage[y1:y2, x1:x2]
                        filename = f"ocr_fails/now_stage_low_confidence_{confidence:.3f}_{timestamp}.jpg"
                        print(detection)
                        cv2.imwrite(filename, ocr_area)
                        # cv2.imwrite(filename, cropped_img)
                        print(f"當前階段低信心截圖已保存: {filename} (文字: '{text}', 信心: {confidence:.3f})")
                
                # 檢查 all_stage 的信心程度
                for detection in all_ocr_results_with_confidence:
                    confidence = detection[2]
                    text = detection[1]
                    if confidence < 0.8:
                        import os
                        if not os.path.exists("ocr_fails"):
                            os.makedirs("ocr_fails")
                        timestamp = int(time.time() * 1000)
                        # 使用 easyocr 判斷的區塊範圍保存截圖
                        x_min, y_min, x_max, y_max = map(int, detection[0][0] + detection[0][2])
                        x1,x2,y1,y2 = map(int, detection[0][0] + detection[0][2])
                        ocr_area = all_stage[y1:y2, x1:x2]
                        print(x1,x2,y1,y2)
                        print(ocr_area.shape)
                        filename = f"ocr_fails/all_stage_low_confidence_{confidence:.3f}_{timestamp}.jpg"
                        try:
                            cv2.imwrite(filename, ocr_area)
                        except Exception as e:
                            print(f"Error saving image: {e}")
                            continue
                        print(f"所有階段低信心截圖已保存: {filename} (文字: '{text}', 信心: {confidence:.3f})")
                
                # 提取純文字結果
                now_ocr_result = [s.replace("展反","爆反").replace('暈眩回',"暈回").replace('耳眩回',"暈回").replace('反爆_6',"反爆").replace('技爆耳',"技爆暈").replace('口爆',"回爆").replace('6',"").replace("技暈眩","技暈").replace("4","").replace('技眩',"技暈") if isinstance(s, str) else s for s in [detection[1] for detection in now_ocr_results_with_confidence]]
                all_result = [s.replace("展反","爆反").replace('暈眩回',"暈回").replace('耳眩回',"暈回").replace('反爆_6',"反爆").replace('技爆耳',"技爆暈").replace('口爆',"回爆").replace('6',"").replace("技暈眩","技暈").replace("4","").replace('技眩',"技暈") if isinstance(s, str) else s for s in [detection[1] for detection in all_ocr_results_with_confidence]]
                
                # 檢查是否有識別到文字
                if not now_ocr_result:
                    print("無法識別當前階段文字，跳過此次處理")
                    continue
                
                if not all_result:
                    print("無法識別所有階段文字，跳過此次處理")
                    continue
                    
                now_result = now_ocr_result[0]
                sample_result= get_abbrev(result).replace("爆閃","連閃").replace("閃閃","連閃").replace("閃爆","連閃").replace("閃連","連閃").replace("閃爆","連閃").replace("爆連","連爆").replace("回技","技回").replace("回閃","閃回").replace("爆反","反爆").replace("回暈","暈回").replace("回反","反回").replace("暈技","技暈")
                #找到index
                if sample_result in all_result:
                    index = all_result.index(sample_result)
                    print(f"找到簡寫 {sample_result} 在當前階段的索引: {index}")
                    if index ==0:
                        d.click(281,350)
                    else:
                        print(f"簡寫 {sample_result} 在當前階段的索引: {index}")
                        d.click(266,412 + (index-1) * 49)
                    time.sleep(1)
                    d.click(276,721)
                    time.sleep(1)
                    d.click(268,869)
                    time.sleep(1)
                    d.click(282,584)
                    time.sleep(1)
                    d.click(376,798)
                    time.sleep(0.3)
                    d.click(227, 798)
                    time.sleep(1)
                    img = device.capture_screenshot()
                    conditions = [abs(np.sum(img[554, 221]) - np.sum([58, 65, 198]))
                                <= 10, abs(np.sum(img[550, 424]) - np.sum([42, 154, 112])) <= 10]
                    if all(conditions):
                        d.click(204, 552)
                        time.sleep(1)
                    d.click(419,720)
                    time.sleep(3)
                    d.click(272,796)
                    time.sleep(1)
                    d.click(281,350)
                    time.sleep(1)
                    img = device.capture_screenshot()
                    all_stage =img[328:832,147:371]
                    
                    # 使用 detail=1 獲取信心程度
                    all_ocr_results_with_confidence = reader.readtext(all_stage, detail=1)
                    
                    # 檢查信心程度並保存低信心截圖
                    for detection in all_ocr_results_with_confidence:
                        confidence = detection[2]
                        text = detection[1]
                        if confidence < 0.8:
                            import os
                            if not os.path.exists("ocr_fails"):
                                os.makedirs("ocr_fails")
                            timestamp = int(time.time() * 1000)
                            filename = f"ocr_fails/final_stage_low_confidence_{confidence:.3f}_{timestamp}.jpg"
                            x1,x2,y1,y2 = map(int, detection[0][0] + detection[0][2])
                            all_stage = all_stage[y1:y2, x1:x2]
                            print(detection)
                            cv2.imwrite(filename, all_stage)
                            print(f"最終階段低信心截圖已保存: {filename} (文字: '{text}', 信心: {confidence:.3f})")
                    
                    # 提取純文字結果並處理
                    all_ocr_result = [s.replace("展反","爆反").replace('耳眩回',"暈回").replace('暈眩回',"暈回").replace('反爆_6',"反爆").replace('技爆耳',"技爆暈").replace('口爆',"回爆").replace('6',"").replace("技暈眩","技暈").replace("4","").replace('技眩',"技暈") if isinstance(s, str) else s for s in [detection[1] for detection in all_ocr_results_with_confidence]]
                    
                    # 檢查是否有識別到文字
                    if not all_ocr_result:
                        print("無法識別所有階段文字，跳過此次處理")
                        continue
                        
                    all_result = all_ocr_result
                    if now_result in all_result:
                        index = all_result.index(now_result)
                        print(f"找到簡寫 {now_result} 在當前階段的索引: {index}")
                    if index ==0:
                        d.click(281,350)
                    else:
                        print(f"簡寫 {now_result} 在當前階段的索引: {index}")

                        d.click(281,412 + (index-1) * 49)
                    time.sleep(1)
                    d.click(276,721)
                    time.sleep(1)
                    d.click(268,869)
                    time.sleep(1)
                    d.click(441,805)

                    time.sleep(1)
                    d.click(271,634)
                else:
                    print(f"簡寫 {sample_result} 未在當前階段找到")
                    print(all_result)

    d.click(447,801)
    time.sleep(2)
    d.click(273,560)
    time.sleep(2)

if __name__ == "__main__":
    reader = easyocr.Reader(['en', 'ch_tra'])

    current_device_ip = 'fc65396d'
    try:
        print(f"正在連接到設備: {current_device_ip}")
        d = u2.connect(current_device_ip)
        print(f"成功連接到設備: {current_device_ip}")
    except Exception as e:
        print(f"連接設備 {current_device_ip} 時發生錯誤: {e}")
    open_the_gold(d, reader)