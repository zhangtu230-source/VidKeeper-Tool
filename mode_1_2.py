import os

from config import (
    BITRATE_TOO_LOW_KBPS, BITRATE_REASONABLE_MULTIPLIER,
    BITRATE_LOWRES_SKIP_MULTIPLIER,
)
from utils import (
    get_video_info, get_file_size_mb, move_file, estimate_reasonable_bitrate,
    is_low_resolution, is_legacy_format, is_legacy_codec, is_modern_codec,
    is_hdr_or_10bit, get_quality_param, get_optimal_preset,
)
from encoder import compress_video


# ============================================================
# 模式1 决策（老片保画质 · 归档首选）
# 5步优先级，命中即停
# ① 码率 < 300kbps → 跳过
# ② ≤360p 且现代编码 → 强制跳过
# ③ 老式编码/老式容器 → CPU x265 CRF22
# ④ 现代编码且码率合理(≤2×均衡值) → 跳过
# ⑤ 现代编码且码率 > 2×均衡值 → CPU x265 CRF22
# ============================================================
def decide_action_mode1(info, filepath=None):
    if not info:
        return ('compress', 'h265', 22, "无法分析视频信息，使用 H.265 保守压缩")

    codec = info['codec']
    bitrate_kbps = info['bitrate'] / 1000
    is_low = is_low_resolution(info['width'], info['height'])
    is_legacy = is_legacy_codec(codec) or (
        is_legacy_format(filepath, info) if filepath else False
    )

    # ① 码率 < 300kbps → 跳过
    if bitrate_kbps < BITRATE_TOO_LOW_KBPS:
        return ('skip', None, None,
                "码率 < %dkbps，再压无意义" % BITRATE_TOO_LOW_KBPS)

    # ② ≤360p 且现代编码 → 强制跳过
    if is_low and is_modern_codec(codec):
        return ('skip', None, None,
                "≤360p 现代编码，强制跳过避免画质损失")

    # ③ 老式编码/老式容器 → CPU x265 CRF22
    if is_legacy:
        crf = get_quality_param('x265', 'high', is_low)  # 22 或 21(≤360p)
        return ('compress', 'h265', crf,
                "老式编码/老式容器，CPU x265 CRF%d" % crf)

    # ④ 现代编码且码率合理(≤2×均衡值) → 跳过
    reasonable = estimate_reasonable_bitrate(info, 'balanced')
    if is_low:
        # ≤360p 现代编码码率 ≤ 1.5× 均衡值 → 强制跳过
        if bitrate_kbps <= reasonable * BITRATE_LOWRES_SKIP_MULTIPLIER:
            return ('skip', None, None,
                    "≤360p 现代编码码率 ≤ 1.5×均衡值，强制跳过")
    if bitrate_kbps <= reasonable * BITRATE_REASONABLE_MULTIPLIER:
        return ('skip', None, None,
                "现代编码码率合理(≤2×均衡值)，跳过")

    # ⑤ 现代编码且码率 > 2×均衡值 → CPU x265 CRF22
    crf = get_quality_param('x265', 'high', is_low)
    return ('compress', 'h265', crf,
            "现代编码码率 > 2×均衡值，CPU x265 CRF%d" % crf)


