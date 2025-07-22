import os
import textwrap
import subprocess
import requests
from uuid import uuid4
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import List, Optional
from PIL import Image, ImageDraw, ImageFont
from google.cloud import texttospeech
from pydub.utils import mediainfo

# Load Google Cloud credentials
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = "key.json"

# App & Configuration
app = FastAPI()

FONT_PATH = "assets/hindi.ttf"
FONT_SIZE = 42
WIDTH, HEIGHT = 720, 1280
BOX_HEIGHT = 200
WORDS_PER_CHUNK = 10
UPLOADS_DIR = "uploads"
os.makedirs(UPLOADS_DIR, exist_ok=True)

# Input Models
class ContentItem(BaseModel):
    type: str  # "video" or "image"
    url: str
    text: str

class ContentList(BaseModel):
    language_code: str
    language_name: str
    content: List[ContentItem]
    logo_url: Optional[str] = None

# Utilities
def wrap_text(text, width=35):
    return textwrap.wrap(text, width=width)

def split_text_chunks(text):
    words = text.split()
    return [" ".join(words[i:i + WORDS_PER_CHUNK]) for i in range(0, len(words), WORDS_PER_CHUNK)]

def create_subtitle_image(text, idx, i, clips_dir):
    img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(FONT_PATH, FONT_SIZE)
    lines = wrap_text(text)
    line_height = draw.textbbox((0, 0), "A", font=font)[3] + 12
    total_height = len(lines) * line_height
    y = HEIGHT - BOX_HEIGHT + (BOX_HEIGHT - total_height) // 2
    draw.rectangle([(0, HEIGHT - BOX_HEIGHT), (WIDTH, HEIGHT)], fill=(0, 0, 0, 200))
    for line in lines:
        w = draw.textlength(line, font=font)
        x = (WIDTH - w) // 2
        draw.text((x, y), line, font=font, fill="white")
        y += line_height
    path = os.path.join(clips_dir, f"subtitle_{idx}_{i}.png")
    img.save(path)
    return path

def generate_tts(text, path, language_code, language_name):
    client = texttospeech.TextToSpeechClient()
    input_text = texttospeech.SynthesisInput(text=text)
    voice = texttospeech.VoiceSelectionParams(language_code=language_code, name=language_name)
    audio_config = texttospeech.AudioConfig(audio_encoding=texttospeech.AudioEncoding.MP3, speaking_rate=0.92)
    response = client.synthesize_speech(input=input_text, voice=voice, audio_config=audio_config)
    with open(path, "wb") as out:
        out.write(response.audio_content)

def get_audio_duration(path):
    return float(mediainfo(path)['duration'])

def download_media(url, ext, clips_dir, filename=None):
    response = requests.get(url)
    if response.status_code != 200:
        raise Exception(f"Failed to download media from: {url}")
    name = filename or f"{uuid4().hex}{ext}"
    file_path = os.path.join(clips_dir, name)
    with open(file_path, "wb") as f:
        f.write(response.content)
    return file_path

# API Endpoint
@app.post("/generate-video")
def generate_video(data: ContentList):
    import shutil

    request_id = uuid4().hex
    request_dir = os.path.join(UPLOADS_DIR, request_id)
    clips_dir = os.path.join(request_dir, "clips")
    os.makedirs(clips_dir, exist_ok=True)

    final_segments = []
    logo_path = None

    try:
        # Download logo if provided
        if data.logo_url:
            logo_path = download_media(data.logo_url, ".png", clips_dir, filename="logo.png")
            logo_img = Image.open(logo_path)
            logo_img.thumbnail((100, 100))
            logo_img.save(logo_path)

        for idx, item in enumerate(data.content):
            chunks = split_text_chunks(item.text)
            ext = ".mp4" if item.type == "video" else ".jpg"
            media_path = download_media(item.url, ext, clips_dir)

            for i, chunk in enumerate(chunks):
                audio_path = os.path.join(clips_dir, f"audio_{idx}_{i}.mp3")
                generate_tts(chunk, audio_path, data.language_code, data.language_name)
                duration = get_audio_duration(audio_path)
                subtitle_img = create_subtitle_image(chunk, idx, i, clips_dir)

                if item.type == 'video':
                    media_input = ["-i", media_path]
                else:
                    media_input = ["-loop", "1", "-t", str(duration), "-i", media_path]

                inputs = media_input + ["-i", subtitle_img, "-i", audio_path]
                if logo_path:
                    inputs += ["-i", logo_path]

                output_clip = os.path.join(clips_dir, f"clip_{idx}_{i}.mp4")

                filters = f"[0:v] scale={WIDTH}:{HEIGHT},fps=25,format=rgba [bg];"
                filters += "[bg][1:v] overlay=0:0 [tmp];"
                if logo_path:
                    filters += "[tmp][3:v] overlay=W-w-20:20 [v];"
                else:
                    filters += "[tmp] null [v];"

                audio_index = 2  # always 2nd index for audio
                cmd = [
                    "ffmpeg", *inputs,
                    "-filter_complex", filters,
                    "-map", "[v]", "-map", f"{audio_index}:a",
                    "-shortest", "-c:v", "libx264", "-c:a", "aac", "-y", output_clip
                ]

                subprocess.run(cmd, check=True)
                final_segments.append(output_clip)

        # Concatenate all final clips
        concat_file = os.path.join(request_dir, "concat.txt")
        with open(concat_file, "w") as f:
            for clip in final_segments:
                f.write(f"file '{os.path.abspath(clip)}'\n")

        final_output = os.path.join(request_dir, "final_output.mp4")
        subprocess.run(["ffmpeg", "-f", "concat", "-safe", "0", "-i", concat_file, "-c", "copy", "-y", final_output], check=True)

        # Clean up everything else
        for item in os.listdir(request_dir):
            full_path = os.path.join(request_dir, item)
            if full_path != final_output:
                if os.path.isdir(full_path):
                    shutil.rmtree(full_path)
                else:
                    os.remove(full_path)

        return JSONResponse(content={"message": "✅ Final video created", "video_path": final_output})

    except Exception as e:
        shutil.rmtree(request_dir, ignore_errors=True)
        raise HTTPException(status_code=500, detail=f"Error occurred: {str(e)}")
