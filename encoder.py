import os
import sys
import subprocess
import re
import threading

if sys.platform == 'win32':
    CREATE_NO_WINDOW = subprocess.CREATE_NO_WINDOW
else:
    CREATE_NO_WINDOW = 0

import config as _config_module
from config import (
    LEGACY_CODECS, LEGACY_EXTENSIONS, FFMPEG_BAR_WIDTH, QUALITY_TABLE,
    FPS_HARD_LIMIT,
)
from utils import (
    get_video_info, get_duration_seconds, get_video_duration,
    parse_time_to_seconds, get_optimal_preset, get_fps_limit,
    is_low_resolution, is_legacy_format, get_quality_param,
)


_GPU_ENCODERS_DETECTED = None
_GPU_ENCODERS_LOCK = threading.Lock()


def detect_gpu_encoders():
    global _GPU_ENCODERS_DETECTED
    with _GPU_ENCODERS_LOCK:
        if _GPU_ENCODERS_DETECTED is not None:
            return _GPU_ENCODERS_DETECTED

        encoders = {}
        try:
            result = subprocess.run(['ffmpeg', '-encoders'], capture_output=True, text=True, timeout=10)
            stdout = result.stdout
            encoders['h264_nvenc'] = 'h264_nvenc' in stdout
            encoders['hevc_nvenc'] = 'hevc_nvenc' in stdout
            encoders['av1_nvenc'] = 'av1_nvenc' in stdout
            encoders['h264_amf'] = 'h264_amf' in stdout
            encoders['hevc_amf'] = 'hevc_amf' in stdout
            encoders['h264_qsv'] = 'h264_qsv' in stdout
            encoders['hevc_qsv'] = 'hevc_qsv' in stdout
        except Exception:
            encoders = {'h264_nvenc': False, 'hevc_nvenc': False, 'av1_nvenc': False,
                        'h264_amf': False, 'hevc_amf': False, 'h264_qsv': False, 'hevc_qsv': False}

        _GPU_ENCODERS_DETECTED = encoders
        return encoders


def has_gpu_encoder(codec_type='h264'):
    encoders = detect_gpu_encoders()
    if codec_type == 'h265':
        return encoders.get('hevc_nvenc') or encoders.get('hevc_amf') or encoders.get('hevc_qsv')
    return encoders.get('h264_nvenc') or encoders.get('h264_amf') or encoders.get('h264_qsv')


def get_gpu_encoder(codec_type):
    encoders = detect_gpu_encoders()
    if codec_type == 'h265':
        if encoders.get('hevc_nvenc'):
            return 'hevc_nvenc'
        elif encoders.get('hevc_amf'):
            return 'hevc_amf'
        elif encoders.get('hevc_qsv'):
            return 'hevc_qsv'
        return None
    else:
        if encoders.get('h264_nvenc'):
            return 'h264_nvenc'
        elif encoders.get('h264_amf'):
            return 'h264_amf'
        elif encoders.get('h264_qsv'):
            return 'h264_qsv'
        return None


# ============================================================
# GPU 质量参数转换（v2.5 新规则）
# 不再使用 "CPU CRF + 4"，而是按 QUALITY_TABLE 独立标定
# ============================================================
def get_cq_value(codec_type, tier='high', is_le360p=False):
    """获取 GPU NVENC 的 CQ 值

    codec_type: 'h264' / 'h265'
    tier: 'lossless' / 'high' / 'balanced' / 'high_compress'
    is_le360p: 是否为 ≤360p（使用防块效应特别值）
    """
    if codec_type == 'h265':
        return get_quality_param('nvenc_hevc', tier, is_le360p)
    return get_quality_param('nvenc_h264', tier, is_le360p)


def get_crf_value(codec_type, tier='high', is_le360p=False):
    """获取 CPU 的 CRF 值"""
    if codec_type == 'h265':
        return get_quality_param('x265', tier, is_le360p)
    return get_quality_param('x264', tier, is_le360p)


def gpu_crf_convert(cpu_crf, codec_type):
    """旧版兼容接口：根据 CPU CRF 推算近似的 GPU CQ（仅用于历史调用）

    v2.5 起推荐直接使用 get_cq_value
    """
    if codec_type == 'h264':
        return min(max(cpu_crf - 1, 17), 24)
    return min(max(cpu_crf - 1, 19), 26)


