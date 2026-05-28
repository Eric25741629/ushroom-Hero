import re
from adb_operations import run_adb

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
