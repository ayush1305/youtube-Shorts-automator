import os
import uuid
import asyncio
import logging
import requests
import random
from typing import Dict, Any, Optional, List
from datetime import date
import json
from fastapi import FastAPI, HTTPException, status, Request, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.concurrency import run_in_threadpool
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field

# Google API & YouTube Client imports
from google_auth_oauthlib.flow import Flow
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# Import configuration settings, logger, and generator
from config import settings, logger, BASE_DIR
import youtube_generator

app = FastAPI(
    title="YouTube Shorts Automation Pipeline API",
    description="Backend pipeline for generating vertical shorts with styled center captions, AI scoring, and trends analysis.",
    version="2.0.0"
)

# Enable CORS for frontend interface
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve output directory statically so the frontend can preview generated videos directly
app.mount("/output", StaticFiles(directory=settings.OUTPUT_DIR), name="output")

from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi import Depends

security = HTTPBasic()

def get_current_user(credentials: HTTPBasicCredentials = Depends(security)):
    auth_username = os.environ.get("BASIC_AUTH_USERNAME", "admin")
    auth_password = os.environ.get("BASIC_AUTH_PASSWORD", "S@lary9321")
    if credentials.username != auth_username or credentials.password != auth_password:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username

CLIENT_SECRETS_FILE = os.path.join(BASE_DIR, "client_secrets.json")
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
oauth_verifiers = {}



# =====================================================================
# Request / Response Models
# =====================================================================

class TTSRequest(BaseModel):
    text: str = Field(..., min_length=1, description="Script to narrate.")
    voice: str = Field("en-US-GuyNeural", description="Microsoft Edge TTS voice model.")

class DownloadRequest(BaseModel):
    video_url: str = Field(..., description="Direct video URL to download.")

class VideoRequest(BaseModel):
    video_path: str = Field(..., description="Local path to input video.")
    audio_path: str = Field(..., description="Local path to TTS audio.")
    subtitles_path: Optional[str] = Field(None, description="Local path to styled ASS subtitles.")

class PipelineRequest(BaseModel):
    category: str = Field("tech", description="Category: tech, history, how_why")
    prompt: str = Field("", description="Optional custom topic keywords")

class KeysRequest(BaseModel):
    gemini_key: str = Field("", description="Gemini API Key")
    pexels_key: str = Field("", description="Pexels API Key")

class PublishRequest(BaseModel):
    video_path: str = Field(..., description="Local video path to upload")
    title: str = Field(..., description="Video title")
    description: str = Field(..., description="Video description")
    tags: List[str] = Field(default=[], description="Video keywords/tags")
    privacy_status: str = Field("private", description="Video privacy: private, public, unlisted")


# =====================================================================
# Helper Utilities
# =====================================================================

def resolve_and_verify_path(path_str: str) -> str:
    """Resolve relative path to BASE_DIR and verify disk existence."""
    if os.path.isabs(path_str):
        resolved = os.path.abspath(path_str)
    else:
        resolved = os.path.abspath(os.path.join(BASE_DIR, path_str))
    
    if not os.path.exists(resolved):
        logger.error(f"File not found: {resolved}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Resource not found: {path_str}"
        )
    return resolved

def to_posix_relative_path(abs_path: str) -> str:
    """Convert absolute path to base-relative POSIX format (uses forward slashes)."""
    rel = os.path.relpath(abs_path, BASE_DIR)
    return rel.replace("\\", "/")

def upload_to_youtube_sync(credentials, video_path: str, title: str, description: str, tags: list, privacy_status: str) -> str:
    """Execute resumable media upload using the YouTube API."""
    youtube = build('youtube', 'v3', credentials=credentials)
    
    # Clip title to max 100 characters (YouTube limit)
    safe_title = title[:100]
    
    body = {
        'snippet': {
            'title': safe_title,
            'description': description,
            'tags': tags,
            'categoryId': '28' # Science & Technology
        },
        'status': {
            'privacyStatus': privacy_status,
            'selfDeclaredMadeForKids': False
        }
    }
    
    media = MediaFileUpload(video_path, chunksize=1024*1024, resumable=True)
    request = youtube.videos().insert(
        part=','.join(body.keys()),
        body=body,
        media_body=media
    )
    
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            logger.info(f"YouTube Upload Progress: {int(status.progress() * 100)}%")
            
    logger.info(f"YouTube Upload complete! Video ID: {response.get('id')}")
    return response.get('id')