def gpu_preset_convert(cpu_preset):
    preset_map = {
        'ultrafast': 'p1',
        'superfast': 'p2',
        'veryfast':  'p2',
        'faster':    'p3',
        'fast':      'p4',
        'medium':    'p4',
        'slow':      'p5',
        'slower':    'p6',
        'veryslow':  'p7',
    }
    return preset_map.get(cpu_preset, 'p4')


# ============================================================
# GPU 智能判断（v2.5 新规则 · auto 模式）
# 硬性阻断：显存预估不足 / 编码器不支持 / 源为 RGB / 非 yuv420p
# 鼓励启用：≥2K / 文件>4GB 且老式特殊格式
# 默认回退：CPU
# ============================================================
def should_use_gpu(video_info):
    if not video_info:
        return False

    if not has_gpu_encoder('h264') and not has_gpu_encoder('h265'):
        return False

    width = video_info.get('width', 0)
    height = video_info.get('height', 0)
    codec = video_info.get('codec', '')
    pix_fmt = video_info.get('pix_fmt', 'yuv420p').lower()
    file_size = video_info.get('file_size', 0)
    bitrate = video_info.get('bitrate', 0)
    ext = video_info.get('container_ext', '')

    pixels = width * height

    # 硬性阻断 1：4K / 大码率场景（显存预估不足）
    if pixels >= 3840 * 2160:
        return False
    # 显存估算：码率 > 80 Mbps 且分辨率 ≥ 1080p 视为显存吃紧
    if pixels >= 1920 * 1080 and bitrate > 80_000_000:
        return False

    # 硬性阻断 2：源为 RGB / 非 yuv420p 像素格式
    if codec == 'rawvideo':
        return False
    if pix_fmt and not pix_fmt.startswith('yuv420p'):
        return False

    # 鼓励启用 1：分辨率 ≥ 2K
    if pixels >= 2560 * 1440:
        return True

    # 鼓励启用 2：文件 > 4GB 且老式/特殊格式
    if file_size > 4 * 1024 * 1024 * 1024 and ext in LEGACY_EXTENSIONS:
        return True

    # 默认回退
    return False


# ============================================================
# 全局音频策略（v2.5 §一.6）
# 无音频流：不添加音频轨道
# 单音轨 / 立体声：AAC 128k（≤360p 用 64k）
# 多声道(5.1/7.1) · 目标容器 MP4：AAC 256k
# 多声道(5.1/7.1) · 目标容器 MKV / WebM：Opus 192k
# 音轨选择：保留所有音轨，不合并、不删除
# ≤360p 优化：强制音频 ≤ 64k AAC
# ============================================================
def build_audio_args(video_info, target_container='mp4'):
    """构造 ffmpeg 音频参数

    返回 (audio_args_list, audio_desc)
    """
    if not video_info or video_info.get('audio_streams_count', 0) == 0:
        return ['-an'], '无音频流'

    is_low = is_low_resolution(video_info['width'], video_info['height'])
    channels = video_info.get('audio_channels_total', 2)

    # ≤360p 强制音频 ≤ 64k AAC
    if is_low:
        return ['-c:a', 'aac', '-b:a', '64k'], 'AAC 64k (≤360p)'

    # 多声道（5.1=6, 7.1=8）
    if channels >= 6:
        target_container = target_container.lower()
        if target_container in ('mkv', 'webm'):
            return ['-c:a', 'libopus', '-b:a', '192k'], 'Opus 192k (多声道)'
        return ['-c:a', 'aac', '-b:a', '256k'], 'AAC 256k (多声道, MP4)'

    # 单音轨 / 立体声
    return ['-c:a', 'aac', '-b:a', '128k'], 'AAC 128k'


