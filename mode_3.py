import os

from config import MIN_FILE_SIZE_MB, LEGACY_EXTENSIONS
from utils import (
    get_video_info, get_file_size_mb, move_file, is_legacy_format,
)
from mode_1_2 import process_mode_1_2


# ============================================================
# 模式3（小文件放行 · 扫盘归档）
# 逻辑：
#   < 100MB 且现代格式 → 跳过（直接移入成功目录）
#   < 100MB 且老式格式 → 执行模式6的三步递进逻辑
#   ≥ 100MB → 套用模式1逻辑
# ============================================================
def process_mode_3(filepath, success_dir, failed_dir, log_callback=None, progress_callback=None,
                   auto_mode=True, custom_crf=23, gpu_mode='off'):
    original_size = get_file_size_mb(filepath)

    if log_callback:
        log_callback("  原始大小: %.1f MB" % original_size)

    # < 100MB 路径
    if original_size < MIN_FILE_SIZE_MB:
        # 判定现代/老式格式（需视频信息辅助判定 MOV+老式编码）
        info = get_video_info(str(filepath))
        is_legacy = is_legacy_format(filepath, info) if info else (
            os.path.splitext(filepath)[1].lower() in LEGACY_EXTENSIONS
        )

        if not is_legacy:
            # < 100MB 且现代格式 → 直接移入成功目录
            if log_callback:
                log_callback("  📦 小文件 + 现代格式，直接移入成功目录（< %d MB）" % MIN_FILE_SIZE_MB)
            move_path = success_dir / os.path.basename(filepath)
            move_file(filepath, move_path)
            return True, "小文件放行"
        else:
            # < 100MB 且老式格式 → 执行模式6三步递进
            if log_callback:
                log_callback("  📼 小文件 + 老式格式，执行模式6三步递进逻辑")
            from mode_6 import process_mode_6
            return process_mode_6(filepath, success_dir, failed_dir,
                                  log_callback=log_callback,
                                  progress_callback=progress_callback,
                                  auto_mode=auto_mode,
                                  custom_crf=custom_crf,
                                  gpu_mode=gpu_mode)

    # ≥ 100MB → 套用模式1逻辑
    if log_callback:
        log_callback("  ≥ %d MB，套用模式1逻辑" % MIN_FILE_SIZE_MB)
    return process_mode_1_2(filepath, success_dir, failed_dir, mode=1,
                            log_callback=log_callback,
                            progress_callback=progress_callback,
                            auto_mode=auto_mode,
                            custom_crf=custom_crf,
                            gpu_mode=gpu_mode)
