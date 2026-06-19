import httpx
import yt_dlp
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

# Enable CORS so your PWA (frontend) can talk to this backend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins for testing
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configuration for yt-dlp
YDL_OPTIONS = {
    'format': 'bestaudio/best',
    'noplaylist': True,
    'quiet': True,
    'no_warnings': True,
}

def get_yt_stream_url(video_id: str):
    video_url = f"https://www.youtube.com/watch?v={video_id}"
    with yt_dlp.YoutubeDL(YDL_OPTIONS) as ydl:
        try:
            info = ydl.extract_info(video_url, download=False)
            return info['url']
        except Exception as e:
            print(f"Extraction Error: {e}")
            return None

@app.get("/")
def home():
    return {"status": "Music Proxy API is running"}

@app.get("/api/stream-audio/{video_id}")
async def stream_audio(video_id: str, request: Request):
    # 1. Get the direct YouTube URL (IP-bound to the server)
    real_url = get_yt_stream_url(video_id)
    
    if not real_url:
        raise HTTPException(status_code=404, detail="Audio not found")

    # 2. Handle Range Headers (Essential for seeking/scrubbing in the PWA)
    range_header = request.headers.get("range")
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    }
    if range_header:
        headers["Range"] = range_header

    # 3. Generator to stream the bytes from YouTube to the Client
    async def iterate_yt_stream():
        async with httpx.AsyncClient() as client:
            try:
                async with client.stream("GET", real_url, headers=headers, follow_redirects=True) as r:
                    # Pass along the status code from YouTube (e.g., 206 Partial Content)
                    async for chunk in r.aiter_bytes(chunk_size=1024 * 64):
                        yield chunk
            except Exception as e:
                print(f"Streaming error: {e}")

    # 4. Return the stream
    return StreamingResponse(
        iterate_yt_stream(),
        media_type="audio/mpeg",
        headers={"Accept-Ranges": "bytes"}
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
