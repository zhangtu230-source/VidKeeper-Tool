import os
import subprocess
import json
import shutil
import time

from config import (
    LEGACY_CODECS, LEGACY_EXTENSIONS, MODERN_CODECS, MODERN_EXTENSIONS,
    BITRATE_REFERENCE_MBPS, FPS_WHITELIST, FPS_HARD_LIMIT, FPS_WHITELIST_TOLERANCE,
    QUALITY_TABLE,
)


def get_timestamp():
    """获取当前时间戳字符串 [HH:MM:SS]"""
    return time.strftime("[%H:%M:%S]")


# ============================================================
# 视频信息读取（ffprobe）
# ============================================================
def get_video_info(filepath):
    """使用 ffprobe 获取视频流详细信息"""
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_format", "-show_streams",
        str(filepath)
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=30)
        stdout = result.stdout.decode('utf-8', errors='replace')

        try:
            data = json.loads(stdout)
        except json.JSONDecodeError:
            return None

        video_stream = None
        audio_streams = []
        for stream in data.get("streams", []):
            if stream["codec_type"] == "video" and video_stream is None:
                video_stream = stream
            elif stream["codec_type"] == "audio":
                audio_streams.append(stream)

        if not video_stream:
            return None

        width = int(video_stream.get("width", 0))
        height = int(video_stream.get("height", 0))
        bitrate_str = video_stream.get("bit_rate") or data.get("format", {}).get("bit_rate", "0")
        bitrate = int(bitrate_str) if bitrate_str else 0

        duration_str = data.get("format", {}).get("duration", "0")
        duration = float(duration_str) if duration_str else 0

        codec = video_stream.get("codec_name", "unknown").lower()

        field_order = video_stream.get("field_order", "progressive").lower()
        is_interlaced = field_order not in ["progressive", "unknown"]

        r_frame_rate = video_stream.get("r_frame_rate", "0/1")
        try:
            num, den = r_frame_rate.split("/")
            fps = float(num) / float(den) if float(den) > 0 else 30
        except Exception:
            fps = 30

        # 像素格式与位深（用于 GPU 硬性阻断与 HDR 判定）
        pix_fmt = video_stream.get("pix_fmt", "yuv420p").lower()
        bits_per_raw_sample = video_stream.get("bits_per_raw_sample", "8")
        try:
            bit_depth = int(bits_per_raw_sample)
        except (ValueError, TypeError):
            bit_depth = 8

        # 颜色空间相关（HDR 判定）
        color_transfer = video_stream.get("color_transfer", "").lower()
        color_primaries = video_stream.get("color_primaries", "").lower()

        # 音频信息
        audio_bitrate = 0
        audio_channels_total = 0
        if audio_streams:
            for s in audio_streams:
                ab = s.get("bit_rate", "0")
                audio_bitrate += int(ab) if ab else 0
                ch = s.get("channels", 0)
                audio_channels_total += int(ch) if ch else 0
            audio_bitrate = audio_bitrate // 1000

        # 容器格式
        format_name = data.get("format", {}).get("format_name", "").lower()

        return {
            "width": width,
            "height": height,
            "codec": codec,
            "bitrate": bitrate,
            "fps": fps,
            "duration": duration,
            "is_rawvideo": (codec == "rawvideo"),
            "file_size": os.path.getsize(filepath),
            "is_interlaced": is_interlaced,
            "audio_bitrate": audio_bitrate,
            "audio_streams_count": len(audio_streams),
            "audio_channels_total": audio_channels_total,
            "pix_fmt": pix_fmt,
            "bit_depth": bit_depth,
            "color_transfer": color_transfer,
            "color_primaries": color_primaries,
            "format_name": format_name,
            "container_ext": os.path.splitext(filepath)[1].lower(),
        }
    except Exception:
        return None


# ============================================================
# 分辨率分类（按新规则）
# ============================================================
def classify_resolution(width, height):
    """根据像素数分类分辨率"""
    pixels = width * height
    if pixels >= 3840 * 2160:
        return "4K"
    elif pixels >= 2560 * 1440:
        return "2K"
    elif pixels >= 1920 * 1080:
        return "1080p"
    elif pixels >= 1280 * 720:
        return "720p"
    elif pixels >= 640 * 480:
        return "480p"
    elif pixels >= 480 * 360:
        return "360p"
    elif pixels >= 320 * 240:
        return "240p"
    else:
        return "144p"


def is_low_resolution(width, height):
    """≤360p 判定（用于触发特别保护规则）"""
    return classify_resolution(width, height) in ("360p", "240p", "144p")


# ============================================================
# 合理码率参考（仅作判定阈值，非压制目标）
# 返回 bps
# ============================================================
def get_reference_bitrate_kbps(resolution_label, tier="balanced"):
    """获取对应分辨率与档位的参考码率（kbps）"""
    table = BITRATE_REFERENCE_MBPS.get(resolution_label) or BITRATE_REFERENCE_MBPS["360p"]
    mbps = table.get(tier, table["balanced"])
    return int(mbps * 1000)


def estimate_reasonable_bitrate(info, tier="balanced"):
    """估算合理码率（kbps），用于判定阈值"""
    if not info:
        return 600
    resolution_label = classify_resolution(info['width'], info['height'])
    return get_reference_bitrate_kbps(resolution_label, tier)


# ============================================================
# 现代编码 / 老式格式判定
# ============================================================
def is_modern_codec(codec):
    """是否为现代编码（H.264 / H.265 / AV1）"""
    return codec in MODERN_CODECS


def is_legacy_codec(codec):
    """是否为老式 / 特殊编码"""
    return codec in LEGACY_CODECS


