import os

from utils import get_file_size_mb, get_video_info, is_low_resolution, move_file
from encoder import remux_video, reencode_video, select_target_container, get_container_extension


# ============================================================
# 模式6（老式格式转现代封装 · 兼容性优先）
# 适用：电视盒子、老播放器兼容
# ≤360p 优化：仅执行第1-2步，不重编码视频流
# 源文件永不删除，仅生成新文件
#
# 目标容器选择规则：
#   源编码 H.264/H.265/AV1 → MP4
#   源编码 MPEG-2/其他老式编码 → MKV
#   第3步 Full Encode 后编码为 H.264 → MP4
#
# 三步递进（成功即停）：
#   步骤1 Remux：容器转换，音视频流直接拷贝
#   步骤2 Video Passthrough：视频流拷贝，音频转码（按全局音频策略）
#   步骤3 Full Encode：CPU x264 CRF20（≤360p CRF19 防块效应）
# ============================================================
def process_mode_6(filepath, success_dir, failed_dir, log_callback=None, progress_callback=None,
                   auto_mode=True, custom_crf=23, gpu_mode='off', force_mp4=False):
    original_size = get_file_size_mb(filepath)

    if log_callback:
        log_callback("  原始大小: %.1f MB" % original_size)

    info = get_video_info(str(filepath))
    if info:
        if log_callback:
            log_callback("  分辨率: %dx%d, 原码率: %.0f kbps"
                         % (info['width'], info['height'], info['bitrate'] / 1000))
            log_callback("  编码格式: %s, 帧率: %.1ffps" % (info['codec'], info['fps']))
            if is_low_resolution(info['width'], info['height']):
                log_callback("  ≤360p：仅执行第1-2步，不重编码视频流")
    else:
        if log_callback:
            log_callback("  无法读取视频信息")

    # 选择目标容器
    target_container = select_target_container(info, force_mp4=force_mp4)
    ext = get_container_extension(target_container)

    if log_callback:
        log_callback("  目标容器: %s（源编码=%s）" % (target_container.upper(), info.get('codec', 'unknown') if info else 'unknown'))

    # ≤360p 标记（用于 Full Encode 使用 CRF19 防块效应）
    is_le360p = False
    if info:
        is_le360p = is_low_resolution(info['width'], info['height'])

    # 输出路径
    base_name = os.path.basename(filepath).rsplit('.', 1)[0]
    output_path = success_dir / (base_name + ext)
    counter = 1
    while output_path.exists():
        output_path = success_dir / (base_name + '_%d%s' % (counter, ext))
        counter += 1

    # 调用三步递进
    result_type, actual_output = remux_video(filepath, output_path, log_callback=log_callback,
                                             progress_callback=progress_callback,
                                             auto_mode=auto_mode, custom_crf=custom_crf,
                                             gpu_mode=gpu_mode,
                                             target_container=target_container,
                                             is_le360p=is_le360p)

    # GPU 失败时回退 CPU（仅当第3步 Full Encode 失败时）
    if not result_type and gpu_mode != 'off':
        if log_callback:
            log_callback("  🔄 GPU编码失败，尝试CPU编码（Full Encode）...")
        # 用 .mp4 扩展名尝试
        actual_output_fallback = output_path
        if str(output_path).lower()[-4:] != '.mp4':
            import pathlib
            actual_output_fallback = pathlib.Path(str(output_path)[:str(output_path).rfind('.')] + '.mp4')
        if actual_output_fallback.exists():
            try:
                os.remove(str(actual_output_fallback))
            except Exception:
                pass
        if output_path.exists():
            try:
                os.remove(str(output_path))
            except Exception:
                pass
        result_type = reencode_video(filepath, actual_output_fallback, log_callback=log_callback,
                                     progress_callback=progress_callback,
                                     auto_mode=auto_mode, custom_crf=custom_crf,
                                     gpu_mode='off', force_cpu=True,
                                     target_container='mp4',
                                     is_le360p=is_le360p,
                                     switch_container_on_reencode=True)
        actual_output = actual_output_fallback if result_type else None

    # 结果处理
    if result_type:
        # 使用实际输出路径（可能已切换为 .mp4）
        final_output = actual_output if actual_output else output_path
        converted_size = get_file_size_mb(final_output)

        # 反向压缩检测：转换后文件变大 → 丢弃新文件，源文件移动到成功文件夹（原名）
        # 注意：步骤1 Remux 通常大小相近（允许±5%波动），仅步骤3 Full Encode 严格检测变大
        if result_type == 'reencode' and converted_size >= original_size:
            if log_callback:
                log_callback("  ⚠ Full Encode 后变大(%.1fMB -> %.1fMB)，丢弃新文件，源文件移动到成功文件夹"
                             % (original_size, converted_size))
            try:
                os.remove(str(final_output))
            except Exception:
                pass
            os.makedirs(str(success_dir), exist_ok=True)
            move_path = success_dir / os.path.basename(filepath)
            move_file(filepath, move_path)
            return True, "反向压缩，已跳过"
        # 步骤1/2 Remux/Passthrough：只在明显变大(>20%)时丢弃（容器转换开销正常±5%）
        if result_type in ('remux', 'passthrough') and converted_size > original_size * 1.20:
            if log_callback:
                log_callback("  ⚠ %s 后明显变大(%.1fMB -> %.1fMB, >20%%)，丢弃新文件，源文件移动到成功文件夹"
                             % (result_type, original_size, converted_size))
            try:
                os.remove(str(final_output))
            except Exception:
                pass
            os.makedirs(str(success_dir), exist_ok=True)
            move_path = success_dir / os.path.basename(filepath)
            move_file(filepath, move_path)
            return True, "反向压缩，已跳过"

        # 描述
        if result_type == 'remux':
            action_type = 'Remux（音视频流直接拷贝）'
            detail = '容器转换无损'
        elif result_type == 'passthrough':
            action_type = 'Video Passthrough（视频copy + 音频转码）'
            detail = '视频无损'
        elif result_type == 'reencode':
            action_type = 'Full Encode（CPU x264 CRF20）'
            detail = '视频重编码'
        else:
            action_type = '转换'
            detail = '已转换'

        if log_callback:
            ratio = (1 - converted_size / original_size) * 100 if original_size > 0 else 0
            out_info = get_video_info(str(final_output))
            out_bitrate = out_info.get('bitrate', 0) // 1000 if out_info else 0
            log_callback("  ✓ 成功: %.1fMB -> %.1fMB (%.1f%%), %s, 码率: %d kbps"
                         % (original_size, converted_size, ratio, detail, out_bitrate))
            log_callback("  ✓ 源文件已保留原位置（模式6/7 转换成功保留源）")
        return True, action_type

    # 失败：删除残余输出文件，源文件移动到 Fail 目录
    if log_callback:
        log_callback("  ❌ 三步递进均失败，删除残余文件，源文件移动到 Fail 目录")
    # 删除所有可能的残余输出文件
    if output_path.exists():
        try:
            os.remove(str(output_path))
        except Exception:
            pass
    if actual_output and os.path.exists(str(actual_output)):
        try:
            os.remove(str(actual_output))
        except Exception:
            pass
    os.makedirs(str(failed_dir), exist_ok=True)
    move_path = failed_dir / os.path.basename(filepath)
    move_file(filepath, move_path)
    return False, "转换失败"
