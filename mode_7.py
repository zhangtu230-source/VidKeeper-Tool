import os

from utils import get_file_size_mb, get_video_info, is_modern_codec, is_legacy_codec, move_file
from encoder import reencode_video
from mode_6 import process_mode_6


# ============================================================
# 模式7（全部转为 MP4）
# 逻辑：
#   已是 MP4 且内部编码为现代编码（H.264/H.265/AV1）→ 移动到成功文件夹（原名）
#   已是 MP4 但内部编码为老式编码（MPEG-2 等）→ 直接执行模式6第3步 Full Encode → MP4（短路优化）
#   其他格式 → 执行模式6的三步处理逻辑（目标容器强制 MP4；
#             若源编码为 MPEG-2 等 MP4 不兼容编码，跳过第1-2步，直接执行第3步 → MP4）
# 输出规则：转换成功→源文件保留原位置；反向压缩→源文件移到成功文件夹；失败→源文件移到失败文件夹
# ============================================================
def process_mode_7(filepath, success_dir, failed_dir, log_callback=None, progress_callback=None,
                   auto_mode=True, custom_crf=23, gpu_mode='off'):
    original_size = get_file_size_mb(filepath)

    if log_callback:
        log_callback("  原始大小: %.1f MB" % original_size)

    file_ext = os.path.splitext(filepath)[1].lower()
    info = get_video_info(str(filepath))

    if info:
        if log_callback:
            log_callback("  分辨率: %dx%d, 原码率: %.0f kbps"
                         % (info['width'], info['height'], info['bitrate'] / 1000))
            log_callback("  编码格式: %s, 帧率: %.1ffps" % (info['codec'], info['fps']))
    else:
        if log_callback:
            log_callback("  无法读取视频信息")

    codec = info.get('codec', '') if info else ''

    # 输出路径
    base_name = os.path.basename(filepath).rsplit('.', 1)[0]
    output_path = success_dir / (base_name + '.mp4')
    counter = 1
    while output_path.exists():
        output_path = success_dir / (base_name + '_%d.mp4' % counter)
        counter += 1

    # 情况1：已是 MP4 且内部编码为现代编码 → 移动到成功文件夹（原名）
    if file_ext == '.mp4' and is_modern_codec(codec):
        if log_callback:
            log_callback("  ✓ 已是 MP4 + 现代编码(%s)，移动到成功文件夹（原名）" % codec)
        os.makedirs(str(success_dir), exist_ok=True)
        move_path = success_dir / os.path.basename(filepath)
        move_file(filepath, move_path)
        return True, "无需转换"

    # 情况2：已是 MP4 但内部编码为老式编码 → 直接 Full Encode（短路优化，避免无效 Remux）
    if file_ext == '.mp4' and is_legacy_codec(codec):
        if log_callback:
            log_callback("  📼 MP4 + 老式编码(%s)，直接 Full Encode → MP4（短路优化）" % codec)
        result_type = reencode_video(filepath, output_path, log_callback=log_callback,
                                     progress_callback=progress_callback,
                                     auto_mode=auto_mode, custom_crf=custom_crf,
                                     gpu_mode=gpu_mode,
                                     target_container='mp4',
                                     is_le360p=False)
        # GPU 失败回退 CPU
        if not result_type and gpu_mode != 'off':
            if log_callback:
                log_callback("  🔄 GPU失败，尝试CPU编码...")
            if output_path.exists():
                try:
                    os.remove(str(output_path))
                except Exception:
                    pass
            result_type = reencode_video(filepath, output_path, log_callback=log_callback,
                                         progress_callback=progress_callback,
                                         auto_mode=auto_mode, custom_crf=custom_crf,
                                         gpu_mode='off', force_cpu=True,
                                         target_container='mp4',
                                         is_le360p=False)

        if result_type:
            converted_size = get_file_size_mb(output_path)
            # 反向压缩检测
            if converted_size >= original_size:
                if log_callback:
                    log_callback("  ⚠ Full Encode 后变大(%.1fMB -> %.1fMB)，丢弃新文件，源文件移动到成功文件夹"
                                 % (original_size, converted_size))
                try:
                    os.remove(str(output_path))
                except Exception:
                    pass
                os.makedirs(str(success_dir), exist_ok=True)
                move_path = success_dir / os.path.basename(filepath)
                move_file(filepath, move_path)
                return True, "反向压缩，已跳过"
            if log_callback:
                ratio = (1 - converted_size / original_size) * 100 if original_size > 0 else 0
                out_info = get_video_info(str(output_path))
                out_bitrate = out_info.get('bitrate', 0) // 1000 if out_info else 0
                log_callback("  ✓ 成功: %.1fMB -> %.1fMB (%.1f%%), Full Encode, 码率: %d kbps"
                             % (original_size, converted_size, ratio, out_bitrate))
                log_callback("  ✓ 源文件已保留原位置（模式6/7 转换成功保留源）")
            return True, "Full Encode → MP4"

        # 失败：删除残余，源文件移动到 Fail 目录
        if log_callback:
            log_callback("  ❌ Full Encode 失败，删除残余文件，源文件移动到 Fail 目录")
        if output_path.exists():
            try:
                os.remove(str(output_path))
            except Exception:
                pass
        os.makedirs(str(failed_dir), exist_ok=True)
        move_path = failed_dir / os.path.basename(filepath)
        move_file(filepath, move_path)
        return False, "转换失败"

    # 情况3：其他格式
    # 子情况3a：源编码为 MPEG-2 / 老式编码 → 直接 Full Encode → MP4（短路跳过第1-2步无效尝试）
    # 子情况3b：其他格式 → 执行模式6三步处理（目标容器强制 MP4）
    mp4_incompatible_codecs = {
        'mpeg2video', 'mpeg1video', 'dvvideo', 'prores', 'prores_ks', 'prores_aw',
        'vc1', 'wmv1', 'wmv2', 'wmv3', 'rv10', 'rv20', 'rv30', 'rv40',
        'theora', 'flv1', 'vp6f', 'vp6a', 'svq1', 'svq3', 'cinepak',
        'msmpeg4v1', 'msmpeg4v2', 'msmpeg4v3', 'indeo2', 'indeo3', 'indeo5',
        'mjpeg', 'ffv1', 'v210', 'utvideo', 'magicyuv', 'ffvhuff',
    }
    if codec.lower() in mp4_incompatible_codecs or is_legacy_codec(codec):
        if log_callback:
            log_callback("  📼 老式编码/MP4不兼容编码(%s)，直接 Full Encode → MP4（短路跳过第1-2步）" % codec)
        result_type = reencode_video(filepath, output_path, log_callback=log_callback,
                                     progress_callback=progress_callback,
                                     auto_mode=auto_mode, custom_crf=custom_crf,
                                     gpu_mode=gpu_mode,
                                     target_container='mp4',
                                     is_le360p=False,
                                     switch_container_on_reencode=True)
        # GPU 失败回退 CPU
        if not result_type and gpu_mode != 'off':
            if log_callback:
                log_callback("  🔄 GPU失败，尝试CPU编码...")
            if output_path.exists():
                try:
                    os.remove(str(output_path))
                except Exception:
                    pass
            result_type = reencode_video(filepath, output_path, log_callback=log_callback,
                                         progress_callback=progress_callback,
                                         auto_mode=auto_mode, custom_crf=custom_crf,
                                         gpu_mode='off', force_cpu=True,
                                         target_container='mp4',
                                         is_le360p=False,
                                         switch_container_on_reencode=True)

        if result_type:
            converted_size = get_file_size_mb(output_path)
            # 反向压缩检测
            if converted_size >= original_size:
                if log_callback:
                    log_callback("  ⚠ Full Encode 后变大(%.1fMB -> %.1fMB)，丢弃新文件，源文件移动到成功文件夹"
                                 % (original_size, converted_size))
                try:
                    os.remove(str(output_path))
                except Exception:
                    pass
                os.makedirs(str(success_dir), exist_ok=True)
                move_path = success_dir / os.path.basename(filepath)
                move_file(filepath, move_path)
                return True, "反向压缩，已跳过"
            if log_callback:
                ratio = (1 - converted_size / original_size) * 100 if original_size > 0 else 0
                out_info = get_video_info(str(output_path))
                out_bitrate = out_info.get('bitrate', 0) // 1000 if out_info else 0
                log_callback("  ✓ 成功: %.1fMB -> %.1fMB (%.1f%%), Full Encode, 码率: %d kbps"
                             % (original_size, converted_size, ratio, out_bitrate))
                log_callback("  ✓ 源文件已保留原位置（模式6/7 转换成功保留源）")
            return True, "Full Encode → MP4（短路）"

        # 失败：删除残余，源文件移动到 Fail 目录
        if log_callback:
            log_callback("  ❌ Full Encode 失败，删除残余文件，源文件移动到 Fail 目录")
        if output_path.exists():
            try:
                os.remove(str(output_path))
            except Exception:
                pass
        os.makedirs(str(failed_dir), exist_ok=True)
        move_path = failed_dir / os.path.basename(filepath)
        move_file(filepath, move_path)
        return False, "转换失败"

    # 子情况3b：其他格式（如 MKV 内已是 H.264/H.265），走模式6三步
    if log_callback:
        log_callback("  → 其他格式，执行模式6三步处理（目标容器强制 MP4）")
    return process_mode_6(filepath, success_dir, failed_dir,
                          log_callback=log_callback,
                          progress_callback=progress_callback,
                          auto_mode=auto_mode,
                          custom_crf=custom_crf,
                          gpu_mode=gpu_mode,
                          force_mp4=True)