# ============================================================
# 压缩视频核心函数（用于模式1/2/3/4/5/8）
# ============================================================
def compress_video(input_path, output_path, codec_type, crf, is_interlaced=False,
                   audio_bitrate=0, audio_bitrate_target=96,
                   log_callback=None, progress_callback=None,
                   auto_mode=True, custom_crf=23,
                   preset='medium', gpu_mode='off', force_cpu=False, fps_limit=None,
                   target_container='mp4', tier='high', is_le360p=False,
                   keep_source_fps=None, profile=None, level=None,
                   force_10bit=False):
    """压缩视频的核心函数（v2.5）

    新规则要点：
    - 移除 need_downscale（4K 不降分辨率，由调用方决定跳过）
    - 帧率全局限制 30fps（除非 keep_source_fps）
    - 音频按全局音频策略处理（build_audio_args）
    - GPU auto 智能判断按新规则
    - profile/level：模式8 需显式指定（x264 High@L4.1, x265 Main10@L5.0）
    - force_10bit=True 时输出像素格式按源（Main10 保持 10bit），否则 yuv420p
    """
    total_duration = get_duration_seconds(input_path)
    if total_duration <= 0:
        total_duration = 99999

    timeout_seconds = min(max(int(total_duration * 4) + 60, 60), 7200)
    if total_duration <= 0:
        timeout_seconds = 7200

    # 自定义 CRF 模式覆盖
    if not auto_mode:
        crf = custom_crf

    video_info = get_video_info(str(input_path))
    use_gpu = False

    if force_cpu:
        if log_callback:
            log_callback("  🔄 强制使用CPU编码")
    elif gpu_mode == 'on':
        # GPU 强制开启：失败即报错，仅大文件加速用
        use_gpu = has_gpu_encoder(codec_type)
        if not use_gpu:
            use_gpu = has_gpu_encoder('h264') or has_gpu_encoder('h265')
            if use_gpu:
                if log_callback:
                    log_callback("  ⚠ 指定编码器(%s)的GPU版本不可用，但检测到其他GPU编码器，仍使用GPU" % codec_type.upper())
            else:
                if log_callback:
                    log_callback("  ⚠ [GPU回退] 未检测到任何可用的GPU编码器，回退到CPU编码")
    elif gpu_mode == 'auto':
        # v2.5 新规则智能判断
        use_gpu = should_use_gpu(video_info)
        if use_gpu and video_info:
            width = video_info.get('width', 0)
            height = video_info.get('height', 0)
            pix_fmt = video_info.get('pix_fmt', 'yuv420p')
            if log_callback:
                log_callback("  ⚡ GPU auto 启用 (分辨率=%dx%d, pix_fmt=%s)" % (width, height, pix_fmt))

    if use_gpu:
        vcodec = get_gpu_encoder(codec_type)
        if vcodec is None:
            if log_callback:
                log_callback("  ⚠ [GPU回退] %s的GPU编码器不可用，回退到CPU编码" % codec_type.upper())
            use_gpu = False
        else:
            # v2.5：直接用 QUALITY_TABLE 的 CQ 值，不再用 CRF+4
            crf_value = get_cq_value(codec_type, tier, is_le360p)
            gpu_preset = gpu_preset_convert(preset)
    else:
        if codec_type == 'h264':
            vcodec = 'libx264'
            crf_value = min(max(crf, 16), 30)
        else:
            vcodec = 'libx265'
            crf_value = min(max(crf, 18), 34)
        gpu_preset = preset

    # 命令构造
    cmd = [
        'ffmpeg', '-i', str(input_path),
        '-map', '0:v:0', '-map', '0:a?',  # 保留第一条视频和所有音频
        '-c:v', vcodec,
        '-preset', gpu_preset,
    ]

    # profile/level（模式8 显式指定）
    if profile:
        cmd += ['-profile:v', profile]
    if level:
        cmd += ['-level', str(level)]

    # 像素格式：force_10bit=True 时按源保持（Main10 不强制降 8bit），否则 yuv420p
    if force_10bit:
        src_pix_fmt = video_info.get('pix_fmt', '') if video_info else ''
        if src_pix_fmt and (src_pix_fmt.startswith('yuv420p10') or src_pix_fmt.startswith('yuv422p10') or src_pix_fmt.startswith('yuv444p10')):
            cmd += ['-pix_fmt', src_pix_fmt]
        elif src_pix_fmt and ('p10' in src_pix_fmt or '10le' in src_pix_fmt or '10be' in src_pix_fmt):
            cmd += ['-pix_fmt', 'yuv420p10le']
        else:
            cmd += ['-pix_fmt', 'yuv420p']
    else:
        cmd += ['-pix_fmt', 'yuv420p']

    if use_gpu:
        cmd += ['-cq', str(crf_value), '-rc', 'vbr_hq']
    else:
        cmd += ['-crf', str(crf_value)]

    # 视频滤镜
    vf_filters = []

    # 帧率限制：由 ENABLE_FPS_LIMIT 全局开关控制（默认关闭，保持源帧率）
    if keep_source_fps is None:
        keep_source_fps = not _config_module.ENABLE_FPS_LIMIT
    if not keep_source_fps and video_info:
        source_fps = video_info.get('fps', 0)
        limit = fps_limit if fps_limit is not None else get_fps_limit(source_fps)
        if limit is not None and source_fps > limit:
            vf_filters.append(f'fps={limit}')
            if log_callback:
                log_callback("  帧率 %.2ffps > %dfps，限制为 %dfps" % (source_fps, limit, limit))

    # 奇数分辨率向下取整
    if video_info:
        width = video_info.get('width', 0)
        height = video_info.get('height', 0)
        if width % 2 != 0 or height % 2 != 0:
            new_width = width if width % 2 == 0 else width - 1
            new_height = height if height % 2 == 0 else height - 1
            if new_width > 0 and new_height > 0:
                vf_filters.append(f'scale={new_width}:{new_height}')
                if log_callback:
                    log_callback("  分辨率(%dx%d)不是偶数，调整为(%dx%d)以兼容yuv420p" % (width, height, new_width, new_height))

    # 去隔行
    if is_interlaced:
        vf_filters.append('yadif')
        if log_callback:
            log_callback("  检测到隔行扫描，启用去隔行处理...")

    if vf_filters:
        cmd += ['-vf', ','.join(vf_filters)]

    if codec_type == 'h265':
        cmd += ['-tag:v', 'hvc1']

    # 音频参数（v2.5 全局音频策略）
    audio_args, audio_desc = build_audio_args(video_info, target_container)
    cmd += audio_args

    # 输出容器标志
    if target_container.lower() == 'mp4':
        cmd += ['-movflags', '+faststart']

    cmd += [
        '-progress', 'pipe:1',
        '-stats',
        '-y',
        str(output_path)
    ]

    deint_text = ", 去隔行" if is_interlaced else ""
    gpu_text = "[GPU]" if use_gpu else ""
    profile_text = ""
    if profile:
        profile_text = ", profile=%s" % profile
    if level:
        profile_text += "@%s" % level
    if log_callback:
        if use_gpu:
            log_callback("  开始压缩 %s (编码器=%s, CQ=%d, preset=%s%s, 音频=%s%s)..." % (gpu_text, vcodec, crf_value, gpu_preset, profile_text, audio_desc, deint_text))
        else:
            log_callback("  开始压缩 %s (编码器=%s, CRF=%d, preset=%s%s, 音频=%s%s)..." % (gpu_text, vcodec, crf_value, gpu_preset, profile_text, audio_desc, deint_text))

    process = None
    timer = None
    try:
        os.makedirs(str(output_path.parent), exist_ok=True)
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   universal_newlines=False, bufsize=0,
                                   creationflags=CREATE_NO_WINDOW)

        timer = threading.Timer(timeout_seconds, process.kill)
        timer.start()

        ffmpeg_output = []

        for raw_line in iter(process.stdout.readline, b''):
            line = raw_line.decode('utf-8', errors='replace').strip()
            ffmpeg_output.append(line)
            if 'time=' in line:
                time_match = re.search(r'time=(\d+:\d+:\d+\.\d+)', line)
                if time_match:
                    current = parse_time_to_seconds(time_match.group(1))
                    percent = min(current / total_duration * 100, 100)
                    filled = int(FFMPEG_BAR_WIDTH * percent / 100)
                    if progress_callback:
                        progress_callback("%5.1f%%" % percent)

        return_code = process.wait()

        if not output_path.exists() or os.path.getsize(str(output_path)) <= 1024:
            if log_callback:
                log_callback("  ❌ 编码失败，返回码: %d" % return_code)
                if return_code != 0:
                    last_errors = [l for l in ffmpeg_output[-10:] if 'error' in l.lower() or 'failed' in l.lower()]
                    for err in last_errors:
                        log_callback("    %s" % err)
                if use_gpu:
                    log_callback("  ⚠ GPU 编码失败，将由调用方决定是否回退 CPU")

        return output_path.exists() and os.path.getsize(str(output_path)) > 1024
    finally:
        if timer:
            timer.cancel()
        if process:
            try:
                process.stdout.close()
            except Exception:
                pass
        if progress_callback:
            progress_callback("完成")