def download_file_sync(url: str, dest_path: str) -> None:
    """Download video asset from URL."""
    logger.info(f"Downloading from URL: {url} to {dest_path}")
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    try:
        response = requests.get(url, headers=headers, stream=True, timeout=60)
        response.raise_for_status()
        with open(dest_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=16384):
                if chunk:
                    f.write(chunk)
        logger.info(f"Download complete: {dest_path}")
    except requests.RequestException as e:
        logger.error(f"Download failed: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to download asset: {str(e)}"
        )

async def get_audio_duration_ffprobe(audio_path: str) -> float:
    """Run ffprobe to query exact audio duration."""
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        audio_path
    ]
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            raise Exception(stderr.decode().strip())
        return float(stdout.decode().strip())
    except Exception as e:
        logger.error(f"ffprobe duration check failed: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to query audio duration: {str(e)}"
        )

def increment_usage() -> int:
    """Increment and return the current day's video generation count."""
    import json
    today_str = str(date.today())
    tracker_path = os.path.join(settings.TEMP_DIR, "usage_tracker.json")
    
    count = 0
    if os.path.exists(tracker_path):
        try:
            with open(tracker_path, "r") as f:
                data = json.load(f)
                if data.get("date") == today_str:
                    count = data.get("count", 0)
        except Exception:
            pass
            
    count += 1
    try:
        with open(tracker_path, "w") as f:
            json.dump({"date": today_str, "count": count}, f)
    except Exception as e:
        logger.error(f"Failed to update daily usage logs: {str(e)}")
    return count

def get_usage() -> int:
    """Get the current day's video generation count."""
    import json
    today_str = str(date.today())
    tracker_path = os.path.join(settings.TEMP_DIR, "usage_tracker.json")
    
    if os.path.exists(tracker_path):
        try:
            with open(tracker_path, "r") as f:
                data = json.load(f)
                if data.get("date") == today_str:
                    return data.get("count", 0)
        except Exception:
            pass
    return 0

async def synthesize_tts_with_fallback(text: str, voice: str, audio_path: str, ass_path: str) -> None:
    """Synthesize speech using edge-tts and compile ASS subtitles with sentence fallback."""
    import edge_tts
    communicate = edge_tts.Communicate(text, voice)
    
    word_boundaries = []
    sentence_boundaries = []
    
    with open(audio_path, "wb") as audio_file:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_file.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                offset_sec = chunk["offset"] / 10000000.0
                duration_sec = chunk["duration"] / 10000000.0
                word_boundaries.append({
                    "word": chunk["text"],
                    "start": offset_sec,
                    "end": offset_sec + duration_sec
                })
            elif chunk["type"] == "SentenceBoundary":
                offset_sec = chunk["offset"] / 10000000.0
                duration_sec = chunk["duration"] / 10000000.0
                sentence_boundaries.append({
                    "text": chunk["text"],
                    "start": offset_sec,
                    "end": offset_sec + duration_sec
                })

    # Fallback interpolation if word boundaries are not returned by the API
    if not word_boundaries and sentence_boundaries:
        logger.info("Word boundaries missing. Interpolating from sentence boundaries.")
        for sb in sentence_boundaries:
            words = sb["text"].split()
            if not words:
                continue
            duration = sb["end"] - sb["start"]
            word_dur = duration / len(words)
            for i, word in enumerate(words):
                start = sb["start"] + i * word_dur
                end = start + word_dur
                word_boundaries.append({
                    "word": word,
                    "start": start,
                    "end": end
                })

    # If both are empty (extremely rare/offline), generate placeholder boundaries from word spacing heuristics
    if not word_boundaries:
        logger.warning("No boundaries returned by edge-tts. Synthesizing timeline heuristics.")
        # Estimate average reading speed: 130 words per minute (approx 0.46 seconds per word)
        words = text.split()
        for i, word in enumerate(words):
            start = i * 0.46
            end = start + 0.46
            word_boundaries.append({
                "word": word,
                "start": start,
                "end": end
            })

    # Write styled ASS subtitle file
    youtube_generator.generate_ass_file(word_boundaries, ass_path)

# =====================================================================
# API Endpoints
# =====================================================================

@app.get("/", response_class=HTMLResponse)
async def serve_dashboard(username: str = Depends(get_current_user)):
    """Serve the interactive dashboard UI at the root path."""
    dashboard_path = os.path.join(BASE_DIR, "dashboard.html")
    if os.path.exists(dashboard_path):
        with open(dashboard_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read(), status_code=200)
    return HTMLResponse(content="<h1>Dashboard file not found.</h1>", status_code=404)

