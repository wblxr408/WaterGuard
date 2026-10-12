"""Encode original timed browser captures, preserving actual UI operation time."""
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'packages/contracts'))
from waterguard_contracts.core import digest_bytes, require


def stamp(seconds):
    centiseconds = round(seconds*100)
    h, rest = divmod(centiseconds, 360000)
    m, rest = divmod(rest, 6000)
    s, cs = divmod(rest, 100)
    return f'{h}:{m:02d}:{s:02d}.{cs:02d}'


def run():
    folder = ROOT/'artifacts/contest-video'
    source = folder/'video-capture.json'
    capture = json.loads(source.read_text(encoding='utf-8'))
    require(len(capture['parts']) == 10, 'Ten actual recorded parts required')
    lines = ['ffconcat version 1.0']
    captions = []
    elapsed = 0
    frame_count = 0
    for part in capture['parts']:
        frames = part['frames']
        require(bool(frames) and part['duration_s'] >= 20, 'Incomplete recording')
        for i, frame in enumerate(frames):
            path = Path(frame['path'])
            require(path.is_file() and path.resolve().is_relative_to(folder.resolve()), 'Missing original recorded frame')
            after = frames[i+1]['elapsed_s'] if i+1<len(frames) else part['duration_s']
            duration = after-frame['elapsed_s']+(frames[0]['elapsed_s'] if i == 0 else 0)
            require(duration > 0, 'Recording timestamps are not monotonic')
            lines += ["file '"+path.as_posix().replace("'", "'\\''")+"'", 'option framerate 1000', f'duration {duration:.6f}']
            frame_count += 1
        label = part['label']
        if len(label)>30:
            pivot = label.rfind('，', 0, 32)
            if pivot<12:
                pivot = 30
            label = label[:pivot]+'\\N'+label[pivot:].lstrip('，')
        captions.append(f'Dialogue: 0,{stamp(elapsed)},{stamp(elapsed+part["duration_s"])},Caption,,0,0,0,,{label}')
        elapsed += part['duration_s']
    lines.append("file '"+Path(capture['parts'][-1]['frames'][-1]['path']).as_posix()+"'")
    lines.append('option framerate 1000')
    (folder/'recorded-frames.txt').write_text('\n'.join(lines)+'\n', encoding='utf-8')
    ass = '''[Script Info]
ScriptType: v4.00+
PlayResX: 1280
PlayResY: 720
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,Microsoft YaHei,22,&H00FFFFFF,&H000000FF,&H00202A2B,&H90000000,0,0,0,0,100,100,0,0,3,2,0,2,38,38,12,1
Style: Method,Microsoft YaHei,16,&H00FFFFFF,&H000000FF,&H00202A2B,&H90000000,0,0,0,0,100,100,0,0,3,1,0,8,32,32,7,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
'''
    ass += f'Dialogue: 1,0:00:00.00,{stamp(elapsed)},Method,,0,0,0,,真实 AGH 已完成运行回看 · 原始截图录屏约2帧/秒 · 保留实际操作时长\n'
    ass += '\n'.join(captions)+'\n'
    (folder/'captions.ass').write_text(ass, encoding='utf-8')
    output = folder/'WaterGuard-demonstration.mp4'
    subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'warning', '-y', '-safe', '0',
        '-f', 'concat', '-i', str(folder/'recorded-frames.txt'),
        '-vf', 'scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2,fps=25,subtitles=artifacts/contest-video/captions.ass',
        '-c:v', 'libx264', '-preset', 'fast', '-crf', '20', '-pix_fmt', 'yuv420p',
        '-t', f'{elapsed:.6f}', '-movflags', '+faststart', str(output)], cwd=ROOT, check=True)
    probe = json.loads(subprocess.check_output(['ffprobe','-v','error','-show_format','-show_streams','-of','json',str(output)]))
    actual = float(probe['format']['duration'])
    require(180<=actual<=300 and abs(actual-elapsed)<.2, 'Encoded duration differs from real recording')
    report = dict(schema_version='1.0.0', method=capture['method'], parts=len(capture['parts']),
        original_frames=frame_count, captured_duration_s=elapsed, encoded_duration_s=actual,
        recording_sha256=digest_bytes(source.read_bytes()), video_sha256=digest_bytes(output.read_bytes()),
        size_bytes=output.stat().st_size, width=1280, height=720, output_fps=25,
        interpretation='Output frames repeat actual sampled browser frames. This is completed-run replay, not accelerated or claimed realtime solver execution.')
    (folder/'render.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))


if __name__ == '__main__':
    run()
