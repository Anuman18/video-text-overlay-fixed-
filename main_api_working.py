import os
import subprocess
import textwrap
import requests
import shutil
import re
from uuid import uuid4
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import List, Optional
from PIL import Image, ImageDraw, ImageFont
from google.cloud import texttospeech
from pydub import AudioSegment
from pydub.utils import mediainfo

app = FastAPI()
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = "key.json"

# CONFIG
FONT_PATH = "assets/NotoSans-Regular.ttf"
FONT_SIZE = 36
WIDTH, HEIGHT = 720, 1280
OUTPUT = "uploads/video"
os.makedirs(OUTPUT, exist_ok=True)

class ContentItem(BaseModel):
    type: str
    url: str
    text: str
    overlay_url: Optional[str] = None

class ContentInput(BaseModel):
    language_code: str
    language_name: str
    logo_url: Optional[str] = None
    content: List[ContentItem]

def split_sentences(text):
    sentences = re.split(r'(?<=[.!?।])\s+', text.strip())
    return [s.strip() for s in sentences if s.strip()]

def create_subtitle_image(text, idx, i, CLIPS):
    img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(FONT_PATH, FONT_SIZE)

    lines = textwrap.wrap(text, width=38)
    line_height = draw.textbbox((0, 0), "A", font=font)[3] + 10
    total_height = len(lines) * line_height
    y_start = HEIGHT - 250 - total_height
    max_width = max([draw.textlength(line, font=font) for line in lines])
    padding_x, padding_y = 40, 20
    box_x1 = (WIDTH - max_width) // 2 - padding_x
    box_y1 = y_start - padding_y // 2
    box_x2 = (WIDTH + max_width) // 2 + padding_x
    box_y2 = y_start + total_height + padding_y // 2
    draw.rounded_rectangle([(box_x1, box_y1), (box_x2, box_y2)], radius=20, fill=(0, 0, 0, 180))
    y = y_start
    for line in lines:
        x = (WIDTH - draw.textlength(line, font=font)) // 2
        draw.text((x, y), line, font=font, fill="white")
        y += line_height

    path = os.path.join(CLIPS, f"subtitle_{idx}_{i}.png")
    img.save(path)
    return path

def generate_tts(text, path, language_code, language_name):
    client = texttospeech.TextToSpeechClient()
    synthesis_input = texttospeech.SynthesisInput(text=text)
    voice = texttospeech.VoiceSelectionParams(language_code=language_code, name=language_name)
    config = texttospeech.AudioConfig(audio_encoding=texttospeech.AudioEncoding.MP3, speaking_rate=0.85)
    response = client.synthesize_speech(input=synthesis_input, voice=voice, audio_config=config)
    with open(path, "wb") as out:
        out.write(response.audio_content)

def get_audio_duration(path):
    return float(mediainfo(path)['duration'])

def auto_crop_image(image_path, output_path):
    img = Image.open(image_path).convert("RGB")
    target_w, target_h = 720, 1280
    scale = max(target_w / img.width, target_h / img.height)
    img = img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)
    x = (img.width - target_w) // 2
    y = (img.height - target_h) // 2
    cropped = img.crop((x, y, x + target_w, y + target_h))
    cropped.save(output_path, "PNG", quality=95)