@app.get("/usage", status_code=status.HTTP_200_OK)
async def get_daily_usage() -> Dict[str, int]:
    return {"count": get_usage()}

@app.get("/auth/youtube")
async def auth_youtube(request: Request):
    """Initiate YouTube OAuth2 flow."""
    if not os.path.exists(CLIENT_SECRETS_FILE):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="client_secrets.json is missing in the project folder. Please download it from Google Cloud Console."
        )
    
    base_url = str(request.base_url).rstrip("/")
    flow = Flow.from_client_secrets_file(
        CLIENT_SECRETS_FILE,
        scopes=SCOPES,
        redirect_uri=f"{base_url}/oauth2callback"
    )
    
    authorization_url, state = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true',
        prompt='consent'
    )
    oauth_verifiers[state] = flow.code_verifier
    
    return RedirectResponse(authorization_url)

@app.get("/oauth2callback")
async def oauth2callback(request: Request):
    """Handle the OAuth2 callback from Google."""
    if not os.path.exists(CLIENT_SECRETS_FILE):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="client_secrets.json is missing."
        )
        
    state = request.query_params.get("state")
    code_verifier = oauth_verifiers.pop(state, None)
    
    base_url = str(request.base_url).rstrip("/")
    flow = Flow.from_client_secrets_file(
        CLIENT_SECRETS_FILE,
        scopes=SCOPES,
        redirect_uri=f"{base_url}/oauth2callback",
        code_verifier=code_verifier
    )
    
    authorization_response = str(request.url)
    
    try:
        os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
        os.environ['OAUTHLIB_RELAX_TOKEN_SCOPE'] = '1'
        flow.fetch_token(authorization_response=authorization_response)
        
        credentials = flow.credentials
        token_path = os.path.join(BASE_DIR, "token.json")
        with open(token_path, "w") as token_file:
            token_file.write(credentials.to_json())
            
        return HTMLResponse(content="""
            <html>
                <head>
                    <title>Authentication Successful</title>
                    <style>
                        body { font-family: sans-serif; display: flex; flex-direction: column; align-items: center; justify-content: center; height: 100vh; background-color: #f3f4f6; color: #1f2937; margin: 0; }
                        .card { background: white; padding: 2.5rem; border-radius: 12px; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1); text-align: center; }
                        h1 { color: #10B981; margin-top: 0; }
                        button { background: #3B82F6; color: white; border: none; padding: 10px 20px; border-radius: 6px; font-weight: bold; cursor: pointer; margin-top: 15px; }
                    </style>
                </head>
                <body>
                    <div class="card">
                        <h1>YouTube Connected Successfully!</h1>
                        <p>Your channel has been linked. You can close this tab and return to the dashboard.</p>
                        <button onclick="window.close()">Close Window</button>
                    </div>
                </body>
            </html>
        """)
    except Exception as e:
        logger.exception("OAuth exchange failed")
        return HTMLResponse(content=f"""
            <html>
                <body>
                    <h1 style='color:red;'>Authentication Failed</h1>
                    <p>{str(e)}</p>
                </body>
            </html>
        """, status_code=500)

@app.get("/youtube/status")
async def youtube_status():
    """Get YouTube authentication status."""
    token_path = os.path.join(BASE_DIR, "token.json")
    if not os.path.exists(token_path):
        return {"connected": False}
        
    try:
        with open(token_path, "r") as token_file:
            creds_data = json.load(token_file)
        
        credentials = Credentials.from_authorized_user_info(creds_data, SCOPES)
        
        if credentials.expired and credentials.refresh_token:
            credentials.refresh(GoogleRequest())
            with open(token_path, "w") as token_file:
                token_file.write(credentials.to_json())
                
        return {"connected": True}
    except Exception as e:
        logger.error(f"Error reading credentials status: {e}")
        return {"connected": False}

