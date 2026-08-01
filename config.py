import os

# ============================================================
# 视频扩展名白名单（所有可识别的视频文件）
# ============================================================
VIDEO_EXTENSIONS = {
    ".mp4", ".mov", ".avi", ".mkv", ".wmv", ".flv", ".webm",
    ".m4v", ".mpg", ".mpeg", ".ts",
    ".m2ts", ".mts", ".m2t", ".vob", ".evo", ".mod", ".tod",
    ".mxf", ".gxf", ".lxf", ".3gp", ".3g2", ".asf",
    ".rm", ".rmvb", ".divx", ".xvid",
    ".ogv", ".ogm", ".drc", ".dv", ".fli", ".flc",
    ".f4v", ".h264", ".h265", ".hevc", ".264", ".265",
    ".nsv", ".nut", ".m4p", ".mjpeg", ".mjpg",
    ".yuv", ".rgb",
    ".m2v", ".tp", ".swf",
}

# ============================================================
# 现代封装容器（无需兼容性转封装）
# ============================================================
MODERN_EXTENSIONS = {".mp4", ".mkv", ".webm", ".mov"}

# ============================================================
# 老式 / 特殊封装容器（需兼容性处理）
# 来自新规则：.m2ts, .mts, .vob, .m2v, .ts, .tp / .mxf, .gxf, .lxf
#             .rm, .rmvb, .wmv, .avi, .flv, .swf / .mpg, .mpeg, .mod, .tod
#             .3gp, .3g2, .asf, .ogv / MOV + 老式编码（由 is_legacy_format 动态判定）
# ============================================================
LEGACY_EXTENSIONS = {
    ".m2ts", ".mts", ".m2t", ".m2v", ".vob", ".ts", ".tp",
    ".mxf", ".gxf", ".lxf",
    ".rm", ".rmvb", ".wmv", ".avi", ".flv", ".swf",
    ".mpg", ".mpeg", ".mod", ".tod",
    ".3gp", ".3g2", ".asf", ".ogv",
    # 以下为旧版兼容保留
    ".evo", ".divx", ".xvid", ".ogm", ".drc", ".dv",
    ".fli", ".flc", ".f4v", ".h264", ".h265", ".hevc",
    ".264", ".265", ".nsv", ".nut", ".m4p",
    ".mjpeg", ".mjpg", ".yuv", ".rgb",
}

# ============================================================
# 现代视频编码（无需转码）
# ============================================================
MODERN_CODECS = {
    "h264", "hevc", "h265", "av1",
}

# ============================================================
# 老式 / 特殊视频编码（不论容器，需转码）
# 新规则白名单：MPEG-1, MPEG-2, ProRes, DV, Cinepak, Sorenson, WMV1/2/3,
#              RealVideo, Theora, Flash Video (VP6/Sorenson Spark), MJPEG
# ============================================================
LEGACY_CODECS = {
    "mpeg1video", "mpeg2video", "mpeg2", "mpeg",
    "prores", "prores_aw", "prores_ks", "prores_lt", "prores_standard",
    "prores_hq", "prores_4444", "prores_xq",
    "dvvideo", "dv", "dvcp", "dvcp50", "dvcp100", "dvcpro", "dvcpro50", "dvcpro100",
    "cinepak", "indeo3", "indeo4", "indeo5",
    "sorenson", "svq1", "svq3",
    "wmv1", "wmv2", "wmv3", "wvc1", "vc1",
    "rv10", "rv20", "rv30", "rv40",
    "theora",
    "flv1", "flashsv", "flashsv2", "vp6", "vp6a", "vp6f",
    "mjpeg", "mjpeg_b",
    "h263", "h263p", "h263i",
    "msmpeg4v2", "msmpeg4", "msmpeg4v1",
}

# 编码效率系数（旧版兼容保留，仅用于估算）
CODEC_EFFICIENCY = {
    "h264": 1.0,
    "hevc": 0.6,
    "h265": 0.6,
    "av1": 0.55,
    "vp9": 0.65,
    "mpeg4": 1.2,
    "wmv3": 1.3,
    "mpeg2video": 1.5,
}

# ============================================================
# 编码质量参数（影视优化标定，v2.5）
# CPU x264 / CPU x265 / NVENC H.264 / NVENC HEVC
# 每项四档：视觉无损 / 高质量(默认) / 均衡 / 高压缩
# ≤360p 特别值（防块效应）
# ============================================================
QUALITY_TABLE = {
    "x264": {  # CPU H.264
        "lossless": 18, "high": 20, "balanced": 23, "high_compress": 26,
        "le360p": 19,
    },
    "x265": {  # CPU H.265
        "lossless": 20, "high": 22, "balanced": 25, "high_compress": 28,
        "le360p": 21,
    },
    "nvenc_h264": {  # NVENC H.264
        "lossless": 17, "high": 19, "balanced": 21, "high_compress": 24,
        "le360p": 20,
    },
    "nvenc_hevc": {  # NVENC HEVC
        "lossless": 19, "high": 21, "balanced": 23, "high_compress": 26,
        "le360p": 22,
    },
}

