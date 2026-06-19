import os
import hashlib
import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from Crypto.Cipher import Blowfish, AES
from dotenv import load_dotenv

# Load local .env file if it exists
load_dotenv()

app = FastAPI()

# Enable CORS for your PWA frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- CONFIGURATION ---
# Fetches from Railway Variables or .env file
ARL_COOKIE = os.environ.get("ARL_COOKIE")
SECRET_KEY = b"jo6a16n6gu5p096e"
PRIVATE_API = "https://www.deezer.com/ajax/gw-light.php"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

if not ARL_COOKIE:
    print("⚠️ WARNING: ARL_COOKIE is not set. API will fail.")

# --- CRYPTO HELPERS ---

def derive_bf_key(track_id: str):
    """Derive the Blowfish key for a specific track"""
    m = hashlib.md5()
    m.update(track_id.encode())
    track_id_hash = m.hexdigest().encode()
    key = b""
    for i in range(16):
        val = track_id_hash[i] ^ track_id_hash[i + 16] ^ SECRET_KEY[i]
        key += bytes([val])
    return key

def generate_cdn_url(track_info: dict):
    """Generate the encrypted CDN download URL"""
    # format 1 = MP3_128kbps
    hash_input = "¤".join([track_info["MD5_ORIGIN"], "1", track_info["SNG_ID"], track_info["MEDIA_VERSION"]])
    m = hashlib.md5()
    m.update(hash_input.encode("utf-8"))
    hash_result = m.hexdigest()
    
    url_part = "¤".join([hash_result, hash_input, ""])
    while len(url_part) % 16 != 0:
        url_part += " "
        
    cipher = AES.new(b"jo6aey6haid2Teih", AES.MODE_ECB)
    encrypted_hex = cipher.encrypt(url_part.encode("utf-8")).hex()
    return f"https://e-cdns-proxy-{track_info['MD5_ORIGIN'][0]}.dzcdn.net/mobile/1/{encrypted_hex}"

async def get_api_token(client: httpx.AsyncClient):
    """Get the required 'checkForm' token from Deezer"""
    res = await client.post(
        PRIVATE_API,
        params={"method": "deezer.getUserData", "input": "3", "api_version": "1.0"},
    )
    data = res.json()
    if "results" in data and "checkForm" in data["results"]:
        return data["results"]["checkForm"]
    raise HTTPException(status_code=401, detail="Deezer session invalid. Check ARL.")

# --- ENDPOINTS ---

@app.get("/")
def health_check():
    return {"status": "Proxy is online"}

@app.get("/api/search")
async def search(query: str):
    async with httpx.AsyncClient(cookies={"arl": ARL_COOKIE}, headers=HEADERS) as client:
        try:
            token = await get_api_token(client)
            res = await client.post(
                PRIVATE_API,
                params={"method": "search.getSongs", "input": "3", "api_version": "1.0", "api_token": token},
                json={"QUERY": query, "NB": 25}
            )
            data = res.json()
            tracks = data.get("results", {}).get("data", [])
            
            results = []
            for t in tracks:
                if "SNG_ID" in t:
                    results.append({
                        "id": t.get("SNG_ID"),
                        "title": t.get("SNG_TITLE"),
                        "artist": t.get("ART_NAME"),
                        "album": t.get("ALB_TITLE"),
                        "image": f"https://e-cdns-images.dzcdn.net/images/cover/{t.get('ALB_PICTURE')}/250x250.jpg"
                    })
            return {"data": results}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/stream-deezer/{track_id}")
async def stream_deezer(track_id: str):
    async with httpx.AsyncClient(cookies={"arl": ARL_COOKIE}, headers=HEADERS) as client:
        try:
            token = await get_api_token(client)
            
            # 1. Get Secret Metadata
            res = await client.post(
                PRIVATE_API,
                params={"method": "song.getData", "input": "3", "api_version": "1.0", "api_token": token},
                json={"sng_id": track_id}
            )
            track_info = res.json().get("results")
            if not track_info:
                raise HTTPException(status_code=404, detail="Track not found")
            
            # 2. Prepare Stream
            cdn_url = generate_cdn_url(track_info)
            bf_key = derive_bf_key(track_id)
            cipher = Blowfish.new(bf_key, Blowfish.MODE_ECB)

            async def iterate_and_decrypt():
                async with client.stream("GET", cdn_url) as r:
                    buffer = b""
                    chunk_index = 0
                    async for chunk in r.aiter_bytes():
                        buffer += chunk
                        while len(buffer) >= 2048:
                            block = buffer[:2048]
                            buffer = buffer[2048:]
                            # Only every 3rd 2kb block is encrypted
                            if chunk_index % 3 == 0:
                                yield cipher.decrypt(block)
                            else:
                                yield block
                            chunk_index += 1
                    if buffer:
                        yield buffer

            return StreamingResponse(iterate_and_decrypt(), media_type="audio/mpeg")
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