@app.post("/publish")
async def publish_video(request: PublishRequest):
    """Upload a video directly to the connected YouTube channel."""
    token_path = os.path.join(BASE_DIR, "token.json")
    if not os.path.exists(token_path):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="YouTube channel is not connected. Please authenticate first."
        )
        
    filename = os.path.basename(request.video_path)
    abs_video_path = os.path.abspath(os.path.join(settings.OUTPUT_DIR, filename))
        
    if not os.path.exists(abs_video_path):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Video file not found at: {request.video_path} (Resolved: {abs_video_path})"
        )

        
    try:
        with open(token_path, "r") as token_file:
            creds_data = json.load(token_file)
        
        credentials = Credentials.from_authorized_user_info(creds_data, SCOPES)
        if credentials.expired and credentials.refresh_token:
            credentials.refresh(GoogleRequest())
            with open(token_path, "w") as token_file:
                token_file.write(credentials.to_json())
    except Exception as e:
        logger.exception("Failed to load or refresh credentials")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Failed to load credentials: {str(e)}"
        )
        
    try:
        video_id = await run_in_threadpool(
            upload_to_youtube_sync,
            credentials,
            abs_video_path,
            request.title,
            request.description,
            request.tags,
            request.privacy_status
        )
        return {
            "success": True,
            "video_id": video_id,
            "message": "Video successfully uploaded to YouTube!"
        }
    except Exception as e:
        logger.exception("YouTube upload failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"YouTube upload failed: {str(e)}"
        )


@app.get("/settings/keys", status_code=status.HTTP_200_OK)
async def get_keys():
    """Retrieve keys configuration (obfuscated)."""
    keys = settings.get_keys()
    
    def mask(key):
        return f"{key[:4]}...{key[-4:]}" if len(key) > 8 else "Configured" if key else ""
        
    return {
        "gemini_configured": bool(keys.get("gemini_key")),
        "pexels_configured": bool(keys.get("pexels_key")),
        "gemini_mask": mask(keys.get("gemini_key")),
        "pexels_mask": mask(keys.get("pexels_key"))
    }

@app.post("/settings/keys", status_code=status.HTTP_200_OK)
async def save_keys(request: KeysRequest):
    """Save API keys configuration."""
    try:
        settings.save_keys(request.gemini_key, request.pexels_key)
        return {"success": True, "message": "API keys saved successfully."}
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to save keys: {str(e)}"
        )

@app.get("/trending", status_code=status.HTTP_200_OK)
async def get_trends(category: str = "tech"):
    """Suggest trending topics to post today."""
    if category not in ["tech", "history", "how_why"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid category. Choose from: tech, history, how_why"
        )
    recommendations = youtube_generator.get_what_to_post_today(category)
    return recommendations

@app.post("/tts", status_code=status.HTTP_200_OK)
async def generate_tts(request: TTSRequest) -> Dict[str, Any]:
    """Synthesize narration audio and generate aligned ASS subtitles."""
    import edge_tts
    dest_filename = f"voice_{uuid.uuid4().hex[:8]}.mp3"
    dest_path = os.path.join(settings.OUTPUT_DIR, dest_filename)
    
    ass_filename = f"subtitles_{uuid.uuid4().hex[:8]}.ass"
    ass_path = os.path.join(settings.TEMP_DIR, ass_filename)
    
    logger.info(f"Synthesizing voice (len: {len(request.text)}) using {request.voice}")
    
    try:
        await synthesize_tts_with_fallback(request.text, request.voice, dest_path, ass_path)
        
        relative_audio = to_posix_relative_path(dest_path)
        relative_subtitles = to_posix_relative_path(ass_path)
        
        return {
            "success": True,
            "audio_path": relative_audio,
            "subtitles_path": relative_subtitles
        }
    except Exception as e:
        logger.exception("Error synthesizing edge-tts audio / boundaries")
        if os.path.exists(dest_path):
            os.remove(dest_path)
        if os.path.exists(ass_path):
            os.remove(ass_path)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"TTS synthesis failed: {str(e)}"
        )

@app.post("/download", status_code=status.HTTP_200_OK)
async def download_video(request: DownloadRequest) -> Dict[str, Any]:
    filename = f"pexels_{uuid.uuid4().hex[:12]}.mp4"
    dest_path = os.path.join(settings.TEMP_DIR, filename)
    await run_in_threadpool(download_file_sync, request.video_url, dest_path)
    return {
        "success": True,
        "video_path": to_posix_relative_path(dest_path)
    }