# ============================================================
# 合理码率参考表（仅作判定阈值，非压制目标）
# 单位：Mbps（旧版 kbps，新规则统一 Mbps，内部使用 bps）
# 字段：高质量 / 均衡(参考) / 高压缩
# ============================================================
BITRATE_REFERENCE_MBPS = {
    "4K":   {"high": 25,  "balanced": 16,  "high_compress": 10},
    "2K":   {"high": 16,  "balanced": 10,  "high_compress": 6},
    "1080p":{"high": 8,   "balanced": 5,   "high_compress": 3},
    "720p": {"high": 4,   "balanced": 2.5, "high_compress": 1.5},
    "480p": {"high": 1.5, "balanced": 1,   "high_compress": 0.6},
    "360p": {"high": 1.0, "balanced": 0.6, "high_compress": 0.35},
    "240p": {"high": 0.4, "balanced": 0.25,"high_compress": 0.15},
    "144p": {"high": 0.25,"balanced": 0.15,"high_compress": 0.1},
}

# ============================================================
# 帧率规则
# 合法帧率白名单：23.976 / 24 / 25 / 29.97 / 30 fps
# > 30fps 强制降级为 30fps
# < 30fps 且不在白名单 → 保持原样
# ============================================================
FPS_WHITELIST = {23.976, 24.0, 25.0, 29.97, 30.0}
FPS_HARD_LIMIT = 30.0
FPS_WHITELIST_TOLERANCE = 0.05  # 容差，处理 23.98 等精度问题

# ============================================================
# 帧率限制开关（v2.6 新增）
# False（默认）：压缩时保持源帧率不变
# True：压缩时 >30fps 强制降为 30fps
# 仅对重新编码（compress_video / reencode_video）有效，
# 流拷贝（Remux / Passthrough）不受影响
# ============================================================
ENABLE_FPS_LIMIT = False

# ============================================================
# 4K 压缩开关（v2.5 新增）
# 默认 false：跳过所有 4K
# 设为 true 时：仅对码率 > 48 Mbps（3× 均衡值 16Mbps）的 4K 文件执行压缩
# ============================================================
COMPRESS_4K_DEFAULT = False
COMPRESS_4K_BITRATE_THRESHOLD_MBPS = 48  # 3 × 16Mbps

# ============================================================
# 码率判定阈值
# ============================================================
BITRATE_REASONABLE_MULTIPLIER = 2.0       # 现代编码码率 ≤ 2× 均衡值 → 合理
BITRATE_LOWRES_SKIP_MULTIPLIER = 1.5     # ≤360p 现代编码码率 ≤ 1.5× 均衡值 → 强制跳过
BITRATE_TOO_LOW_KBPS = 300               # 码率 < 300kbps → 跳过（再压无意义）

# ============================================================
# 文件大小阈值
# ============================================================
MIN_FILE_SIZE_MB = 100
FFMPEG_BAR_WIDTH = 48

# ============================================================
# 模式定义（影视 / 生活归档专用 v2.5 Final）
# output_dir：在源文件目录下新建的子文件夹名后缀
# ============================================================
MODE_INFO = {
    0: {
        "name": "模式0：仅分析（不解码、不编码、不移动）",
        "output_dir": "视频分析报告"
    },
    1: {
        "name": "模式1：老片保画质（归档首选）",
        "output_dir": "压缩输出_老片保画质"
    },
    2: {
        "name": "模式2：老片省空间（老视频专用）",
        "output_dir": "压缩输出_老片省空间"
    },
    3: {
        "name": "模式3：小文件放行（扫盘归档）",
        "output_dir": "压缩输出_小文件放行"
    },
    4: {
        "name": "模式4：极速模式（最小化磁盘 I/O）",
        "output_dir": "压缩输出_极速模式"
    },
    5: {
        "name": "模式5：超高清压缩（4K / 2K / 1080p）",
        "output_dir": "压缩输出_超高清"
    },
    6: {
        "name": "模式6：老式格式转现代封装（兼容性优先）",
        "output_dir": "老格式转换输出"
    },
    7: {
        "name": "模式7：全部转为 MP4",
        "output_dir": "视频转MP4输出"
    },
    8: {
        "name": "模式8：网络分发（1080p 生活视频分享）",
        "output_dir": "压缩输出_网络分发"
    }
}
