import os

from config import MIN_FILE_SIZE_MB
from utils import get_file_size_mb, move_file
from mode_1_2 import process_mode_1_2


# ============================================================
# 模式4（极速模式）
# 逻辑：
#   < 100MB → 直接移入成功目录（无处理）
#   ≥ 100MB → 套用模式1逻辑
# 特点：最小化磁盘 I/O，适合快速整理
# ============================================================
def process_mode_4(filepath, success_dir, failed_dir, log_callback=None, progress_callback=None,
                   auto_mode=True, custom_crf=23, gpu_mode='off'):
    original_size = get_file_size_mb(filepath)

    if log_callback:
        log_callback("  原始大小: %.1f MB" % original_size)

    # < 100MB → 直接移入成功目录
    if original_size < MIN_FILE_SIZE_MB:
        if log_callback:
            log_callback("  📦 小文件直接移入成功目录（< %d MB，无处理）" % MIN_FILE_SIZE_MB)
        move_path = success_dir / os.path.basename(filepath)
        move_file(filepath, move_path)
        return True, "小文件放行"

    # ≥ 100MB → 套用模式1逻辑
    if log_callback:
        log_callback("  ≥ %d MB，套用模式1逻辑" % MIN_FILE_SIZE_MB)
    return process_mode_1_2(filepath, success_dir, failed_dir, mode=1,
                            log_callback=log_callback,
                            progress_callback=progress_callback,
                            auto_mode=auto_mode,
                            custom_crf=custom_crf,
                            gpu_mode=gpu_mode)