# ============================================================
# 模式6/7 三步递进：Remux / Video Passthrough / Full Encode
# ============================================================
def select_target_container(video_info, force_mp4=False):
    """选择目标容器

    规则：
      源视频编码为 H.264 / H.265 / AV1 → 目标容器 MP4
      源视频编码为 MPEG-2 / 其他老式编码 → 目标容器 MKV（兼容性更好）
      第3步 Full Encode 后编码为 H.264 → 目标容器 MP4
      force_mp4=True 时强制 MP4
    """
    if force_mp4:
        return 'mp4'
    if not video_info:
        return 'mp4'
    codec = video_info.get('codec', '')
    if codec in ('h264', 'hevc', 'h265', 'av1'):
        return 'mp4'
    return 'mkv'


def get_container_extension(container):
    return {'mp4': '.mp4', 'mkv': '.mkv', 'webm': '.webm'}.get(container, '.mp4')


def remux_video(input_path, output_path, log_callback=None, progress_callback=None,
                auto_mode=True, custom_crf=23, gpu_mode='off', force_cpu=False,
                target_container=None, is_le360p=False):
    """模式6/7 三步递进封装

    步骤1 Remux：容器转换，音视频流直接拷贝（Stream Copy）
    步骤2 Video Passthrough：视频流拷贝，音频按全局策略转码
    步骤3 Full Encode：交由 reencode_video 处理（x264 CRF20）

    ≤360p 优化：仅执行第1-2步，不重编码视频流
    """
    total_duration = get_video_duration(str(input_path))
    duration_known = (total_duration > 0)

    timeout_seconds = min(max(int(total_duration * 4) + 60, 60), 7200)
    if total_duration <= 0:
        timeout_seconds = 7200

    video_info = get_video_info(str(input_path))

    # 选择目标容器
    if target_container is None:
        target_container = select_target_container(video_info)

    # ≤360p 优化：仅执行第1-2步
    skip_full_encode = is_le360p or (video_info and is_low_resolution(video_info['width'], video_info['height']))

    def _run_ffmpeg(cmd, step_desc):
        nonlocal process, timer
        if log_callback:
            log_callback("  %s..." % step_desc)
        try:
            os.makedirs(str(output_path.parent), exist_ok=True)
            process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       universal_newlines=False, bufsize=0,
                                       creationflags=CREATE_NO_WINDOW)
            timer = threading.Timer(timeout_seconds, process.kill)
            timer.start()

            for raw_line in iter(process.stdout.readline, b''):
                line = raw_line.decode('utf-8', errors='replace').strip()
                if 'time=' in line:
                    time_match = re.search(r'time=(\d+:\d+:\d+\.\d+)', line)
                    if time_match:
                        current = parse_time_to_seconds(time_match.group(1))
                        if duration_known:
                            percent = min(current / total_duration * 100, 100)
                            if progress_callback:
                                progress_callback("%5.1f%%" % percent)
                        else:
                            if progress_callback:
                                progress_callback("已处理 %.0f秒" % current)

            return_code = process.wait()
            return return_code
        finally:
            if timer:
                timer.cancel()
            if process:
                try:
                    process.stdout.close()
                except Exception:
                    pass

    process = None
    timer = None

    # 步骤1 Remux
    ext = get_container_extension(target_container)
    # 若输出路径扩展名与目标容器不一致，调用方应已设置好
    cmd1 = [
        'ffmpeg', '-i', str(input_path),
        '-map', '0', '-c', 'copy',
    ]
    if target_container == 'mp4':
        cmd1 += ['-movflags', '+faststart']
    cmd1 += ['-progress', 'pipe:1', '-stats', '-y', str(output_path)]

    rc = _run_ffmpeg(cmd1, "步骤1 Remux（音视频流直接拷贝）")
    if rc == 0 and output_path.exists() and os.path.getsize(str(output_path)) > 1024:
        if progress_callback:
            progress_callback("完成")
        return 'remux', output_path

    # 清理失败输出
    if output_path.exists():
        try:
            os.remove(str(output_path))
        except Exception:
            pass

    # 步骤2 Video Passthrough：视频copy + 音频转码
    audio_args, audio_desc = build_audio_args(video_info, target_container)
    cmd2 = [
        'ffmpeg', '-i', str(input_path),
        '-map', '0:v:0', '-map', '0:a?',
        '-c:v', 'copy',
    ] + audio_args
    if target_container == 'mp4':
        cmd2 += ['-movflags', '+faststart']
    cmd2 += ['-progress', 'pipe:1', '-stats', '-y', str(output_path)]

    rc = _run_ffmpeg(cmd2, "步骤2 Video Passthrough（视频copy, 音频%s）" % audio_desc)
    if rc == 0 and output_path.exists() and os.path.getsize(str(output_path)) > 1024:
        if progress_callback:
            progress_callback("完成")
        return 'passthrough', output_path

    if output_path.exists():
        try:
            os.remove(str(output_path))
        except Exception:
            pass

    # ≤360p 不执行 Full Encode（除非显式允许）
    if skip_full_encode:
        if log_callback:
            log_callback("  ⚠ ≤360p 第1-2步均失败，移入 Fail 目录")
        return False, None

    # 步骤3 Full Encode（x264 CRF20，切换容器为 MP4）
    if log_callback:
        log_callback("  步骤3 Full Encode（CPU x264 CRF20，切换容器为 MP4）...")
    # 若原 output_path 扩展名不是 .mp4，改为 .mp4
    reencode_output = output_path
    if str(output_path).lower()[-4:] != '.mp4':
        import pathlib
        reencode_output = pathlib.Path(str(output_path)[:str(output_path).rfind('.')] + '.mp4')
        counter = 1
        while reencode_output.exists():
            reencode_output = pathlib.Path(str(output_path)[:str(output_path).rfind('.')] + '_%d.mp4' % counter)
            counter += 1
    result = reencode_video(input_path, reencode_output, log_callback, progress_callback,
                            auto_mode=auto_mode, custom_crf=custom_crf,
                            gpu_mode=gpu_mode, force_cpu=force_cpu,
                            target_container='mp4', is_le360p=is_le360p,
                            switch_container_on_reencode=True)
    if not result and output_path.exists():
        try:
            os.remove(str(output_path))
        except Exception:
            pass
    if result:
        return result, reencode_output
    return False, None