# ============================================================
# 模式2 决策（老片省空间 · 模式1的激进版）
# 6步优先级，命中即停
# 与模式1的梯度差异：
#   - 触发阈值更低：1.5×均衡值即压缩（模式1为2×）
#   - 压缩参数更激进：CRF25（模式1为CRF22）
# ① 码率 < 300kbps → 跳过
# ② ≤360p 且现代编码 → 强制跳过
# ③ ≤360p 且老式编码 → 仅转封装（Stream Copy，走模式6）
# ④ >360p 且老式编码 → CPU x265 CRF25
# ⑤ >360p 且现代编码 且码率 ≤ 1.5×均衡值 → 跳过
# ⑥ >360p 且现代编码 且码率 > 1.5×均衡值 → CPU x265 CRF25
# ============================================================
def decide_action_mode2(info, filepath=None):
    if not info:
        return ('compress', 'h265', 25, "无法分析视频信息，使用 H.265 保守压缩")

    codec = info['codec']
    bitrate_kbps = info['bitrate'] / 1000
    is_low = is_low_resolution(info['width'], info['height'])
    is_legacy = is_legacy_codec(codec) or (
        is_legacy_format(filepath, info) if filepath else False
    )

    # ① 码率 < 300kbps → 跳过
    if bitrate_kbps < BITRATE_TOO_LOW_KBPS:
        return ('skip', None, None,
                "码率 < %dkbps，再压无意义" % BITRATE_TOO_LOW_KBPS)

    # ② ≤360p 且现代编码 → 强制跳过
    if is_low and is_modern_codec(codec):
        return ('skip', None, None, "≤360p 现代编码，强制跳过")

    # ③ ≤360p 且老式编码 → 仅转封装 Stream Copy（走模式6）
    if is_low and is_legacy:
        return ('remux', None, None,
                "≤360p 老式编码，仅转封装 Stream Copy")

    # ④ >360p 且老式编码 → CPU x265 CRF25
    if is_legacy:
        crf = get_quality_param('x265', 'balanced')  # 25
        return ('compress', 'h265', crf,
                ">360p 老式编码，CPU x265 CRF%d" % crf)

    # ⑤ >360p 且现代编码 且码率 ≤ 1.5×均衡值 → 跳过
    reasonable = estimate_reasonable_bitrate(info, 'balanced')
    if bitrate_kbps <= reasonable * BITRATE_LOWRES_SKIP_MULTIPLIER:
        return ('skip', None, None,
                "现代编码码率合理(≤1.5×均衡值)，跳过")

    # ⑥ >360p 且现代编码 且码率 > 1.5×均衡值 → CPU x265 CRF25
    crf = get_quality_param('x265', 'balanced')  # 25
    return ('compress', 'h265', crf,
            "现代编码码率 > 1.5×均衡值，CPU x265 CRF%d" % crf)


# ============================================================
# 模式8 决策（网络分发 · 1080p 生活视频分享）
# 分辨率限制：≥2K → 跳过
# 基础逻辑同模式1，但参数覆盖：
#   优先 CPU x264 CRF20（High@L4.1）
#   HDR/10bit: CPU x265 CRF22（Main10@L5.0）
# ============================================================
def decide_action_mode8(info, filepath=None):
    if not info:
        return ('compress', 'h264', 20, "无法分析视频信息，使用 H.264 保守压缩")

    pixels = info['width'] * info['height']

    # 分辨率限制：≥2K → 跳过
    if pixels >= 2560 * 1440:
        return ('skip', None, None,
                "≥2K 分辨率，不适用本模式（请使用模式5）")

    codec = info['codec']
    bitrate_kbps = info['bitrate'] / 1000
    is_low = is_low_resolution(info['width'], info['height'])
    is_legacy = is_legacy_codec(codec) or (
        is_legacy_format(filepath, info) if filepath else False
    )
    is_hdr = is_hdr_or_10bit(info)

    # 参数选择：HDR/10bit → x265 CRF22; 否则 x264 CRF20
    if is_hdr:
        target_codec = 'h265'
        crf_normal = get_quality_param('x265', 'high')         # 22
        crf_low = get_quality_param('x265', 'high', True)      # 21
    else:
        target_codec = 'h264'
        crf_normal = get_quality_param('x264', 'high')         # 20
        crf_low = get_quality_param('x264', 'high', True)      # 19

    # ① 码率 < 300kbps → 跳过
    if bitrate_kbps < BITRATE_TOO_LOW_KBPS:
        return ('skip', None, None,
                "码率 < %dkbps，再压无意义" % BITRATE_TOO_LOW_KBPS)

    # ② ≤360p 且现代编码 → 强制跳过
    if is_low and is_modern_codec(codec):
        return ('skip', None, None,
                "≤360p 现代编码，强制跳过")

    # ③ 老式编码/老式容器 → 压缩（参数覆盖）
    if is_legacy:
        crf = crf_low if is_low else crf_normal
        return ('compress', target_codec, crf,
                "老式编码/老式容器，%s CRF%d" % (target_codec.upper(), crf))

    # ④ 现代编码且码率合理 → 跳过
    reasonable = estimate_reasonable_bitrate(info, 'balanced')
    if is_low:
        if bitrate_kbps <= reasonable * BITRATE_LOWRES_SKIP_MULTIPLIER:
            return ('skip', None, None,
                    "≤360p 现代编码码率 ≤ 1.5×均衡值，强制跳过")
    if bitrate_kbps <= reasonable * BITRATE_REASONABLE_MULTIPLIER:
        return ('skip', None, None,
                "现代编码码率合理(≤2×均衡值)，跳过")

    # ⑤ 现代编码且码率 > 2×均衡值 → 压缩
    crf = crf_low if is_low else crf_normal
    return ('compress', target_codec, crf,
            "现代编码码率 > 2×均衡值，%s CRF%d" % (target_codec.upper(), crf))


