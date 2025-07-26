import os
import subprocess
from google.cloud import texttospeech
from pydub import AudioSegment
from pydub.utils import mediainfo
from PIL import Image, ImageDraw, ImageFont
import textwrap

# Set Google Credentials
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = "key.json"

# Directories
CLIPS_DIR = "uploads/clips"
OUTPUT_DIR = "uploads/final"
FONT_PATH = "assets/NotoSans-Regular.ttf"
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Video dimensions
WIDTH, HEIGHT = 1280, 720 
FONT_SIZE = 36

# Add 3 input texts (one for each video)
texts = [
    "क्या आपने कभी किसी नए व्यक्ति से बात करने में झिझक महसूस की है?",
    "कभी-कभी, एक अच्छा काम ही दोस्ती की शुरुआत बन जाता है।",
    "हर मुस्कान एक नई कहानी कह सकती है, बस ज़रा ध्यान से सुनिए।"
]

def generate_tts(text, output_path, language_code="hi-IN", voice_name="hi-IN-Wavenet-E"):
    client = texttospeech.TextToSpeechClient()
    input_text = texttospeech.SynthesisInput(text=text)
    voice = texttospeech.VoiceSelectionParams(language_code=language_code, name=voice_name)
    audio_config = texttospeech.AudioConfig(audio_encoding=texttospeech.AudioEncoding.MP3)
    response = client.synthesize_speech(input=input_text, voice=voice, audio_config=audio_config)
    with open(output_path, "wb") as out:
        out.write(response.audio_content)

def get_audio_duration(audio_path):
    return float(mediainfo(audio_path)['duration'])

def create_subtitle_image(text, output_path):
    img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(FONT_PATH, FONT_SIZE)

    lines = textwrap.wrap(text, width=50)
    line_height = draw.textbbox((0, 0), "A", font=font)[3] + 10
    total_height = len(lines) * line_height

    # Subtitle box closer to bottom (adjusted)
    y_start = HEIGHT - total_height - 80

    max_width = max([draw.textlength(line, font=font) for line in lines])
    padding_x = 40
    padding_y = 20
    box_x1 = (WIDTH - max_width) // 2 - padding_x
    box_y1 = y_start - padding_y // 2
    box_x2 = (WIDTH + max_width) // 2 + padding_x
    box_y2 = y_start + total_height + padding_y // 2
    draw.rounded_rectangle([(box_x1, box_y1), (box_x2, box_y2)], radius=20, fill=(0, 0, 0, 160))

    y = y_start
    for line in lines:
        text_width = draw.textlength(line, font=font)
        x = (WIDTH - text_width) // 2
        draw.text((x, y), line, font=font, fill="white")
        y += line_height

    img.save(output_path)

final_segments = []

for i in range(len(texts)):
    video_input = os.path.join(CLIPS_DIR, f"clip_{i}.mp4")
    audio_output = os.path.join(OUTPUT_DIR, f"audio_{i}.mp3")
    subtitle_output = os.path.join(OUTPUT_DIR, f"subtitle_{i}.png")
    final_output = os.path.join(OUTPUT_DIR, f"final_clip_{i}.mp4")

    # Generate TTS
    generate_tts(texts[i], audio_output)

    # Create subtitle image
    create_subtitle_image(texts[i], subtitle_output)

    # Get audio duration
    duration = get_audio_duration(audio_output)

    # FFmpeg command to merge video + subtitle + audio
    cmd = [
        "ffmpeg", "-i", video_input,
        "-i", subtitle_output,
        "-i", audio_output,
        "-filter_complex",
        "[0:v]scale=1280:720,fps=25,format=rgba[bg];[bg][1:v]overlay=0:0[v]",
        "-map", "[v]", "-map", "2:a",
        "-shortest", "-c:v", "libx264", "-c:a", "aac", "-y", final_output
    ]
    subprocess.run(cmd, check=True)
    final_segments.append(final_output)

# Concat all final segments into one video
concat_file = os.path.join(OUTPUT_DIR, "concat.txt")
with open(concat_file, "w") as f:
    for clip in final_segments:
        f.write(f"file '{os.path.abspath(clip)}'\n")

final_video_path = os.path.join(OUTPUT_DIR, "final_output.mp4")
subprocess.run([
    "ffmpeg", "-f", "concat", "-safe", "0", "-i", concat_file,
    "-c", "copy", "-y", final_video_path
], check=True)

print(f"✅ Final video saved at: {final_video_path}")

