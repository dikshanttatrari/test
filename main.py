import os
import hashlib
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from Crypto.Cipher import Blowfish, AES

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

ARL_COOKIE = os.getenv("ARL_COOKIE")
SECRET_KEY = b"jo6a16n6gu5p096e"
PRIVATE_API = "https://www.deezer.com/ajax/gw-light.php"

# Strict headers to look like the official Android App
HEADERS = {
    "User-Agent": "Deezer/9.41.0.1 (Android; 13; Mobile; en)",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "max-age=0",
}

async def get_user_token(client: httpx.AsyncClient):
    """Login with ARL and get the user API token"""
    try:
        res = await client.post(
            PRIVATE_API,
            params={"method": "deezer.getUserData", "input": "3", "api_version": "1.0"},
            headers=HEADERS
        )
        data = res.json()
        return data.get("results", {}).get("checkForm")
    except Exception:
        return None

# --- CRYPTO (Your Logic) ---
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
    format_id = "1"
    hash_input = "¤".join([track_info["MD5_ORIGIN"], format_id, track_info["SNG_ID"], track_info["MEDIA_VERSION"]])
    m = hashlib.md5()
    m.update(hash_input.encode("utf-8"))
    hash_result = m.hexdigest()
    url_part = "¤".join([hash_result, hash_input, ""])
    while len(url_part) % 16 != 0: url_part += " "
    cipher = AES.new(b"jo6aey6haid2Teih", AES.MODE_ECB)
    return f"https://e-cdns-proxy-{track_info['MD5_ORIGIN'][0]}.dzcdn.net/mobile/1/{cipher.encrypt(url_part.encode()).hex()}"

# --- ENDPOINTS ---

@app.get("/api/search")
async def search(query: str):
    async with httpx.AsyncClient(cookies={"arl": ARL_COOKIE}, headers=HEADERS) as client:
        # 1. Try Private Search first
        token = await get_user_token(client)
        if token:
            res = await client.post(
                PRIVATE_API,
                params={"method": "search.getSongs", "input": "3", "api_version": "1.0", "api_token": token},
                json={"QUERY": query, "NB": 20}
            )
            data = res.json()
            tracks = data.get("results", {}).get("data", [])
            
            if tracks:
                return {"source": "private", "data": [{
                    "id": t.get("SNG_ID"),
                    "title": t.get("SNG_TITLE"),
                    "artist": t.get("ART_NAME"),
                    "image": f"https://e-cdns-images.dzcdn.net/images/cover/{t.get('ALB_PICTURE')}/250x250.jpg"
                } for t in tracks if t.get("SNG_ID")]}

        # 2. Fallback to Public Search (Since your server is in the USA, this should work)
        public_res = await client.get(f"https://api.deezer.com/search?q={query}")
        public_data = public_res.json()
        public_tracks = public_data.get("data", [])

        if public_tracks:
            return {"source": "public", "data": [{
                "id": str(t.get("id")),
                "title": t.get("title"),
                "artist": t.get("artist", {}).get("name"),
                "image": t.get("album", {}).get("cover_medium")
            } for t in public_tracks]}

        return {"data": [], "debug": "Both private and public search returned no data."}

@app.get("/api/stream-deezer/{track_id}")
async def stream_deezer(track_id: str):
    async with httpx.AsyncClient(cookies={"arl": ARL_COOKIE}, headers=HEADERS) as client:
        try:
            # 1. Get the token (Handshake)
            api_token = await get_user_token(client)
            if not api_token:
                raise HTTPException(status_code=401, detail="ARL Rejected by Deezer")

            # 2. Get track info
            res = await client.post(
                PRIVATE_API,
                params={"method": "song.getData", "input": "3", "api_version": "1.0", "api_token": api_token},
                json={"sng_id": track_id},
            )
            data = res.json()
            track_info = data.get("results")

            # --- THE FIX: Check if MD5_ORIGIN exists ---
            if not track_info or "MD5_ORIGIN" not in track_info:
                print(f"FAILED TO GET STREAM DATA. Response: {data}")
                raise HTTPException(
                    status_code=403, 
                    detail="This song is restricted or requires a Premium ARL."
                )
            
            # 3. Proceed with decryption if data exists
            cdn_url = generate_download_url(track_info)
            bf_key = derive_blowfish_key(track_id)
            cipher = Blowfish.new(bf_key, Blowfish.MODE_ECB)

            async def iterate_and_decrypt():
                async with client.stream("GET", cdn_url) as r:
                    if r.status_code != 200:
                        return # Stop if CDN fails
                    chunk_index = 0
                    async for chunk in r.aiter_bytes(chunk_size=2048):
                        if chunk_index % 3 == 0 and len(chunk) == 2048:
                            yield cipher.decrypt(chunk)
                        else:
                            yield chunk
                        chunk_index += 1
            
            return StreamingResponse(iterate_and_decrypt(), media_type="audio/mpeg")

        except Exception as e:
            # Log the full error to Railway console
            print(f"STREAM ERROR: {str(e)}")
            raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
