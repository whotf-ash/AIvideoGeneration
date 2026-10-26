import os
import gc
import random
import logging
import asyncio
import json
import glob
import subprocess
import requests
import re
import textwrap
import PIL.Image
import PIL.ImageDraw
import PIL.ImageFont

from dotenv import load_dotenv

# Enable system logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

load_dotenv(override=True)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY")

from google import genai
import edge_tts
from gtts import gTTS
from moviepy.editor import AudioFileClip
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

ai_client = genai.Client(api_key=GEMINI_API_KEY)

# List of high-quality female Edge TTS voices for dynamic randomization
FEMALE_VOICES = [
    "en-US-AvaNeural",
    "en-US-EmmaNeural",
    "en-US-JennyNeural",
    "en-US-AriaNeural",
    "en-GB-SoniaNeural",
    "en-AU-NatashaNeural"
]

def generate_structured_script(topic: str) -> list[dict]:
    """Generates an AI script for when the user DOES NOT provide a custom script."""
    prompt = (
        f"Create an ultra-engaging, fast-paced 30-second YouTube Short script about '{topic}'. "
        "Start directly with a hook. Break the script into 7-10 short scenes. "
        "CRITICAL RULE: Each scene's 'text' should be a medium sentence (about 8 to 12 words). "
        "This is so the background clip changes every 2 to 4 seconds while the caption remains readable. "
        "For each scene, provide a highly relevant 2-3 word visual search term for stock videos. "
        "Return ONLY a valid JSON array of objects with keys 'text' and 'keyword'."
    )
    
    try:
        response = ai_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt
        )
        
        clean_json = response.text.strip()
        if clean_json.startswith("```json"):
            clean_json = clean_json[7:]
        if clean_json.endswith("```"):
            clean_json = clean_json[:-3]
            
        return json.loads(clean_json.strip())
    except Exception as e:
        logging.error(f"Failed to parse JSON script from Gemini: {e}")
        return [
            {"text": f"This is all you need to know about {topic[:15]}.", "keyword": "cinematic technology"},
            {"text": "Things are moving faster than ever before.", "keyword": "fast motion background"}
        ]


def parse_custom_script(raw_text: str, fallback_topic: str) -> list[dict]:
    """
    Splits custom scripts into chunks of roughly 8-15 words.
    This guarantees the video clips will swap every 2 to 4 seconds.
    """
    # Split by major punctuation first
    raw_sentences = re.split(r'(?<=[.!?])\s+', raw_text.strip())
    scenes = []
    
    for sentence in raw_sentences:
        sentence = sentence.strip()
        if not sentence: 
            continue
            
        words = sentence.split()
        # If a chunk is larger than 14 words, split it in half to keep clips changing fast
        if len(words) > 14:
            half = len(words) // 2
            scenes.append({"text": " ".join(words[:half]), "keyword": f"cinematic {fallback_topic}"})
            scenes.append({"text": " ".join(words[half:]), "keyword": f"cinematic {fallback_topic}"})
        else:
            scenes.append({"text": sentence, "keyword": f"cinematic {fallback_topic}"})
            
    return scenes


def generate_social_caption(topic: str) -> str:
    prompt = f"Write a sweet, catchy YouTube Shorts caption for a video about '{topic}'. Keep it to 1 sentence with 5 trendy hashtags."
    try:
        response = ai_client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
        return response.text.strip()
    except Exception:
        return f"✨ Discover {topic}! #Shorts #Trending #Viral"


async def generate_voiceover(text: str, output_path: str = "voice.mp3", voice_name: str = None):
    selected_voice = voice_name or random.choice(FEMALE_VOICES)
    try:
        communicate = edge_tts.Communicate(text, selected_voice, rate="+15%")
        await communicate.save(output_path)
    except Exception as e:
        logging.warning(f"Edge-TTS failed ({e}), using gTTS fallback...")
        tts = gTTS(text=text, lang="en")
        tts.save(output_path)


def is_valid_video(file_path: str) -> bool:
    if not os.path.exists(file_path) or os.path.getsize(file_path) < 50000:
        return False
    try:
        cmd = [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=codec_name", "-of",
            "default=noprint_wrappers=1:nokey=1", file_path
        ]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5)
        return len(result.stdout.strip()) > 0
    except Exception:
        return False


def download_pexels_clip(keyword: str, output_path: str) -> bool:
    headers = {"Authorization": PEXELS_API_KEY}
    for attempt in range(3):
        random_page = random.randint(1, 8)
        url = f"https://api.pexels.com/videos/search?query={keyword}&orientation=portrait&per_page=15&page={random_page}"
        
        try:
            res = requests.get(url, headers=headers, timeout=10).json()
            videos = res.get("videos", [])
            
            if not videos:
                continue
                
            random.shuffle(videos)
            
            for video_info in videos:
                video_files = video_info.get("video_files", [])
                valid_files = [f for f in video_files if f.get("file_type") == "video/mp4"]
                if not valid_files:
                    continue
                    
                valid_files.sort(key=lambda x: abs(x.get("height", 0) - 1080))
                video_url = valid_files[0]["link"]
                
                with requests.get(video_url, stream=True, timeout=15) as r:
                    r.raise_for_status()
                    with open(output_path, "wb") as f:
                        for chunk in r.iter_content(chunk_size=8192):
                            f.write(chunk)
                
                if is_valid_video(output_path):
                    return True
        except Exception as e:
            logging.error(f"Error fetching clip for '{keyword}' on attempt {attempt}: {e}")
    return False


