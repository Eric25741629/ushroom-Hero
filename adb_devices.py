import subprocess
import shlex
import re
from typing import Union, List
ADB = "adb"  # 如果 adb 不在 PATH，请改成 adb.exe 的绝对路径


# ... 其他 adb_devices.py 中的程式碼 ...

def run_adb(cmd: Union[str, List[str]], device_serial: str = None) -> str:
    """
    在終端執行 adb 指令並返回輸出。
    cmd 可以是：
      - 一個完整的 shell 命令字串 (shlex 語法)
      - 已拆好的參數列表 (List[str])
    如果指定了 device_serial，就用 -s 參數鎖定設備。
    """
    base = ['adb']
    if device_serial:
        base += ['-s', device_serial]

    if isinstance(cmd, str):
        args = shlex.split(cmd)
    else:
        args = cmd

    full_cmd = base + args

    try:
        result = subprocess.run(
            full_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,             # 確保輸出被解碼為字串
            encoding='utf-8',      # 指定 UTF-8 編碼
            errors='replace',      # 替換無法解碼的字元
            check=False            # 不要自動為非零退出碼引發異常
        )
    except FileNotFoundError:
        # 處理 'adb' 命令本身未找到的情況
        raise RuntimeError("ADB command not found. Ensure 'adb' is in your system PATH.")

    if result.returncode != 0:
        # 如果 ADB 命令失敗，stderr 應該包含錯誤訊息
        error_message = result.stderr.strip() if result.stderr else "Unknown ADB error"
        raise RuntimeError(f"ADB Error (code {result.returncode}): {error_message}")

    # text=True 和成功的解碼應確保 stdout 是一個字串。
    # 如果 stdout 為 None (在此處不太可能)，strip() 會失敗。
    # 使用 text=True 和 encoding，它應該是一個字串 (可能為空)。
    return result.stdout.strip() if result.stdout is not None else ""

# ... 其他 adb_devices.py 中的程式碼 ...



# … 省略上面不变的部分 …

def list_users(device_serial: str = None) -> list[str]:
    raw = run_adb("shell pm list users", device_serial=device_serial)
    return re.findall(r"UserInfo\{(\d+):", raw)

def user_has_package(user: str, pkg: str, device_serial: str = None) -> bool:
    raw = run_adb(f"shell pm list packages --user {user}", device_serial=device_serial)
    return any(line.strip().endswith(pkg) for line in raw.splitlines())

def resolve_main_activity(pkg: str, device_serial: str = None) -> str:
    raw = run_adb(
        f"shell cmd package resolve-activity --brief "
        f"-c android.intent.category.LAUNCHER {pkg}",
        device_serial=device_serial
    ).strip()
    for line in raw.splitlines():
        if "/" in line:
            return line.strip()
    raise RuntimeError(f"无法解析 {pkg} 的主 Activity，输出：\n{raw}")

def launch_clone(pkg: str, clone_index: int, device_serial: str = None) -> str:
    # 把 device_serial 传给所有子调用
    users = list_users(device_serial=device_serial)
    clones = [u for u in users if user_has_package(u, pkg, device_serial=device_serial)]
    if not clones:
        raise RuntimeError(f"包 {pkg} 未在任何用户下安装")
    if not (1 <= clone_index <= len(clones)):
        raise ValueError(f"clone_index 超出范围 (1–{len(clones)})")
    target_user = clones[clone_index - 1]

    comp = resolve_main_activity(pkg, device_serial=device_serial)
    cmd = (
        f"shell am start --user {target_user} "
        f"-a android.intent.action.MAIN "
        f"-c android.intent.category.LAUNCHER "
        f"-n {comp}"
    )
    return run_adb(cmd, device_serial=device_serial)

# ===== 使用示例 =====
if __name__ == "__main__":
    pkg_name = "com.mxdzz.tw.and"
    try:
        output = launch_clone(pkg_name, 2)  # 启动第二个分身
        print("启动结果：", output)
    except Exception as e:
        print("操作失败：", e)
