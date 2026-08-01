@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

echo ============================================
echo  视频分辨率分类整理工具
echo  按分辨率自动归类到对应文件夹
echo ============================================
echo.

REM 支持的视频扩展名（共67种）
set EXTENSIONS=.mp4 .mkv .mov .avi .wmv .flv .webm .m4v .mpg .mpeg .ts
set EXTENSIONS=%EXTENSIONS% .m2ts .mts .m2t .vob .evo .mod .tod
set EXTENSIONS=%EXTENSIONS% .mxf .gxf .lxf .3gp .3g2 .asf
set EXTENSIONS=%EXTENSIONS% .rm .rmvb .divx .xvid
set EXTENSIONS=%EXTENSIONS% .ogv .ogm .drc .dv .fli .flc
set EXTENSIONS=%EXTENSIONS% .f4v .h264 .h265 .hevc .264 .265
set EXTENSIONS=%EXTENSIONS% .nsv .nut .m4p .mjpeg .mjpg
set EXTENSIONS=%EXTENSIONS% .yuv .rgb .gifv .webp .bik .smk
set EXTENSIONS=%EXTENSIONS% .ivf .vp8 .vp9 .av1 .avs .avs2
set EXTENSIONS=%EXTENSIONS% .wmvhd .wm .dvr-ms .wtv .ifo .iso

REM 遍历当前目录下的所有视频文件
for %%f in (*.*) do (
    set "ext=%%~xf"
    call :check_ext !ext!
    if !found! equ 1 (
        call :classify "%%f"
    )
)

echo.
echo 分类完成！

REM 自毁：删除自身
echo 正在删除本脚本...
(
    del /f /q "%~f0" >nul 2>&1
) && (
    echo 本脚本已自毁，再见！
) || (
    echo 自毁失败，请手动删除本文件。
)

exit /b

:check_ext
set found=0
for %%e in (%EXTENSIONS%) do (
    if /i "%~1"=="%%e" set found=1
)
goto :eof

:classify
set "file=%~1"

REM 使用 ffprobe 获取视频高度（分辨率）
for /f "usebackq tokens=*" %%a in (`ffprobe -v error -select_streams v:0 -show_entries stream^=height -of default^=noprint_wrappers^=1:nokey^=1 "!file!" 2^>nul`) do (
    set "height=%%a"
)

REM 如果获取失败，跳过
if not defined height (
    echo [跳过] !file! - 无法获取分辨率
    goto :eof
)

REM 根据高度分类（360p和480p合并到480）
if !height! geq 4320 (
    set "folder=8K"
) else if !height! geq 2160 (
    set "folder=4K"
) else if !height! geq 1536 (
    set "folder=2K"
) else if !height! geq 1080 (
    set "folder=1080"
) else if !height! geq 900 (
    set "folder=900p"
) else if !height! geq 720 (
    set "folder=720"
) else if !height! geq 576 (
    set "folder=576"
) else if !height! geq 540 (
    set "folder=540"
) else if !height! geq 360 (
    set "folder=480"
) else if !height! geq 240 (
    set "folder=240"
) else (
    set "folder=其他"
)

REM 创建文件夹（如果不存在）
if not exist "!folder!" mkdir "!folder!"

REM 移动文件
move "!file!" "!folder!\" >nul 2>&1
if !errorlevel! equ 0 (
    echo [移动] !file! ^(分辨率: !height!p^) ^-^> !folder!\ 成功
) else (
    echo [失败] !file! ^(分辨率: !height!p^) ^-^> !folder!\ 移动失败
)
goto :eof