def create_blank_video(output_path: str, duration: float):
    cmd = [
        "ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c=#101015:s=540x960:d={duration}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", output_path
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def create_modern_caption_overlay(text: str, temp_img_path: str = "temp_caption.png"):
    width, height = 540, 960
    img = PIL.Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = PIL.ImageDraw.Draw(img)

    # Use textwrap to neatly break longer paragraphs into lines of ~26 characters
    clean_text = re.sub(r'[.,!?;:]', '', text).upper()
    lines = textwrap.wrap(clean_text, width=26)

    # SMALLER FONT so it doesn't take up the whole screen
    try:
        font = PIL.ImageFont.truetype("arialbd.ttf", 32)
    except Exception:
        font = PIL.ImageFont.load_default()

    main_color = random.choice([(255, 255, 255, 255), (255, 223, 0, 255)])
    stroke_color = (0, 0, 0, 255)
    
    # Thinner stroke to match the smaller text
    stroke_width = 2
    line_height = 40 
    
    # Calculate total text height to anchor it nicely at the bottom-center
    total_text_h = len(lines) * line_height
    start_y = height - total_text_h - 180  

    curr_y = start_y
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        line_w = bbox[2] - bbox[0]
        text_x = (width - line_w) // 2

        for adj_x in range(-stroke_width, stroke_width + 1):
            for adj_y in range(-stroke_width, stroke_width + 1):
                draw.text((text_x + adj_x, curr_y + adj_y), line, font=font, fill=stroke_color)
                
        draw.text((text_x, curr_y), line, font=font, fill=main_color)
        curr_y += line_height

    img.save(temp_img_path)


def fast_ffmpeg_concat_and_mix(video_files: list[str], full_voice_path: str, output_filename: str):
    concat_list = "concat_list.txt"
    temp_concat_video = "temp_concat.mp4"

    with open(concat_list, "w") as f:
        for file in video_files:
            f.write(f"file '{os.path.abspath(file)}'\n")

    cmd_concat = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", concat_list, "-c", "copy", temp_concat_video
    ]
    subprocess.run(cmd_concat, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

    cmd_mix = [
        "ffmpeg", "-y", "-i", temp_concat_video, "-i", full_voice_path,
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", output_filename
    ]
    subprocess.run(cmd_mix, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)


async def assemble_pro_short(scenes: list[dict], output_filename: str = "final_short.mp4"):
    rendered_scene_files = []
    temp_files = []
    selected_voice = random.choice(FEMALE_VOICES)
    
    GIRL_KEYWORDS = [
        "aesthetic girl portrait", "beautiful woman looking at camera", 
        "girl walking cinematic", "pretty female model", "stylish girl smiling"
    ]

    # 1. GENERATE ONE SINGLE, CONTINUOUS AUDIO FILE FOR PERFECT PACING
    # We combine the full text with natural spacing so the AI breathes normally.
    full_script_text = " ".join([scene["text"] for scene in scenes])
    temp_full_voice_path = "temp_full_voice.mp3"
    temp_files.append(temp_full_voice_path)
    
    await generate_voiceover(full_script_text, temp_full_voice_path, voice_name=selected_voice)
    
    # Get exact length of the natural audio
    full_audio = AudioFileClip(temp_full_voice_path)
    total_audio_duration = full_audio.duration
    full_audio.close()
    
    # Calculate how many characters total to figure out proportional video clip times
    total_chars = sum(len(scene["text"]) for scene in scenes)
    if total_chars == 0: total_chars = 1

    for idx, scene in enumerate(scenes):
        scene_text = scene.get("text", "")
        keyword = random.choice(GIRL_KEYWORDS) if idx == 0 else scene.get("keyword", "abstract cinematic")
        
        raw_video_file = f"temp_raw_video_{idx}.mp4"
        caption_file = f"temp_caption_{idx}.png"
        rendered_scene_file = f"rendered_scene_{idx}.mp4"
        temp_files.extend([raw_video_file, caption_file, rendered_scene_file])

        # 2. CALCULATE CLIP DURATION PROPORTIONALLY
        # If this chunk is 20% of the total text, it gets 20% of the total video time.
        # This keeps the video swaps perfectly in sync with the one continuous audio track.
        char_ratio = len(scene_text) / total_chars
        duration = round(total_audio_duration * char_ratio, 3)
        if duration < 1.0: duration = 1.0 # Failsafe minimum

        # 3. Fetch Video
        success = download_pexels_clip(keyword, raw_video_file)
        if not success:
            success = download_pexels_clip("cinematic motion loop", raw_video_file)
        if not success or not is_valid_video(raw_video_file):
            create_blank_video(raw_video_file, duration)

        # 4. Generate Small, Paragraph-style Captions
        create_modern_caption_overlay(scene_text, caption_file)

        # 5. Composite Scene
        filter_complex = (
            f"[0:v]fps=30,scale=540:960:force_original_aspect_ratio=increase,crop=540:960,setpts=N/FRAME_RATE/TB,trim=duration={duration}[bg];"
            f"[bg][1:v]overlay=0:0[outv]"
        )
        
        cmd_scene = [
            "ffmpeg", "-y",
            "-stream_loop", "-1", "-i", raw_video_file,
            "-i", caption_file,
            "-filter_complex", filter_complex,
            "-map", "[outv]",
            "-t", str(duration),
            "-c:v", "libx264",
            "-r", "30",
            "-pix_fmt", "yuv420p",
            "-preset", "ultrafast",
            "-threads", "2",
            rendered_scene_file
        ]
        
        subprocess.run(cmd_scene, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        rendered_scene_files.append(rendered_scene_file)

    # 6. Final Mix (Concat perfectly timed video clips + the single natural voiceover)
    fast_ffmpeg_concat_and_mix(rendered_scene_files, temp_full_voice_path, output_filename)

    # Cleanup temporary files
    for f in temp_files:
        if os.path.exists(f):
            try: os.remove(f)
            except Exception: pass


async def delayed_backend_cleanup(filepath: str, delay_seconds: int = 300):
    await asyncio.sleep(delay_seconds)
    abs_filepath = os.path.abspath(filepath)
    if os.path.exists(abs_filepath):
        try: os.remove(abs_filepath)
        except Exception: pass

    stray_patterns = ["temp_*.mp3", "temp_*.mp4", "temp_*.png", "rendered_*.mp4", "short_*.mp4", "concat_list.txt"]
    for pattern in stray_patterns:
        for f in glob.glob(pattern):
            try: os.remove(os.path.abspath(f))
            except Exception: pass


async def build_video_pipeline(topic: str, custom_script: str = None) -> tuple[str, str]:
    if custom_script:
        scenes = parse_custom_script(custom_script, topic)
    else:
        scenes = generate_structured_script(topic)

    output_filename = f"short_{random.randint(1000, 9999)}.mp4"
    await assemble_pro_short(scenes, output_filename)
    
    social_text = generate_social_caption(topic if not custom_script else "Trending")
    return output_filename, social_text


# ==================== TELEGRAM BOT HANDLERS ====================

async def handle_generate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "⚠️ *Usage Options:*\n"
            "1. Auto-script: `/generate Quantum Computing`\n"
            "2. Custom script: `/generate Tech | Did you know quantum computers use qubits?`",
            parse_mode="Markdown"
        )
        return

    full_text = " ".join(context.args)
    if "|" in full_text:
        parts = full_text.split("|", 1)
        topic = parts[0].strip()
        custom_script = parts[1].strip()
    else:
        topic = full_text.strip()
        custom_script = None

    status_msg = await update.message.reply_text(f"⚡ *Rendering Ultra-Fast Short for:* `{topic}`...", parse_mode="Markdown")

    try:
        video_file, social_caption = await build_video_pipeline(topic, custom_script)
        with open(video_file, "rb") as vid:
            await update.message.reply_video(video=vid, caption=f"🎬 **{topic.title()}**\n\n{social_caption}")
        
        await status_msg.delete()
        asyncio.create_task(delayed_backend_cleanup(video_file, delay_seconds=300))
        
    except Exception as e:
        await update.message.reply_text(f"❌ Failed to build video: {str(e)}")


async def scheduled_daily_video(context: ContextTypes.DEFAULT_TYPE):
    topics = ["Space Exploration", "Artificial Intelligence", "Life Hacks", "Future Tech"]
    topic = random.choice(topics)
    chat_id = context.job.chat_id

    await context.bot.send_message(chat_id=chat_id, text=f"🌅 *Generating Daily Short...*\nTopic: {topic}", parse_mode="Markdown")

    try:
        video_file, social_caption = await build_video_pipeline(topic)
        with open(video_file, "rb") as vid:
            await context.bot.send_video(chat_id=chat_id, video=vid, caption=f"📅 **Daily Short:** {topic.title()}\n\n{social_caption}")
            
        asyncio.create_task(delayed_backend_cleanup(video_file, delay_seconds=300))
        
    except Exception as e:
        await context.bot.send_message(chat_id=chat_id, text=f"❌ Daily run failed: {str(e)}")


def main():
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("generate", handle_generate))

    if TELEGRAM_CHAT_ID:
        app.job_queue.run_repeating(
            scheduled_daily_video, interval=86400, first=10, chat_id=int(TELEGRAM_CHAT_ID)
        )

    print("🚀 Ultra-Fast Video Bot is live...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()