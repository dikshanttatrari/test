import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from innertube import InnerTube

app = FastAPI()

# CORS Setup
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize InnerTube with the ANDROID_MUSIC client
# This client is much harder for YouTube to block than "Web"
it = InnerTube("ANDROID_MUSIC")

def get_yt_stream_url(video_id: str):
    try:
        # Call the internal 'player' endpoint
        data = it.player(video_id)
        
        # Check if we got a valid response
        if 'streamingData' not in data:
            print("No streaming data found. Might be restricted.")
            return None
            
        # Get all audio formats
        formats = data['streamingData'].get('adaptiveFormats', [])
        
        # Filter for the best audio-only format (usually opus or m4a)
        audio_formats = [f for f in formats if f['mimeType'].startswith('audio/')]
        
        if not audio_formats:
            return None
            
        # Sort by bitrate to get the highest quality
        best_format = sorted(audio_formats, key=lambda f: f.get('averageBitrate', 0), reverse=True)[0]
        
        return best_format['url']
    except Exception as e:
        print(f"InnerTube Error: {e}")
        return None

@app.get("/api/stream-audio/{video_id}")
async def stream_audio(video_id: str, request: Request):
    # 1. Get the URL using InnerTube API
    real_url = get_yt_stream_url(video_id)
    
    if not real_url:
        raise HTTPException(status_code=404, detail="Song not found or blocked")

    # 2. Proxy the request
    # We pass along the Range header so the user can seek/scrub through the song
    range_header = request.headers.get("range")
    proxy_headers = {
        "User-Agent": "com.google.android.apps.youtube.music/6.41.54 (Linux; U; Android 14; en_US; Pixel 7)",
        "Range": range_header if range_header else "bytes=0-"
    }

    async def iterate_yt_stream():
        async with httpx.AsyncClient() as client:
            try:
                # Follow_redirects is important because YT CDN URLs often redirect
                async with client.stream("GET", real_url, headers=proxy_headers, follow_redirects=True) as r:
                    async for chunk in r.aiter_bytes(chunk_size=1024 * 64):
                        yield chunk
            except Exception as e:
                print(f"Proxy Error: {e}")

    return StreamingResponse(
        iterate_yt_stream(), 
        media_type="audio/mpeg",
        headers={"Accept-Ranges": "bytes"}
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
