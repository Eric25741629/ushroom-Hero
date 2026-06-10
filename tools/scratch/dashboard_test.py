import bot_state
import time
import os

def clear_screen():
    os.system('cls' if os.name == 'nt' else 'clear')

def simple_dashboard():
    """
    一個簡單的文字版中控台，用來測試監控與暫停功能。
    """
    print("=== 菇勇者 中控監控測試 (Ctrl+C 退出) ===")
    
    try:
        while True:
            states = bot_state.get_all_states()
            clear_screen()
            print(f"{ '設備 IP':<20} | {'狀態':<10} | {'目前任務':<15} | {'目前步驟':<30} | {'暫停'}")
            print("-" * 100)
            
            for ip, info in states.items():
                p_status = "YES" if info.get("paused") else "no"
                print(f"{ip:<20} | {info['status']:<10} | {info['task']:<15} | {info['step']:<30} | {p_status}")
            
            print("\n[指令說明] 輸入 'p IP' 暫停, 'r IP' 恢復 (例如: p emulator-5554)")
            print("注意: 由於這是同步輸入，測試時請另開視窗執行主程式。")
            
            time.sleep(2)
    except KeyboardInterrupt:
        print("\n退出中控台。")

if __name__ == "__main__":
    # 在實際應用中，UI 會是 NiceGUI 或 Qt
    # 這裡僅演示如何讀取狀態
    simple_dashboard()
