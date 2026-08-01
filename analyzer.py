import os

from config import (
    LEGACY_CODECS, BITRATE_REASONABLE_MULTIPLIER, BITRATE_LOWRES_SKIP_MULTIPLIER,
    BITRATE_TOO_LOW_KBPS, COMPRESS_4K_BITRATE_THRESHOLD_MBPS,
)
from utils import (
    get_video_info, classify_resolution, estimate_reasonable_bitrate,
    is_low_resolution, is_legacy_format, is_legacy_codec, is_modern_codec,
    is_hdr_or_10bit, is_modern_format,
)


def analyze_video(filepath, log_callback=None):
    """分析单个视频，返回判断结果（v2.5 新规则）"""
    info = get_video_info(filepath)
    if not info:
        return None

    width = info["width"]
    height = info["height"]
    codec = info["codec"]
    bitrate = info["bitrate"] // 1000  # kbps
    fps = info["fps"]
    duration = info["duration"]
    is_interlaced = info["is_interlaced"]
    is_rawvideo = info["is_rawvideo"]
    is_hdr = is_hdr_or_10bit(info)

    resolution_label = classify_resolution(width, height)
    is_low = is_low_resolution(width, height)
    is_legacy_codec_flag = is_legacy_codec(codec)
    is_legacy_fmt = is_legacy_format(filepath, info)
    is_modern_fmt = is_modern_format(filepath, info)

    reasonable_bitrate = estimate_reasonable_bitrate(info, 'balanced')
    threshold_2x = reasonable_bitrate * BITRATE_REASONABLE_MULTIPLIER

    reasons = []
    status = "✅ 最佳状态"

    # 码率过低
    if bitrate > 0 and bitrate < BITRATE_TOO_LOW_KBPS:
        status = "⏩ 跳过（码率过低，再压无意义）"
        reasons.append("码率 %dkbps < %dkbps，再压无意义" % (bitrate, BITRATE_TOO_LOW_KBPS))

    # ≤360p 现代编码保护
    elif is_low and is_modern_codec(codec):
        status = "⏩ 跳过（≤360p 现代编码强制保护）"
        reasons.append("≤360p 现代编码，强制跳过避免画质损失")

    # 老式编码/老式容器
    elif is_legacy_codec_flag or is_legacy_fmt:
        status = "⚠️ 建议压缩（老式编码/老式容器）"
        reasons.append("编码格式 %s 或容器为老式格式，建议转 H.265 节省空间并提升兼容性" % codec)

    # 现代编码 + 码率判定
    elif is_modern_codec(codec):
        if is_low:
            # ≤360p 现代编码：1.5× 阈值
            threshold_low = reasonable_bitrate * BITRATE_LOWRES_SKIP_MULTIPLIER
            if bitrate <= threshold_low:
                status = "✅ 最佳状态（≤360p 现代编码码率合理）"
                reasons.append("≤360p 现代编码码率 %dkbps ≤ 1.5×均衡值(%dkbps)，强制跳过" % (bitrate, int(threshold_low)))
            else:
                status = "⚠️ 可以考虑压缩（≤360p 码率偏高）"
                reasons.append("≤360p 现代编码码率 %dkbps > 1.5×均衡值(%dkbps)" % (bitrate, int(threshold_low)))
        else:
            # >360p 现代编码：2× 阈值
            if bitrate <= threshold_2x:
                status = "✅ 最佳状态（现代编码码率合理）"
                reasons.append("现代编码码率 %dkbps ≤ 2×均衡值(%dkbps)，跳过" % (bitrate, int(threshold_2x)))
            else:
                status = "⚠️ 建议压缩（码率偏高）"
                reasons.append("现代编码码率 %dkbps > 2×均衡值(%dkbps)，建议转 H.265 CRF22" % (bitrate, int(threshold_2x)))

    # 4K 特殊说明
    if resolution_label == "4K":
        threshold_3x = COMPRESS_4K_BITRATE_THRESHOLD_MBPS * 1000
        if bitrate > threshold_3x:
            reasons.append("4K 码率 %dkbps > %dMbps，可使用模式5 + --4k-compress 压缩（保持4K）"
                           % (bitrate, COMPRESS_4K_BITRATE_THRESHOLD_MBPS))
        else:
            reasons.append("4K 分辨率，默认不主动压缩（4K 不降分辨率）")

    # HDR/10bit
    if is_hdr:
        if status == "✅ 最佳状态":
            status = "ℹ️ HDR/10bit 源"
        reasons.append("HDR/10bit 源，模式8 应使用 CPU x265 CRF22（Main10@L5.0）")

    # 隔行扫描
    if is_interlaced:
        if status == "✅ 最佳状态":
            status = "⚠️ 可以考虑压缩"
        reasons.append("隔行扫描视频，转码时将启用去隔行处理")

    # 小文件 + 短时长
    file_size_mb = info["file_size"] / (1024 * 1024)
    if file_size_mb < 15 and duration < 180:
        status = "✅ 最佳状态（小文件，无需处理）"
        reasons = []

    if duration < 5:
        status = "⏩ 跳过（时长过短）"
        reasons = []

    return {
        "file": os.path.basename(filepath),
        "resolution": "%dx%d (%s)" % (width, height, resolution_label),
        "codec": codec,
        "bitrate_kbps": bitrate,
        "fps": round(fps, 2),
        "duration_sec": round(duration, 1),
        "size_mb": round(file_size_mb, 1),
        "status": status,
        "reasons": "; ".join(reasons),
        "estimated_reasonable_bitrate": "%d kbps (2×阈值=%d kbps)" % (reasonable_bitrate, int(threshold_2x)),
        "is_interlaced": is_interlaced,
        "is_rawvideo": is_rawvideo,
        "is_hdr": is_hdr,
        "is_legacy_format": is_legacy_fmt,
        "is_modern_format": is_modern_fmt,
    }


def process_mode_0(filepath, log_callback=None):
    """模式0：视频分析（不解码、不编码、不移动）"""
    if log_callback:
        log_callback("正在分析: %s" % os.path.basename(filepath))

    result = analyze_video(filepath)
    if not result:
        if log_callback:
            log_callback("  ❌ 无法读取视频信息")
        return False, "无法分析"

    if log_callback:
        log_callback("  文件名: %s" % result['file'])
        log_callback("  分辨率: %s" % result['resolution'])
        log_callback("  编码格式: %s" % result['codec'])
        log_callback("  帧率: %sfps" % result['fps'])
        log_callback("  码率: %dkbps" % result['bitrate_kbps'])
        log_callback("  大小: %sMB" % result['size_mb'])
        log_callback("  时长: %s秒" % result['duration_sec'])
        log_callback("  状态: %s" % result['status'])
        if result['is_rawvideo']:
            log_callback("  ⚠️ 原始RGB视频")
        if result['is_interlaced']:
            log_callback("  ⚠️ 隔行扫描视频")
        if result['is_hdr']:
            log_callback("  ⚠️ HDR/10bit 源")
        if result['is_legacy_format']:
            log_callback("  📼 老式格式（需兼容性处理）")
        if result['reasons']:
            log_callback("  原因: %s" % result['reasons'])
        log_callback("  估算合理码率: %s" % result['estimated_reasonable_bitrate'])

    return True, "分析完成"