def reencode_video(input_path, output_path, log_callback=None, progress_callback=None,
                   auto_mode=True, custom_crf=23, preset='medium', gpu_mode='off', force_cpu=False,
                   target_container='mp4', is_le360p=False,
                   switch_container_on_reencode=False, profile=None, level=None,
                   force_10bit=False, keep_source_fps=None):
    """Full Encode（模式6 第3步 / 模式7 短路）

    v2.5：CPU x264 CRF20（≤360p 用 CRF19 防块效应）
    音频按全局音频策略
    switch_container_on_reencode=True 时：编码已为 H.264 → 目标容器强制 MP4
    """
    total_duration = get_video_duration(str(input_path))
    duration_known = (total_duration > 0)

    timeout_seconds = min(max(int(total_duration * 4) + 60, 60), 7200)
    if total_duration <= 0:
        timeout_seconds = 7200

    # CRF 选择
    if is_le360p:
        raw_crf = get_quality_param('x264', is_le360p=True)  # 19
    elif not auto_mode:
        raw_crf = custom_crf
    else:
        raw_crf = get_quality_param('x264', tier='high')  # 20

    video_info = get_video_info(str(input_path))
    use_gpu = False

    # 第3步 Full Encode 后编码为 H.264 → 目标容器 MP4
    if switch_container_on_reencode:
        target_container = 'mp4'

    if force_cpu:
        if log_callback:
            log_callback("  🔄 强制使用CPU编码")
    elif gpu_mode == 'on':
        use_gpu = has_gpu_encoder('h264')
        if not use_gpu:
            use_gpu = has_gpu_encoder('h265')
    elif gpu_mode == 'auto':
        use_gpu = should_use_gpu(video_info)

    if use_gpu:
        vcodec = get_gpu_encoder('h264')
        if vcodec is None:
            use_gpu = False
        else:
            crf_value = get_cq_value('h264', 'high', is_le360p)
            gpu_preset = gpu_preset_convert(preset)
    else:
        vcodec = 'libx264'
        crf_value = min(max(raw_crf, 16), 30)
        gpu_preset = preset

    cmd = [
        'ffmpeg', '-i', str(input_path),
        '-map', '0:v:0', '-map', '0:a?',
        '-c:v', vcodec,
    ]

    if use_gpu:
        cmd += ['-preset', gpu_preset, '-cq', str(crf_value), '-rc', 'vbr_hq']
    else:
        cmd += ['-preset', gpu_preset, '-crf', str(crf_value)]

    # profile/level（模式8 短路 Full Encode 或显式指定）
    if profile:
        cmd += ['-profile:v', profile]
    if level:
        cmd += ['-level', str(level)]

    # 像素格式：force_10bit=True 时按源保持 10bit，否则 yuv420p
    if force_10bit:
        src_pix_fmt = video_info.get('pix_fmt', '') if video_info else ''
        if src_pix_fmt and (src_pix_fmt.startswith('yuv420p10') or src_pix_fmt.startswith('yuv422p10') or src_pix_fmt.startswith('yuv444p10')):
            cmd += ['-pix_fmt', src_pix_fmt]
        elif src_pix_fmt and ('p10' in src_pix_fmt or '10le' in src_pix_fmt or '10be' in src_pix_fmt):
            cmd += ['-pix_fmt', 'yuv420p10le']
        else:
            cmd += ['-pix_fmt', 'yuv420p']
    else:
        cmd += ['-pix_fmt', 'yuv420p']

    # 滤镜：奇数分辨率 + 去隔行 + 帧率限制
    vf_filters = []
    if video_info:
        width = video_info.get('width', 0)
        height = video_info.get('height', 0)
        if width % 2 != 0 or height % 2 != 0:
            new_width = width if width % 2 == 0 else width - 1
            new_height = height if height % 2 == 0 else height - 1
            if new_width > 0 and new_height > 0:
                vf_filters.append(f'scale={new_width}:{new_height}')

        # 帧率限制：由 ENABLE_FPS_LIMIT 全局开关控制（默认关闭，保持源帧率）
        if keep_source_fps is None:
            keep_source_fps = not _config_module.ENABLE_FPS_LIMIT
        if not keep_source_fps:
            source_fps = video_info.get('fps', 0)
            limit = get_fps_limit(source_fps)
            if limit is not None and source_fps > limit:
                vf_filters.append(f'fps={limit}')
                if log_callback:
                    log_callback("  帧率 %.2ffps > %dfps，限制为 %dfps" % (source_fps, limit, limit))

    if vf_filters:
        cmd += ['-vf', ','.join(vf_filters)]

    # 音频
    audio_args, audio_desc = build_audio_args(video_info, target_container)
    cmd += audio_args

    if target_container == 'mp4':
        cmd += ['-movflags', '+faststart']

    cmd += ['-progress', 'pipe:1', '-stats', '-y', str(output_path)]

    # 日志：加 profile/level
    profile_text = ""
    if profile:
        profile_text = ", profile=%s" % profile
    if level:
        profile_text += "@%s" % level
    if log_callback:
        if use_gpu:
            log_callback("  Full Encode [GPU] (编码器=%s, CQ=%d, preset=%s%s, 音频=%s)..." % (vcodec, crf_value, gpu_preset, profile_text, audio_desc))
        else:
            log_callback("  Full Encode (编码器=%s, CRF=%d, preset=%s%s, 音频=%s)..." % (vcodec, crf_value, gpu_preset, profile_text, audio_desc))

    process = None
    timer = None
    try:
        os.makedirs(str(output_path.parent), exist_ok=True)
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   universal_newlines=False, bufsize=0,
                                   creationflags=CREATE_NO_WINDOW)

        timer = threading.Timer(timeout_seconds, process.kill)
        timer.start()

        for raw_line in iter(process.stdout.readline, b''):
            line = raw_line.decode('utf-8', errors='replace').strip()
            if 'time=' in line:
                time_match = re.search(r'time=(\d+:\d+:\d+\.\d+)', line)
                if time_match:
                    current = parse_time_to_seconds(time_match.group(1))
                    if duration_known:
                        percent = min(current / total_duration * 100, 100)
                        if progress_callback:
                            progress_callback("%5.1f%%" % percent)
                    else:
                        if progress_callback:
                            progress_callback("已处理 %.0f秒" % current)

        return_code = process.wait()

        if return_code == 0 and output_path.exists() and os.path.getsize(str(output_path)) > 1024:
            if progress_callback:
                progress_callback("完成")
            return 'reencode'

        if log_callback:
            log_callback("  ❌ Full Encode 失败")
        return False
    finally:
        if timer:
            timer.cancel()
        if process:
            try:
                process.stdout.close()
            except Exception:
                pass