@app.post("/video", status_code=status.HTTP_200_OK)
async def process_video(request: VideoRequest) -> Dict[str, Any]:
    """Combine background video, narration audio, and burn styled subtitles."""
    resolved_video = resolve_and_verify_path(request.video_path)
    resolved_audio = resolve_and_verify_path(request.audio_path)
    
    # Check subtitles path
    resolved_subtitles = None
    if request.subtitles_path:
        resolved_subtitles = resolve_and_verify_path(request.subtitles_path)
        
    output_filename = f"final_{uuid.uuid4().hex[:8]}.mp4"
    resolved_output = os.path.join(settings.OUTPUT_DIR, output_filename)
    
    duration = await get_audio_duration_ffprobe(resolved_audio)
    
    # Setup ffmpeg scale, crop and subtitle overlay filters
    # Convert windows path backslashes to forward slashes for FFmpeg subtitles filter compatibility
    if resolved_subtitles:
        sub_path_ffmpeg = to_posix_relative_path(resolved_subtitles)
        video_filter = f"[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,subtitles={sub_path_ffmpeg}[v]"
    else:
        video_filter = "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920[v]"
        
    cmd = [
        "ffmpeg",
        "-y",
        "-stream_loop", "-1",            # Loop input video indefinitely
        "-i", resolved_video,
        "-i", resolved_audio,
        "-filter_complex", video_filter,
        "-map", "[v]",                    # Map scale/cropped video (with subtitles burned in)
        "-map", "1:a",                    # Map narration audio
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "22",
        "-c:a", "aac",
        "-b:a", "192k",
        "-t", f"{duration:.3f}",          # Limit to exact audio length
        resolved_output
    ]
    
    logger.info(f"Executing FFmpeg: {' '.join(cmd)}")
    
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        
        if process.returncode != 0:
            err = stderr.decode().strip()
            logger.error(f"FFmpeg error: {err}")
            raise Exception(f"FFmpeg failed (code {process.returncode}): {err}")
            
        return {
            "success": True,
            "video_path": to_posix_relative_path(resolved_output)
        }
    except Exception as e:
        logger.exception("Error rendering video")
        if os.path.exists(resolved_output):
            os.remove(resolved_output)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"FFmpeg rendering failed: {str(e)}"
        )

tasks_db = {}

