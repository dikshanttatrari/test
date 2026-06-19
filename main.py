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

# --- CONFIGURATION ---
# Fetches from Railway Environment Variables
ARL_COOKIE = os.getenv("ARL_COOKIE")
SECRET_KEY = b"jo6a16n6gu5p096e"
PRIVATE_API = "https://www.deezer.com/ajax/gw-light.php"

# Safety check
if not ARL_COOKIE:
    print("❌ ERROR: ARL_COOKIE environment variable is not set!")

# --- SESSION MANAGEMENT ---

async def get_user_token(client: httpx.AsyncClient):
    """Login with ARL and get the user API token"""
    res = await client.post(
        PRIVATE_API,
        params={
            "method": "deezer.getUserData",
            "input": "3",
            "api_version": "1.0",
            "api_token": "",
        },
    )
    data = res.json()
    # Handle cases where login might fail
    try:
        token = data["results"]["checkForm"]
        return token
    except KeyError:
        print(f"Login failed. Response: {data}")
        return None

async def get_track_info(client: httpx.AsyncClient, api_token: str, track_id: str):
    res = await client.post(
        PRIVATE_API,
        params={
            "method": "song.getData",
            "input": "3",
            "api_version": "1.0",
            "api_token": api_token,
        },
        json={"sng_id": track_id},
    )
    data = res.json()
    return data["results"]

# --- URL GENERATION (Your Logic) ---

def generate_download_url(track_info: dict):
    format_id = "1"
    md5_origin = track_info["MD5_ORIGIN"]
    media_version = track_info["MEDIA_VERSION"]
    song_id = track_info["SNG_ID"]

    hash_input = "¤".join([md5_origin, format_id, str(song_id), str(media_version)])
    m = hashlib.md5()
    m.update(hash_input.encode("utf-8"))
    hash_result = m.hexdigest()

    url_part = "¤".join([hash_result, hash_input, ""])
    while len(url_part) % 16 != 0:
        url_part += " "

    aes_key = b"jo6aey6haid2Teih"
    cipher = AES.new(aes_key, AES.MODE_ECB)
    encrypted = cipher.encrypt(url_part.encode("utf-8"))
    encrypted_hex = encrypted.hex()

    return f"https://e-cdns-proxy-{md5_origin[0]}.dzcdn.net/mobile/1/{encrypted_hex}"

# --- DECRYPTION (Your Logic) ---

def derive_blowfish_key(track_id: str):
    m = hashlib.md5()
    m.update(track_id.encode())
    track_id_hash = m.hexdigest().encode()
    key = b""
    for i in range(16):
        val = track_id_hash[i] ^ track_id_hash[i + 16] ^ SECRET_KEY[i]
        key += bytes([val])
    return key

def decrypt_chunk(chunk, key):
    cipher = Blowfish.new(key, Blowfish.MODE_ECB)
    return cipher.decrypt(chunk)

# --- ENDPOINTS ---

@app.get("/")
def home():
    return {"status": "Deezer Proxy API is running"}

@app.get("/api/search")
async def search(query: str):
    """Search using ARL session to avoid empty data in blocked regions"""
    async with httpx.AsyncClient(cookies={"arl": ARL_COOKIE}) as client:
        # We use the private search method because it bypasses regional blocks better
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
        } for t in tracks]}

@app.get("/api/stream-deezer/{track_id}")
async def stream_deezer(track_id: str):
    try:
        async with httpx.AsyncClient(
            cookies={"arl": ARL_COOKIE},
            headers={"User-Agent": "Mozilla/5.0"}
        ) as client:

            api_token = await get_user_token(client)
            if not api_token:
                raise HTTPException(status_code=401, detail="Session expired")

            track_info = await get_track_info(client, api_token, track_id)
            cdn_url = generate_download_url(track_info)
            bf_key = derive_blowfish_key(track_id)

            async def iterate_and_decrypt():
                async with client.stream("GET", cdn_url) as r:
                    if r.status_code != 200:
                        raise HTTPException(status_code=r.status_code, detail="CDN Error")
                    chunk_index = 0
                    async for chunk in r.aiter_bytes(chunk_size=2048):
                        if chunk_index % 3 == 0 and len(chunk) == 2048:
                            yield decrypt_chunk(chunk, bf_key)
                        else:
                            yield chunk
                        chunk_index += 1

            return StreamingResponse(iterate_and_decrypt(), media_type="audio/mpeg")

    except Exception as e:
        print(f"Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    # Railway provides the PORT environment variable
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
