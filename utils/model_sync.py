import os
import shutil
import hashlib
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

def get_file_hash(file_path):
    """計算檔案的 MD5 雜湊值作為版本號"""
    if not os.path.exists(file_path):
        return None
    hasher = hashlib.md5()
    try:
        # 增加重試機制，應對 NAS 瞬間不穩
        for _ in range(3):
            try:
                with open(file_path, 'rb') as f:
                    for chunk in iter(lambda: f.read(8192), b""):
                        hasher.update(chunk)
                return hasher.hexdigest()
            except OSError:
                time.sleep(1)
        return None
    except Exception:
        return None

def ensure_local_model(remote_path, cache_name=".mushroom_cache"):
    """
    確保模型檔案在本機 SSD。
    使用暫存檔機制確保檔案完整性。
    """
    if not remote_path or not os.path.exists(remote_path):
        logger.warning(f"遠端路徑不存在: {remote_path}，將嘗試直接使用。")
        return remote_path

    # 定義本機快取目錄
    local_cache_dir = Path.home() / cache_name
    local_cache_dir.mkdir(parents=True, exist_ok=True)
    
    file_name = os.path.basename(remote_path)
    local_path = local_cache_dir / file_name
    temp_path = local_cache_dir / f"{file_name}.tmp"
    
    # 獲取遠端檔案大小
    try:
        remote_size = os.path.getsize(remote_path)
    except OSError as e:
        logger.error(f"無法讀取遠端檔案大小: {e}")
        return str(local_path) if local_path.exists() else remote_path

    # 開始比對版本
    remote_hash = get_file_hash(remote_path)
    if remote_hash is None:
        logger.warning("無法獲取遠端檔案雜湊值，跳過同步。")
        return str(local_path) if local_path.exists() else remote_path
        
    local_hash = get_file_hash(local_path) if local_path.exists() else None
    
    if local_hash != remote_hash:
        logger.info(f"偵測到模型需要更新: {file_name}")
        print(f"正在同步至本機 SSD: {local_path} ({remote_size / 1024 / 1024:.1f} MB)")
        
        try:
            # 如果上次殘留了暫存檔，先刪除
            if temp_path.exists():
                temp_path.unlink()
                
            # 實作進度條並寫入暫存檔
            with open(remote_path, 'rb') as f_src:
                with open(temp_path, 'wb') as f_dst:
                    copied = 0
                    chunk_size = 1024 * 1024 # 1MB chunks
                    while True:
                        buf = f_src.read(chunk_size)
                        if not buf:
                            break
                        f_dst.write(buf)
                        copied += len(buf)
                        
                        percent = (copied / remote_size) * 100
                        bar_len = 30
                        filled_len = int(bar_len * copied // remote_size)
                        bar = '█' * filled_len + '-' * (bar_len - filled_len)
                        print(f'\r[同步中] |{bar}| {percent:.1f}% ({copied/1024/1024:.1f}MB)', end='', flush=True)
            
            # 同步成功後，進行原子更名
            if temp_path.exists():
                if local_path.exists():
                    local_path.unlink()
                temp_path.rename(local_path)
                shutil.copystat(remote_path, local_path)
                print(f"\n✓ {file_name} 同步完成。")
            
        except Exception as e:
            print(f"\n✗ 同步失敗: {e}")
            if temp_path.exists():
                try: temp_path.unlink()
                except: pass
            return remote_path
    else:
        logger.info(f"✓ 本機快取已是最新版本: {file_name}")
        
    return str(local_path)
