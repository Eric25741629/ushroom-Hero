# Unused/commented out code from new_main_before20250514.py

# --- Unused Imports ---
from token import OP
from requests import get
# from oralce_manger import oralce

# --- Unused Global Variables ---
ADB = "adb"
boss_time = 0
reward_time = 0
err = 0
other_time = 0
one_day_action = 0
seed_timme = 0
check = True

# --- Commented out Functions ---
# def load_cnn_model(model_path, num_classes=10):
#     logger.warning("即將廢棄")
#     # 載入模型
#     model = cnn_model.SimpleCNN(num_classes=num_classes)
#     model.load_state_dict(torch.load(model_path))
#     model.eval()  # 設定為評估模式
#     return model

# --- Unused comments ---
# ...已移至 img_tools.py，請使用 img_tools.check_red_dot ...
# ...已移至 img_tools.py，請使用 img_tools.save_stage_debug_image ...


# --- Commented out code from if __name__ == "__main__": ---
# d = u2.connect('emulator-5560')
# d_list = ['emulator-5562',  'emulator-5558',  'emulator-5556','3a8d31f2','fc65396d']
# d_list = ['emulator-5560']
# main("fc65396d", easyocr_reader,Cnn_model,oralce_cnn_model)
# ocr = 1 # This was also unused

# --- Commented out code from the main loop ---
# if red_envelope.check_red_in_pic(img):
# red_envelope.open_red_envelope(d)

# if stage == "主頁面" and current_time.tm_hour % 4 == 0:
#     assistant_manager.go_to_get_assistant()

# if stage == "主頁面" and ip != "emulator-5558":
#     state_manager.check_and_change_state()

# if random.random()<0.7 and get_stage(d, Cnn_model, easyocr_reader) == "主頁面" and "fc65396d" not in ip:
#     d.press("back")  # 按下返回鍵
#     #點擊退出遊戲
#     click_str("確認", d, easyocr_reader)

# new_battle.run_weekly_cloud_pre_single(d,ip,name='因仔仙')
# new_battle.run_saturday_help_single(d, 'emulator-5558')
# if ip != "emulator-5558" and ip != "emulator-5554":
# new_battle.run_weekly_cloud_fighting_single(d,ip)