def is_legacy_format(filepath, info=None):
    """是否为老式 / 特殊格式（综合考虑扩展名、编码、MOV 容器）

    MOV 容器特别说明：
      MOV + H.264/H.265/AV1 → 视为现代格式
      MOV + ProRes/DV/MPEG-2/其他老式编码 → 视为老式格式
    """
    ext = os.path.splitext(filepath)[1].lower()

    # 老式扩展名直接判定
    if ext in LEGACY_EXTENSIONS:
        return True

    # MOV 容器特别处理
    if ext == ".mov":
        if info is None:
            info = get_video_info(filepath)
        if info:
            codec = info.get("codec", "")
            if is_modern_codec(codec):
                return False
            if is_legacy_codec(codec):
                return True
            # MOV + 未知编码 → 视为老式（保守）
            return True

    return False


def is_modern_format(filepath, info=None):
    """是否为现代封装（MP4/MKV/WebM/AV1/HEVC/H.264），MOV 按编码判定"""
    ext = os.path.splitext(filepath)[1].lower()

    if ext in (".mp4", ".mkv", ".webm"):
        return True

    if ext == ".mov":
        if info is None:
            info = get_video_info(filepath)
        if info:
            codec = info.get("codec", "")
            return is_modern_codec(codec)

    return False


# ============================================================
# 帧率规则
# 合法帧率白名单：23.976 / 24 / 25 / 29.97 / 30 fps
# > 30fps 强制降级为 30fps
# < 30fps 且不在白名单 → 保持原样
# ============================================================
def is_fps_in_whitelist(fps):
    """帧率是否在白名单内"""
    for w in FPS_WHITELIST:
        if abs(fps - w) <= FPS_WHITELIST_TOLERANCE:
            return True
    return False


def get_fps_limit(fps):
    """返回应限制到的帧率，None 表示保持原样

    - 源帧率 ∈ 白名单 → None（保持原样）
    - 源帧率 > 30fps → 30
    - 源帧率 < 30fps 且 ∉ 白名单（如 20fps、15fps）→ None（保持原样）
    """
    if is_fps_in_whitelist(fps):
        return None
    if fps > FPS_HARD_LIMIT:
        return FPS_HARD_LIMIT
    return None


# ============================================================
# HDR / 10bit 判定（用于模式8 选择 x264 vs x265）
# ============================================================
def is_hdr_or_10bit(info):
    """检测 HDR / 10bit 源"""
    if not info:
        return False
    if info.get("bit_depth", 8) > 8:
        return True
    transfer = info.get("color_transfer", "")
    primaries = info.get("color_primaries", "")
    # smpte2084 = HDR10, arib-std-b67 = HLG
    if transfer in ("smpte2084", "arib-std-b67"):
        return True
    if primaries in ("bt2020", "bt2020nc"):
        return True
    return False


# ============================================================
# 编码质量参数查询
# ============================================================
def get_quality_param(encoder_key, tier="high", is_le360p=False):
    """获取编码质量参数（CRF / CQ 值）

    encoder_key: 'x264' / 'x265' / 'nvenc_h264' / 'nvenc_hevc'
    tier: 'lossless' / 'high' / 'balanced' / 'high_compress'
    is_le360p: 是否为 ≤360p（使用防块效应特别值）
    """
    table = QUALITY_TABLE.get(encoder_key, QUALITY_TABLE["x265"])
    if is_le360p:
        return table["le360p"]
    return table.get(tier, table["high"])


# ============================================================
# 时长与文件操作工具函数
# ============================================================
def get_duration_seconds(filepath):
    """获取视频时长（秒）"""
    cmd = [
        'ffprobe', '-v', 'error', '-show_entries', 'format=duration',
        '-of', 'default=noprint_wrappers=1:nokey=1', str(filepath)
    ]
    result = subprocess.run(cmd, capture_output=True)
    output = result.stdout.decode('utf-8', errors='replace').strip()
    try:
        return float(output)
    except Exception:
        return 0


def get_video_duration(filepath):
    """获取视频时长（秒）- 兼容原始脚本6的函数名"""
    return get_duration_seconds(filepath)


def parse_time_to_seconds(time_str):
    """解析时间字符串为秒数"""
    try:
        parts = time_str.split(':')
        if len(parts) != 3:
            return 0

        hours = float(parts[0])
        minutes = float(parts[1])
        seconds = float(parts[2])

        if hours < 0 or minutes < 0 or seconds < 0:
            return 0

        return hours * 3600 + minutes * 60 + seconds
    except (ValueError, IndexError):
        return 0


def get_file_size_mb(filepath):
    """获取文件大小（MB）"""
    return os.path.getsize(filepath) / (1024 * 1024)


def move_file(input_path, output_path):
    """移动文件，自动创建目标目录"""
    output_dir = os.path.dirname(str(output_path))
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    shutil.move(str(input_path), str(output_path))


def get_optimal_preset(video_info, codec_type, crf, target_bitrate=None):
    """根据压缩比计算最优 preset（保留旧版逻辑，用于兼容）"""
    if not video_info:
        return 'medium'

    original_bitrate = video_info.get('bitrate', 0)
    if original_bitrate <= 0:
        return 'medium'

    if target_bitrate is None:
        target_bitrate = estimate_reasonable_bitrate(video_info) * 1000

    if target_bitrate <= 0:
        return 'medium'

    compression_ratio = original_bitrate / target_bitrate

    if compression_ratio > 4:
        return 'fast'
    elif compression_ratio > 1.8:
        return 'medium'
    else:
        return 'slow'
