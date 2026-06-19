import os
import hashlib
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from Crypto.Cipher import Blowfish, AES
from dotenv import load_dotenv

# Load local .env file for local testing
load_dotenv()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- CONFIGURATION (Hidden) ---
# This will fetch from Railway Secrets or your local .env file
ARL_COOKIE = os.environ.get("ARL_COOKIE")
SECRET_KEY = b"jo6a16n6gu5p096e"
PRIVATE_API = "https://www.deezer.com/ajax/gw-light.php"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

if not ARL_COOKIE:
    print("❌ ERROR: ARL_COOKIE environment variable is NOT SET!")

# --- SESSION HELPERS ---

async def get_user_token(client: httpx.AsyncClient):
    """Fetches checkForm token with error reporting"""
    res = await client.post(
        PRIVATE_API,
        params={"method": "deezer.getUserData", "input": "3", "api_version": "1.0"},
    )
    data = res.json()
    results = data.get("results")
    if results and "checkForm" in results:
        return results["checkForm"]
    
    print(f"DEBUG: Deezer rejected request. Check ARL or IP. Response: {data}")
    return None

# --- CRYPTO HELPERS ---

def derive_blowfish_key(track_id: str):
    m = hashlib.md5()
    m.update(track_id.encode())
    track_id_hash = m.hexdigest().encode()
    key = b""
    for i in range(16):
        val = track_id_hash[i] ^ track_id_hash[i + 16] ^ SECRET_KEY[i]
        key += bytes([val])
    return key

def generate_download_url(track_info: dict):
    format_id = "1" # MP3_128
    hash_input = "¤".join([track_info["MD5_ORIGIN"], format_id, track_info["SNG_ID"], track_info["MEDIA_VERSION"]])
    m = hashlib.md5()
    m.update(hash_input.encode("utf-8"))
    hash_result = m.hexdigest()
    url_part = "¤".join([hash_result, hash_input, ""])
    while len(url_part) % 16 != 0:
        url_part += " "
    cipher = AES.new(b"jo6aey6haid2Teih", AES.MODE_ECB)
    encrypted_hex = cipher.encrypt(url_part.encode("utf-8")).hex()
    return f"https://e-cdns-proxy-{track_info['MD5_ORIGIN'][0]}.dzcdn.net/mobile/1/{encrypted_hex}"

# --- ENDPOINTS ---

@app.get("/")
def home():
    return {"status": "Deezer Proxy Online"}

@app.get("/api/search")
async def search(query: str):
    async with httpx.AsyncClient(cookies={"arl": ARL_COOKIE}, headers=HEADERS) as client:
        api_token = await get_user_token(client)
        if not api_token:
            raise HTTPException(status_code=401, detail="Deezer session rejected.")

        res = await client.post(
            PRIVATE_API,
            params={"method": "search.getSongs", "input": "3", "api_version": "1.0", "api_token": api_token},
            json={"QUERY": query, "NB": 20}
        )
        data = res.json()
        tracks = data.get("results", {}).get("data", [])
        
        return {"data": [{
            "id": t.get("SNG_ID"),
            "title": t.get("SNG_TITLE"),
            "artist": t.get("ART_NAME"),
            "image": f"https://e-cdns-images.dzcdn.net/images/cover/{t.get('ALB_PICTURE')}/250x250.jpg"
        } for t in tracks if t.get("SNG_ID")]}

@app.get("/api/stream-deezer/{track_id}")
async def stream_deezer(track_id: str):
    async with httpx.AsyncClient(cookies={"arl": ARL_COOKIE}, headers=HEADERS) as client:
        try:
            api_token = await get_user_token(client)
            if not api_token:
                raise HTTPException(status_code=401, detail="Session expired")

            res = await client.post(
                PRIVATE_API,
                params={"method": "song.getData", "input": "3", "api_version": "1.0", "api_token": api_token},
                json={"sng_id": track_id},
            )
            track_info = res.json().get("results")
            
            cdn_url = generate_download_url(track_info)
            bf_key = derive_blowfish_key(track_id)
            cipher = Blowfish.new(bf_key, Blowfish.MODE_ECB)

            async def iterate_and_decrypt():
                async with client.stream("GET", cdn_url) as r:
                    chunk_index = 0
                    async for chunk in r.aiter_bytes(chunk_size=2048):
                        if chunk_index % 3 == 0 and len(chunk) == 2048:
                            yield cipher.decrypt(chunk)
                        else:
                            yield chunk
                        chunk_index += 1
            return StreamingResponse(iterate_and_decrypt(), media_type="audio/mpeg")
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    # Railway sets the PORT environment variable automatically
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
