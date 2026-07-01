import subprocess


def probe_video_duration(url) -> float:
    cmd = [
        'ffprobe',
        '-v', 'error',
        '-show_entries', 'format=duration',
        '-of', 'default=noprint_wrappers=1:nokey=1',
        url,
    ]
    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=15,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {result.stderr}")
    return float(result.stdout.strip())


def start_hls_segment_transcode(input_url, start, duration) -> subprocess.Popen:
    cmd = [
        'ffmpeg', '-ss', f'{start:.2f}', '-t', f'{duration:.2f}',
        '-i', input_url,
        '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '26',
        '-c:a', 'aac', '-b:a', '128k',
        '-output_ts_offset', f'{start:.2f}',
        '-muxdelay', '0',
        '-f', 'mpegts', 'pipe:1',
    ]
    return subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        bufsize=10**6,
    )


def start_flv_to_mp4_transcode(input_path) -> subprocess.Popen:
    cmd = [
        'ffmpeg', '-i', input_path,
        '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '28',
        '-c:a', 'aac', '-b:a', '128k',
        '-f', 'mp4', '-movflags', 'frag_keyframe+empty_moov',
        'pipe:1',
    ]
    return subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        bufsize=10**6,
    )
