import os

from config import (
    BITRATE_REASONABLE_MULTIPLIER, COMPRESS_4K_BITRATE_THRESHOLD_MBPS,
    COMPRESS_4K_DEFAULT,
)
from utils import (
    get_video_info, get_file_size_mb, move_file, estimate_reasonable_bitrate,
    classify_resolution, is_legacy_format, is_legacy_codec,
)
from encoder import compress_video


# ============================================================
# 模式5 决策（超高清压缩 · 4K / 2K / 1080p）
# 8步优先级，命中即停：
# ① 老式编码/老式容器 → 转交模式6处理
# ② ≤ 720p（含 360p 及以下）→ 跳过
# ③ 1080p 且码率 > 2× 均衡值（> 10 Mbps）→ x265 CRF21 + preset=medium
# ④ 1080p 且码率 ≤ 2× 均衡值 → 跳过
# ⑤ 2K 且码率 > 2× 均衡值（> 20 Mbps）→ x265 CRF21 + preset=medium
# ⑥ 2K 且码率 ≤ 2× 均衡值 → 跳过
# ⑦ 4K 且码率 > 3× 均衡值（> 48 Mbps）且 compress_4k=True → x265 CRF20 + preset=fast（GPU: CQ19 lossless）
# ⑧ 4K（其余情况）→ 跳过
# v2.7: 1080p/2K=CRF21+medium, 4K=CRF20+fast(GPU tier=lossless CQ19)
# ============================================================
def decide_action_mode5(info, filepath=None, compress_4k=COMPRESS_4K_DEFAULT):
    if not info:
        return ('compress', 'h265', 21, "无法分析视频信息，使用 H.265 CRF21 保守压缩")

    codec = info['codec']
    bitrate_kbps = info['bitrate'] / 1000
    resolution_label = classify_resolution(info['width'], info['height'])
    is_legacy = is_legacy_codec(codec) or (
        is_legacy_format(filepath, info) if filepath else False
    )

    # ① 老式编码/老式容器 → 转交模式6处理
    if is_legacy:
        return ('delegate_mode6', None, None,
                "老式编码/老式容器，转交模式6处理（兼容性优先）")

    # ② ≤ 720p（含 360p 及以下）→ 跳过
    if resolution_label in ('720p', '480p', '360p', '240p', '144p'):
        return ('skip', None, None,
                "≤720p 分辨率，跳过（不适用超高清压缩）")

    # 现代编码 + 高分辨率（1080p/2K/4K）
    reasonable = estimate_reasonable_bitrate(info, 'balanced')
    threshold_2x = reasonable * BITRATE_REASONABLE_MULTIPLIER

    if resolution_label == '1080p':
        # ③ 1080p 且码率 > 2× 均衡值 → x265 CRF21 + medium
        if bitrate_kbps > threshold_2x:
            return ('compress', 'h265', 21,
                    "1080p 码率 > 2×均衡值(>10Mbps)，x265 CRF21 + medium")
        # ④ 1080p 且码率 ≤ 2× 均衡值 → 跳过
        return ('skip', None, None,
                "1080p 码率合理(≤2×均衡值)，跳过")

    if resolution_label == '2K':
        # ⑤ 2K 且码率 > 2× 均衡值 → x265 CRF21 + medium
        if bitrate_kbps > threshold_2x:
            return ('compress', 'h265', 21,
                    "2K 码率 > 2×均衡值(>20Mbps)，x265 CRF21 + medium")
        # ⑥ 2K 且码率 ≤ 2× 均衡值 → 跳过
        return ('skip', None, None,
                "2K 码率合理(≤2×均衡值)，跳过")

    if resolution_label == '4K':
        # ⑦ 4K 且码率 > 3× 均衡值（> 48 Mbps）且 compress_4k=True → x265 CRF20 + fast（GPU: CQ19 lossless）
        threshold_3x_kbps = COMPRESS_4K_BITRATE_THRESHOLD_MBPS * 1000
        if compress_4k and bitrate_kbps > threshold_3x_kbps:
            return ('compress', 'h265', 20,
                    "4K 码率 > %dMbps 且 compress_4k=True，x265 CRF20 + fast（GPU: CQ19 lossless）"
                    % COMPRESS_4K_BITRATE_THRESHOLD_MBPS)
        # ⑧ 4K（其余情况）→ 跳过
        if compress_4k:
            return ('skip', None, None,
                    "4K 码率 ≤ %dMbps，跳过" % COMPRESS_4K_BITRATE_THRESHOLD_MBPS)
        return ('skip', None, None,
                "4K 分辨率，compress_4k=False，跳过（默认不压缩4K）")

    # 兜底（理论上不会到达）
    return ('skip', None, None, "未匹配任何条件，跳过")


