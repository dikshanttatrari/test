import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
import yt_dlp

app = FastAPI()

# 1. Allow your PWA to access this API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # In production, replace with your PWA URL
    allow_methods=["*"],
    allow_headers=["*"],
)

YDL_OPTIONS = {
    'format': 'bestaudio/best',
    'noplaylist': True,
    'quiet': True,
}

def get_yt_stream_url(video_id: str):
    video_url = f"https://www.youtube.com/watch?v={video_id}"
    with yt_dlp.YoutubeDL(YDL_OPTIONS) as ydl:
        try:
            info = ydl.extract_info(video_url, download=False)
            return info['url']
        except Exception as e:
            print(f"Error: {e}")
            return None

@app.get("/api/stream-audio/{video_id}")
async def stream_audio(video_id: str):
    real_url = get_yt_stream_url(video_id)
    if not real_url:
        raise HTTPException(status_code=404, detail="Song not found")

    async def iterate_yt_stream():
        async with httpx.AsyncClient() as client:
            # Important: We mimic a browser header to avoid being blocked
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
            async with client.stream("GET", real_url, headers=headers) as r:
                async for chunk in r.aiter_bytes(chunk_size=1024 * 64):
                    yield chunk

    return StreamingResponse(iterate_yt_stream(), media_type="audio/mpeg")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
