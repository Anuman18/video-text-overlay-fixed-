import os
import subprocess
import textwrap
import requests
import shutil
from uuid import uuid4
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import List, Optional
from PIL import Image, ImageDraw, ImageFont
from google.cloud import texttospeech
from pydub import AudioSegment
from pydub.utils import mediainfo

# FastAPI app
app = FastAPI()

# Google Cloud credentials
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = "key.json"

# Configuration
FONT_PATH = "assets/NotoSans-Regular.ttf"
FONT_SIZE = 42
WIDTH, HEIGHT = 720, 1280
BOX_HEIGHT = 200
WORDS_PER_CHUNK = 10
OUTPUT = "upload"
os.makedirs(OUTPUT, exist_ok=True)

# Input schema
class ContentItem(BaseModel):
    type: str  # "video" or "image"
    url: str
    text: str

class ContentInput(BaseModel):
    language_code: str
    language_name: str
    logo_url: Optional[str] = None
    content: List[ContentItem]

# Helpers
def wrap_text(text, width=35):
    return textwrap.wrap(text, width=width)

def split_text_chunks(text):
    words = text.split()
    return [" ".join(words[i:i + WORDS_PER_CHUNK]) for i in range(0, len(words), WORDS_PER_CHUNK)]

def create_subtitle_image(text, idx, i, CLIPS):
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
    path = os.path.join(CLIPS, f"subtitle_{idx}_{i}.png")
    img.save(path)
    return path

def generate_tts(text, path, language_code, language_name):
    client = texttospeech.TextToSpeechClient()
    input_text = texttospeech.SynthesisInput(text=text)
    voice = texttospeech.VoiceSelectionParams(language_code=language_code, name=language_name)
    audio_config = texttospeech.AudioConfig(audio_encoding=texttospeech.AudioEncoding.MP3, speaking_rate=0.80)
    response = client.synthesize_speech(input=input_text, voice=voice, audio_config=audio_config)
    with open(path, "wb") as out:
        out.write(response.audio_content)

def get_audio_duration(path):
    return float(mediainfo(path)['duration'])

# Main API
@app.post("/generate-video")
def generate_video(data: ContentInput):
    request_id = uuid4().hex
    CLIPS = os.path.join(OUTPUT, request_id)
    os.makedirs(CLIPS, exist_ok=True)

    try:
        # Download media
        content = []
        for item in data.content:
            ext = ".mp4" if item.type == "video" else ".jpg"
            filename = os.path.join(CLIPS, f"{uuid4().hex}{ext}")
            r = requests.get(item.url)
            with open(filename, "wb") as f:
                f.write(r.content)
            content.append({
                "type": item.type,
                "path": filename,
                "text": item.text
            })

        final_segments = []

        for idx, item in enumerate(content):
            chunks = split_text_chunks(item["text"])

            if item['type'] == 'video':
                audio_paths = []
                subtitle_paths = []
                durations = []

                for i, chunk in enumerate(chunks):
                    audio_path = os.path.join(CLIPS, f"audio_{idx}_{i}.mp3")
                    generate_tts(chunk, audio_path, data.language_code, data.language_name)
                    duration = get_audio_duration(audio_path)
                    durations.append(duration)
                    subtitle_path = create_subtitle_image(chunk, idx, i, CLIPS)
                    audio_paths.append(audio_path)
                    subtitle_paths.append(subtitle_path)

                # Combine audio
                combined_audio_path = os.path.join(CLIPS, f"combined_audio_{idx}.mp3")
                combined = AudioSegment.empty()
                for audio in audio_paths:
                    combined += AudioSegment.from_mp3(audio)
                combined.export(combined_audio_path, format="mp3")

                # Build ffmpeg command
                cmd = ["ffmpeg", "-i", item['path']]
                for subtitle in subtitle_paths:
                    cmd += ["-i", subtitle]
                cmd += ["-i", combined_audio_path]

                filter_complex = f"[0:v] scale={WIDTH}:{HEIGHT},fps=25,format=rgba [base];"
                current_time = 0
                for i in range(len(subtitle_paths)):
                    input_label = f"[{'base' if i == 0 else f'tmp{i}'}][{i+1}:v]"
                    output_label = f"[{'tmp' + str(i+1) if i < len(subtitle_paths) - 1 else 'v'}]"
                    enable = f"enable='between(t,{current_time},{current_time + durations[i]})'"
                    filter_complex += f"{input_label} overlay=0:0:{enable} {output_label};"
                    current_time += durations[i]

                output_path = os.path.join(CLIPS, f"clip_{idx}_merged.mp4")
                cmd += [
                    "-filter_complex", filter_complex.rstrip(";"),
                    "-map", "[v]", "-map", f"{len(subtitle_paths)+1}:a",
                    "-shortest", "-c:v", "libx264", "-c:a", "aac",
                    "-y", output_path
                ]

                subprocess.run(cmd, check=True)
                final_segments.append(output_path)

            else:
                for i, chunk in enumerate(chunks):
                    audio_path = os.path.join(CLIPS, f"audio_{idx}_{i}.mp3")
                    generate_tts(chunk, audio_path, data.language_code, data.language_name)
                    duration = get_audio_duration(audio_path)
                    subtitle_path = create_subtitle_image(chunk, idx, i, CLIPS)
                    output_path = os.path.join(CLIPS, f"clip_{idx}_{i}.mp4")

                    cmd = [
                        "ffmpeg", "-loop", "1", "-t", str(duration), "-i", item['path'],
                        "-i", subtitle_path, "-i", audio_path,
                        "-filter_complex", f"[0:v] scale={WIDTH}:{HEIGHT},fps=25,format=rgba [bg];[bg][1:v] overlay=0:0 [v]",
                        "-map", "[v]", "-map", "2:a", "-shortest",
                        "-c:v", "libx264", "-c:a", "aac", "-y", output_path
                    ]
                    subprocess.run(cmd, check=True)
                    final_segments.append(output_path)

        final_output = os.path.join(OUTPUT, f"{request_id}.mp4")
        concat_file = os.path.join(CLIPS, "concat.txt")
        with open(concat_file, "w") as f:
            for clip in final_segments:
                f.write(f"file '{os.path.abspath(clip)}'\n")

        subprocess.run([
            "ffmpeg", "-f", "concat", "-safe", "0", "-i", concat_file,
            "-c", "copy", "-y", final_output
        ], check=True)

        # Clean up temp files
        shutil.rmtree(CLIPS, ignore_errors=True)
                # Generate thumbnail
        thumbnail_dir = os.path.join("thumbnails")
        os.makedirs(thumbnail_dir, exist_ok=True)
        thumbnail_path = os.path.join(thumbnail_dir, f"{request_id}.jpg")

        subprocess.run([
            "ffmpeg", "-i", final_output,
            "-ss", "00:00:01.000", "-vframes", "1",
            "-q:v", "2", "-y", thumbnail_path
        ], check=True)


        return JSONResponse(content={"message": "✅ Final video created", "video_path": final_output,"thumbnail_path": thumbnail_path})

    except Exception as e:
        shutil.rmtree(CLIPS, ignore_errors=True)
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")