@app.post("/generate-video")
def generate_video(data: ContentInput):
    request_id = uuid4().hex
    CLIPS = os.path.join(OUTPUT, request_id)
    os.makedirs(CLIPS, exist_ok=True)

    try:
        # Process logo_url
        logo_path = None
        if data.logo_url:
            try:
                logo_ext = os.path.splitext(data.logo_url)[-1]
                logo_path = os.path.join(CLIPS, f"logo_{uuid4().hex}{logo_ext}")
                response = requests.get(data.logo_url, timeout=10)
                response.raise_for_status()
                with open(logo_path, "wb") as f:
                    f.write(response.content)
                logo_img = Image.open(logo_path).convert("RGBA").resize((200, 100))
                logo_img.save(logo_path)
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"❌ Failed to process logo: {str(e)}")

        segments = []
        for item in data.content:
            ext = ".mp4" if item.type == "video" else ".png"
            media_path = os.path.join(CLIPS, f"media_{uuid4().hex}{ext}")
            if item.type == "video":
                with open(media_path, "wb") as f:
                    f.write(requests.get(item.url).content)
            else:
                raw_path = os.path.join(CLIPS, f"raw_{uuid4().hex}.png")
                with open(raw_path, "wb") as f:
                    f.write(requests.get(item.url).content)
                auto_crop_image(raw_path, media_path)

            overlay_path = None
            if item.overlay_url:
                overlay_ext = os.path.splitext(item.overlay_url)[-1]
                overlay_path = os.path.join(CLIPS, f"overlay_{uuid4().hex}{overlay_ext}")
                with open(overlay_path, "wb") as f:
                    f.write(requests.get(item.overlay_url).content)
                overlay_img = Image.open(overlay_path).convert("RGBA").resize((720, 1280))
                overlay_img.save(overlay_path)

            segments.append({
                "type": item.type,
                "path": media_path,
                "text": item.text,
                "overlay": overlay_path
            })

        final_clips = []
        for idx, seg in enumerate(segments):
            sentences = split_sentences(seg["text"])
            for i, sentence in enumerate(sentences):
                audio_path = os.path.join(CLIPS, f"audio_{idx}_{i}.mp3")
                generate_tts(sentence, audio_path, data.language_code, data.language_name)
                duration = get_audio_duration(audio_path)
                subtitle_path = create_subtitle_image(sentence, idx, i, CLIPS)
                out_clip = os.path.join(CLIPS, f"clip_{idx}_{i}.mp4")

                cmd = ["ffmpeg"]

                if seg["type"] == "image":
                    cmd += ["-loop", "1", "-t", str(duration), "-i", seg["path"]]
                else:
                    cmd += ["-i", seg["path"]]

                cmd += ["-i", subtitle_path, "-i", audio_path]
                input_idx = 3

                if logo_path:
                    cmd += ["-i", logo_path]
                    logo_input_idx = input_idx
                    logo_input_idx = 3
                else:
                    logo_input_idx = None

                if seg["overlay"]:
                    cmd += ["-i", seg["overlay"]]
                    overlay_input_idx = 4 if logo_path else 3
                else:
                    overlay_input_idx = None

                # Filter chain
                fc = "[0:v]scale=720:1280,fps=25,format=rgba[bg];" \
                     "[bg][1:v]overlay=0:0[tmp1];"

                if logo_input_idx is not None:
                    fc += f"[tmp1][{logo_input_idx}:v]overlay=W-w-20:20[tmp2];"
                else:
                    fc += "[tmp1]null[tmp2];"

                if overlay_input_idx is not None:
                    fc += f"[tmp2][{overlay_input_idx}:v]overlay=0:0[v]"
                else:
                    fc += "[tmp2]null[v]"

                cmd += [
                    "-filter_complex", fc,
                    "-map", "[v]", "-map", "2:a", "-shortest",
                    "-c:v", "libx264", "-c:a", "aac", "-y", out_clip
                ]

                subprocess.run(cmd, check=True)
                final_clips.append(out_clip)

        final_path = os.path.join(OUTPUT, f"{request_id}.mp4")
        concat_list = os.path.join(CLIPS, "concat.txt")
        with open(concat_list, "w") as f:
            for clip in final_clips:
                f.write(f"file '{os.path.abspath(clip)}'\n")

        subprocess.run(["ffmpeg", "-f", "concat", "-safe", "0", "-i", concat_list, "-c", "copy", "-y", final_path], check=True)

        thumbnail_path = os.path.join("uploads/thumbnail", f"{request_id}.jpg")
        os.makedirs(os.path.dirname(thumbnail_path), exist_ok=True)
        subprocess.run(["ffmpeg", "-i", final_path, "-ss", "00:00:01.000", "-vframes", "1", "-q:v", "2", "-y", thumbnail_path], check=True)

        shutil.rmtree(CLIPS, ignore_errors=True)

        return JSONResponse({
            "message": "✅ Video created successfully",
            "video_path": os.path.basename(final_path),
            "thumbnail_path": os.path.basename(thumbnail_path)
        })

    except Exception as e:
        shutil.rmtree(CLIPS, ignore_errors=True)
        raise HTTPException(status_code=500, detail=f"❌ Error: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