# ============================================================
# 统一处理函数（模式1/2/8）
# 输出规则：
# - 跳过：源文件移动到成功文件夹（原名）
# - 压缩变大（反向压缩）：丢弃新文件，源文件移动到成功文件夹（原名）
# - 压缩成功：新文件 _processed.mp4 在成功文件夹，源文件保留原位置
# - 压缩失败：删除残余，源文件移动到失败文件夹
# - 模式2 ③ 转封装：调用 mode_6 处理（源文件永不删除）
# ============================================================
def process_mode_1_2(filepath, success_dir, failed_dir, mode=1, log_callback=None, progress_callback=None,
                     auto_mode=True, custom_crf=23, gpu_mode='off'):
    original_size = get_file_size_mb(filepath)

    if log_callback:
        log_callback("  原始大小: %.1f MB" % original_size)

    info = get_video_info(str(filepath))
    if info:
        if log_callback:
            log_callback("  分辨率: %dx%d, 原码率: %.0f kbps" % (info['width'], info['height'], info['bitrate'] / 1000))
            log_callback("  编码格式: %s, 帧率: %.1ffps" % (info['codec'], info['fps']))
            if info.get('is_interlaced'):
                log_callback("  注意: 隔行扫描视频，将启用去隔行")
    else:
        if log_callback:
            log_callback("  无法读取视频信息")

    # 决策
    if mode == 1:
        action, codec_type, param, reason = decide_action_mode1(info, filepath)
    elif mode == 2:
        action, codec_type, param, reason = decide_action_mode2(info, filepath)
    elif mode == 8:
        action, codec_type, param, reason = decide_action_mode8(info, filepath)
    else:
        action, codec_type, param, reason = ('skip', None, None, "未知模式")

    if log_callback:
        log_callback("  决策: %s" % reason)

    # 跳过：移动源文件到成功文件夹（原名）
    if action == 'skip':
        if log_callback:
            log_callback("  结果: 跳过处理，移动到成功文件夹（原名）")
        os.makedirs(str(success_dir), exist_ok=True)
        move_path = success_dir / os.path.basename(filepath)
        move_file(filepath, move_path)
        return True, "跳过"

    # 模式2 ② 转封装：调用 mode_6（源文件永不删除）
    if action == 'remux':
        from mode_6 import process_mode_6
        return process_mode_6(filepath, success_dir, failed_dir,
                              log_callback=log_callback,
                              progress_callback=progress_callback,
                              auto_mode=auto_mode,
                              custom_crf=custom_crf,
                              gpu_mode=gpu_mode)

    # 压缩
    is_interlaced = info.get('is_interlaced', False) if info else False
    is_le360p = is_low_resolution(info['width'], info['height']) if info else False
    target_container = 'mp4'  # 模式1/2/8 输出统一为 MP4

    # 模式8 专用：profile/level + HDR/10bit 保持10bit
    m8_profile = None
    m8_level = None
    m8_force_10bit = False
    if mode == 8 and action == 'compress':
        is_hdr_m8 = is_hdr_or_10bit(info) if info else False
        if is_hdr_m8 and codec_type == 'h265':
            m8_profile = 'main10'
            m8_level = '5.0'
            m8_force_10bit = True
        elif codec_type == 'h264':
            m8_profile = 'high'
            m8_level = '4.1'

    output_path = success_dir / (os.path.basename(filepath).rsplit('.', 1)[0] + '_processed.mp4')
    counter = 1
    while output_path.exists():
        output_path = success_dir / (os.path.basename(filepath).rsplit('.', 1)[0] + '_processed_%d.mp4' % counter)
        counter += 1

    try:
        preset = get_optimal_preset(info, codec_type, param)
        # 模式8: 高码率视频不用fast，提升为medium保质量（利于网络传输）
        if mode == 8 and preset == 'fast':
            preset = 'medium'
        if log_callback:
            log_callback("  编码器预设: %s" % preset)
        compress_kwargs = dict(
            is_interlaced=is_interlaced,
            log_callback=log_callback,
            progress_callback=progress_callback,
            auto_mode=auto_mode, custom_crf=custom_crf,
            preset=preset, gpu_mode=gpu_mode,
            target_container=target_container,
            tier='high', is_le360p=is_le360p,
        )
        if m8_profile:
            compress_kwargs['profile'] = m8_profile
        if m8_level:
            compress_kwargs['level'] = m8_level
        if m8_force_10bit:
            compress_kwargs['force_10bit'] = True
        success = compress_video(filepath, output_path, codec_type, param, **compress_kwargs)

        if success:
            compressed_size = get_file_size_mb(output_path)

            # 压缩后变大 → 丢弃新文件，源文件移动到成功文件夹（原名）
            if compressed_size >= original_size:
                if log_callback:
                    log_callback("  ⚠ 压缩后变大(%.1fMB -> %.1fMB)，丢弃新文件，源文件移动到成功文件夹"
                                 % (original_size, compressed_size))
                try:
                    os.remove(str(output_path))
                except Exception:
                    pass
                os.makedirs(str(success_dir), exist_ok=True)
                move_path = success_dir / os.path.basename(filepath)
                move_file(filepath, move_path)
                return True, "反向压缩，已跳过"

            ratio = (1 - compressed_size / original_size) * 100
            if log_callback:
                out_info = get_video_info(str(output_path))
                out_bitrate = out_info.get('bitrate', 0) // 1000 if out_info else 0
                log_callback("  结果: %.1fMB -> %.1fMB (%.1f%%), 码率: %d kbps"
                             % (original_size, compressed_size, ratio, out_bitrate))
            return True, "压缩完成"
        else:
            raise Exception("输出文件无效或大小为0")

    except Exception as e:
        if log_callback:
            log_callback("  压缩失败: %s" % str(e))

        # GPU 失败回退 CPU
        if gpu_mode != 'off':
            if log_callback:
                log_callback("  🔄 GPU编码失败，尝试CPU编码...")
            try:
                preset = get_optimal_preset(info, codec_type, param)
                # 模式8: 高码率视频不用fast，提升为medium保质量（与主路径一致）
                if mode == 8 and preset == 'fast':
                    preset = 'medium'
                cpu_kwargs = dict(
                    is_interlaced=is_interlaced,
                    log_callback=log_callback,
                    progress_callback=progress_callback,
                    auto_mode=auto_mode, custom_crf=custom_crf,
                    preset=preset, gpu_mode='off', force_cpu=True,
                    target_container=target_container,
                    tier='high', is_le360p=is_le360p,
                )
                if m8_profile:
                    cpu_kwargs['profile'] = m8_profile
                if m8_level:
                    cpu_kwargs['level'] = m8_level
                if m8_force_10bit:
                    cpu_kwargs['force_10bit'] = True
                success = compress_video(filepath, output_path, codec_type, param, **cpu_kwargs)
                if success:
                    compressed_size = get_file_size_mb(output_path)
                    if compressed_size >= original_size:
                        if log_callback:
                            log_callback("  ⚠ CPU重试后文件仍变大，丢弃新文件，源文件移动到成功文件夹")
                        try:
                            os.remove(str(output_path))
                        except Exception:
                            pass
                        os.makedirs(str(success_dir), exist_ok=True)
                        move_path = success_dir / os.path.basename(filepath)
                        move_file(filepath, move_path)
                        return True, "反向压缩，已跳过"
                    ratio = (1 - compressed_size / original_size) * 100
                    if log_callback:
                        out_info = get_video_info(str(output_path))
                        out_bitrate = out_info.get('bitrate', 0) // 1000 if out_info else 0
                        log_callback("  CPU编码成功: %.1fMB -> %.1fMB (%.1f%%), 码率: %d kbps"
                                     % (original_size, compressed_size, ratio, out_bitrate))
                    return True, "CPU压缩完成"
            except Exception:
                if log_callback:
                    log_callback("  CPU编码也失败")

        # 致命错误：原文件移入 Fail 目录
        if log_callback:
            log_callback("  ❌ 所有尝试均失败，原文件移入 Fail 目录")
        if output_path.exists():
            try:
                os.remove(str(output_path))
            except Exception:
                pass
        move_path = failed_dir / os.path.basename(filepath)
        move_file(filepath, move_path)
        return False, "压缩失败"
