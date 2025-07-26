import os
import subprocess
from google.cloud import texttospeech
from pydub.utils import mediainfo
from PIL import Image, ImageDraw, ImageFont
import textwrap

# Set credentials
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = "key.json"

# Configs
CLIPS_DIR = "uploads/clips"
OUTPUT_DIR = "uploads/final"
BGM_PATH = "assets/background.mp3"
FONT_PATH = "assets/NotoSans-Regular.ttf"
WIDTH, HEIGHT = 1280, 720
FONT_SIZE = 36

os.makedirs(OUTPUT_DIR, exist_ok=True)

texts = [
    "Ever felt stuck, even with everyone around?",
    "That’s where support begins — when one helps, both grow.",
    "Networking means building a circle where everyone adds something valuable.",
    "Support today becomes strength tomorrow.",
    "This is social capital. Not just collecting contacts. Building real trust."
]

def generate_tts(text, output_path):
    print(f"📢 Generating TTS: {text}")
    client = texttospeech.TextToSpeechClient()
    input_text = texttospeech.SynthesisInput(text=text)
    voice = texttospeech.VoiceSelectionParams(
        language_code="hi-IN",
        name="hi-IN-Wavenet-E"
    )
    audio_config = texttospeech.AudioConfig(audio_encoding=texttospeech.AudioEncoding.MP3)
    response = client.synthesize_speech(input=input_text, voice=voice, audio_config=audio_config)
    with open(output_path, "wb") as out:
        out.write(response.audio_content)

def get_audio_duration(path):
    return float(mediainfo(path)['duration'])

def create_subtitle_image(text, output_path):
    img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(FONT_PATH, FONT_SIZE)

    lines = textwrap.wrap(text, width=50)
    line_height = draw.textbbox((0, 0), "A", font=font)[3] + 10
    total_height = len(lines) * line_height
    y_start = HEIGHT - total_height - 80

    max_width = max([draw.textlength(line, font=font) for line in lines])
    padding_x, padding_y = 40, 20
    box = [
        ((WIDTH - max_width) // 2 - padding_x, y_start - padding_y // 2),
        ((WIDTH + max_width) // 2 + padding_x, y_start + total_height + padding_y // 2)
    ]
    draw.rounded_rectangle(box, radius=20, fill=(0, 0, 0, 160))

    y = y_start
    for line in lines:
        x = (WIDTH - draw.textlength(line, font=font)) // 2
        draw.text((x, y), line, font=font, fill="white")
        y += line_height

    img.save(output_path)

final_segments = []

for i, text in enumerate(texts):
    print(f"\n▶️ Processing clip {i}")
    video_input = os.path.join(CLIPS_DIR, f"clip_{i}.mp4")
    audio_output = os.path.join(OUTPUT_DIR, f"tts_{i}.mp3")
    subtitle_output = os.path.join(OUTPUT_DIR, f"subtitle_{i}.png")
    final_output = os.path.join(OUTPUT_DIR, f"final_clip_{i}.mp4")

    generate_tts(text, audio_output)
    create_subtitle_image(text, subtitle_output)

    tts_duration = get_audio_duration(audio_output)
    video_duration = get_audio_duration(video_input)

    filter_complex = (
    f"[0:v]scale=1280:720,fps=25[scaled];"
    f"[scaled][1:v]overlay=0:0[video_sub];"
    f"[3:a]adelay=0|0,volume=0.6[bgm];"
    f"[2:a]volume=1.5[tts];"
    f"[bgm][tts]amix=inputs=2:duration=first[audio_mix]"
)

    cmd = [
        "ffmpeg", "-y",
        "-i", video_input,
        "-i", subtitle_output,
        "-i", audio_output,
        "-i", BGM_PATH,
        "-filter_complex", filter_complex,
        "-map", "[video_sub]",
        "-map", "[audio_mix]",
        "-c:v", "libx264",
        "-c:a", "aac",
        "-t", str(video_duration),
        final_output
    ]

    subprocess.run(cmd, check=True)
    final_segments.append(final_output)

# Concat all clips
concat_file = os.path.join(OUTPUT_DIR, "concat.txt")
with open(concat_file, "w") as f:
    for clip in final_segments:
        f.write(f"file '{os.path.abspath(clip)}'\n")

final_video_path = os.path.join(OUTPUT_DIR, "final_output.mp4")
subprocess.run([
    "ffmpeg", "-f", "concat", "-safe", "0", "-i", concat_file,
    "-c", "copy", "-y", final_video_path
], check=True)

print(f"\n✅ Final video saved at: {final_video_path}")