async def run_pipeline_task(task_id: str, category: str, prompt: str):
    """Executes the full pipeline in a background task to prevent request timeouts."""
    tasks_db[task_id]["logs"].append("Initiating background rendering...")
    tasks_db[task_id]["progress"] = 15
    
    local_video_path = None
    resolved_output = None
    
    try:
        # Get API keys
        keys = settings.get_keys()
        gemini_key = keys.get("gemini_key")
        pexels_key = keys.get("pexels_key")
        
        # 1. Script Generation
        tasks_db[task_id]["logs"].append("Generating script & querying SEO tags via Gemini...")
        tasks_db[task_id]["progress"] = 30
        metadata = youtube_generator.generate_script_and_metadata(category, prompt)
        script_text = metadata.get("script", "")
        keywords = metadata.get("search_keywords", category)
        
        if not script_text:
            raise Exception("Generated script text is empty.")
            
        # 2. Search Pexels video
        tasks_db[task_id]["logs"].append("Searching Pexels for matching stock footage...")
        tasks_db[task_id]["progress"] = 45
        video_url = youtube_generator.search_pexels_video(keywords, pexels_key)
        if not video_url:
            fallback_urls = settings.FALLBACK_VIDEOS.get(category, settings.FALLBACK_VIDEOS["tech"])
            video_url = random.choice(fallback_urls)
            tasks_db[task_id]["logs"].append("No specific footage found. Using backup stock clip...")
            
        # Download video
        tasks_db[task_id]["logs"].append("Downloading stock video footage...")
        temp_video_name = f"bg_{uuid.uuid4().hex[:12]}.mp4"
        local_video_path = os.path.join(settings.TEMP_DIR, temp_video_name)
        try:
            await run_in_threadpool(download_file_sync, video_url, local_video_path)
        except Exception as e:
            backup_url = settings.FALLBACK_VIDEOS["tech"][0]
            await run_in_threadpool(download_file_sync, backup_url, local_video_path)
            
        # 3. Speech synthesis & Subtitle alignment
        tasks_db[task_id]["logs"].append("Synthesizing voiceover narration & aligning subtitles...")
        tasks_db[task_id]["progress"] = 60
        voice_name = "en-US-GuyNeural"
        dest_voice_name = f"voice_{uuid.uuid4().hex[:8]}.mp3"
        local_voice_path = os.path.join(settings.OUTPUT_DIR, dest_voice_name)
        
        ass_filename = f"subtitles_{uuid.uuid4().hex[:8]}.ass"
        local_ass_path = os.path.join(settings.TEMP_DIR, ass_filename)
        
        await synthesize_tts_with_fallback(script_text, voice_name, local_voice_path, local_ass_path)
        
        # 4. Composite rendering (FFmpeg)
        tasks_db[task_id]["logs"].append("Compiling vertical video composite (FFmpeg rendering)...")
        tasks_db[task_id]["progress"] = 80
        output_filename = f"shorts_{uuid.uuid4().hex[:8]}.mp4"
        resolved_output = os.path.join(settings.OUTPUT_DIR, output_filename)
        duration = await get_audio_duration_ffprobe(local_voice_path)
        
        sub_path_ffmpeg = to_posix_relative_path(local_ass_path)
        video_filter = f"[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,subtitles={sub_path_ffmpeg}[v]"
        
        ffmpeg_cmd = [
            "ffmpeg",
            "-y",
            "-threads", "1",
            "-stream_loop", "-1",
            "-i", local_video_path,
            "-i", local_voice_path,
            "-filter_complex", video_filter,
            "-map", "[v]",
            "-map", "1:a",
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-crf", "24",
            "-c:a", "aac",
            "-b:a", "192k",
            "-t", f"{duration:.3f}",
            resolved_output
        ]
        
        process = await asyncio.create_subprocess_exec(
            *ffmpeg_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        
        if process.returncode != 0:
            err = stderr.decode().strip()
            raise Exception(f"FFmpeg rendering crashed: {err}")
            
        # Clean up temporary background raw video clip
        if local_video_path and os.path.exists(local_video_path):
            try:
                os.remove(local_video_path)
            except Exception:
                pass
                
        # 5. Evaluate and score video
        tasks_db[task_id]["logs"].append("Analyzing performance metrics & scoring outline...")
        tasks_db[task_id]["progress"] = 95
        scores = youtube_generator.score_video_metadata(metadata)
        
        # 6. Increment daily usage
        new_usage_count = increment_usage()
        
        # Save results
        relative_output_video = to_posix_relative_path(resolved_output)
        
        tasks_db[task_id]["status"] = "completed"
        tasks_db[task_id]["progress"] = 100
        tasks_db[task_id]["logs"].append("Pipeline execution completed successfully!")
        
        tasks_db[task_id]["result"] = {
            "success": True,
            "video_url": f"/{relative_output_video}",
            "metadata": {
                "title": metadata.get("title", "Untitled Short"),
                "description": metadata.get("description", ""),
                "tags": metadata.get("tags", []),
                "script": script_text,
                "search_keywords": keywords
            },
            "scores": scores,
            "usage_today": new_usage_count
        }
    except Exception as e:
        logger.exception(f"Background task failed: {str(e)}")
        tasks_db[task_id]["status"] = "failed"
        tasks_db[task_id]["logs"].append(f"Pipeline crashed: {str(e)}")
        if resolved_output and os.path.exists(resolved_output):
            try:
                os.remove(resolved_output)
            except Exception:
                pass
        if local_video_path and os.path.exists(local_video_path):
            try:
                os.remove(local_video_path)
            except Exception:
                pass

@app.post("/generate_pipeline", status_code=status.HTTP_200_OK)
async def generate_pipeline(request: PipelineRequest, background_tasks: BackgroundTasks):
    """Start the video automation pipeline in a background task to prevent Render timeouts."""
    current_count = get_usage()
    if current_count >= 50:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Daily limit reached. You can only generate 50 videos per day."
        )
        
    task_id = str(uuid.uuid4())
    tasks_db[task_id] = {
        "status": "processing",
        "progress": 10,
        "logs": ["Task registered. Starting background rendering pipeline..."]
    }
    
    background_tasks.add_task(run_pipeline_task, task_id, request.category, request.prompt)
    
    return {"task_id": task_id}

@app.get("/tasks/{task_id}")
async def get_task_status(task_id: str):
    """Retrieve status, logs, progress, and results of a background generation task."""
    if task_id not in tasks_db:
        raise HTTPException(status_code=404, detail="Task not found")
    return tasks_db[task_id]

# =====================================================================
# Main Application Starter
# =====================================================================

if __name__ == "__main__":
    import uvicorn
    logger.info(f"Starting pipeline on {settings.HOST}:{settings.PORT}")
    uvicorn.run("app:app", host=settings.HOST, port=settings.PORT, reload=False)