# ============================================================
# 模式5 处理函数
# 核心原则：4K 不降分辨率，不主动压缩；老式编码走兼容性路径
# ============================================================
def process_mode_5(filepath, success_dir, failed_dir, log_callback=None, progress_callback=None,
                   auto_mode=True, custom_crf=23, gpu_mode='off', compress_4k=COMPRESS_4K_DEFAULT):
    original_size = get_file_size_mb(filepath)

    if log_callback:
        log_callback("  原始大小: %.1f MB" % original_size)
        if compress_4k:
            log_callback("  ⚙ compress_4k=True（4K码率>%dMbps 将压缩）"
                         % COMPRESS_4K_BITRATE_THRESHOLD_MBPS)

    info = get_video_info(str(filepath))
    if info:
        if log_callback:
            log_callback("  分辨率: %dx%d, 原码率: %.0f kbps"
                         % (info['width'], info['height'], info['bitrate'] / 1000))
            log_callback("  编码格式: %s, 帧率: %.1ffps" % (info['codec'], info['fps']))
            if info.get('is_interlaced'):
                log_callback("  注意: 隔行扫描视频，将启用去隔行")
    else:
        if log_callback:
            log_callback("  无法读取视频信息")

    action, codec_type, param, reason = decide_action_mode5(info, filepath, compress_4k)
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

    # ① 老式编码 → 转交模式6处理（源文件永不删除）
    if action == 'delegate_mode6':
        if log_callback:
            log_callback("  → 转交模式6处理")
        from mode_6 import process_mode_6
        return process_mode_6(filepath, success_dir, failed_dir,
                              log_callback=log_callback,
                              progress_callback=progress_callback,
                              auto_mode=auto_mode,
                              custom_crf=custom_crf,
                              gpu_mode=gpu_mode)

    # 压缩
    is_interlaced = info.get('is_interlaced', False) if info else False
    is_le360p = False  # 模式5 仅处理 ≥1080p，不会触发 ≤360p 特别值
    target_container = 'mp4'

    output_path = success_dir / (os.path.basename(filepath).rsplit('.', 1)[0] + '_processed.mp4')
    counter = 1
    while output_path.exists():
        output_path = success_dir / (os.path.basename(filepath).rsplit('.', 1)[0] + '_processed_%d.mp4' % counter)
        counter += 1

    try:
        # v2.7: 按分辨率固定 preset 和 tier
        # 1080p/2K: CRF21 + medium + tier=high (GPU CQ21)
        # 4K:      CRF20 + fast  + tier=lossless (GPU CQ19)
        resolution_label = classify_resolution(info['width'], info['height']) if info else '1080p'
        if resolution_label == '4K':
            preset = 'fast'
            tier = 'lossless'
        else:
            preset = 'medium'
            tier = 'high'
        if log_callback:
            log_callback("  编码器预设: %s (tier: %s)" % (preset, tier))
        success = compress_video(filepath, output_path, codec_type, param,
                                 is_interlaced=is_interlaced,
                                 log_callback=log_callback,
                                 progress_callback=progress_callback,
                                 auto_mode=auto_mode, custom_crf=custom_crf,
                                 preset=preset, gpu_mode=gpu_mode,
                                 target_container=target_container,
                                 tier=tier, is_le360p=is_le360p)

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
                # v2.7: GPU回退CPU时保持同分辨率档位的 preset/tier
                resolution_label = classify_resolution(info['width'], info['height']) if info else '1080p'
                if resolution_label == '4K':
                    preset = 'fast'
                    tier = 'lossless'
                else:
                    preset = 'medium'
                    tier = 'high'
                success = compress_video(filepath, output_path, codec_type, param,
                                         is_interlaced=is_interlaced,
                                         log_callback=log_callback,
                                         progress_callback=progress_callback,
                                         auto_mode=auto_mode, custom_crf=custom_crf,
                                         preset=preset, gpu_mode='off', force_cpu=True,
                                         target_container=target_container,
                                         tier=tier, is_le360p=is_le360p)
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
