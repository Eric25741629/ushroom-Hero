"""中控台 (control_panel_app) 的 blueprint 拆分套件。

對外入口仍是根目錄 ``control_panel_app.py``（façade）：它建立 Flask app、
註冊本套件內的各 blueprint，並 re-export 測試所依賴的符號。
"""
