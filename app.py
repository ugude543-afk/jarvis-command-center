import os
import io
import sys
import json
import base64
import time
from typing import Any, Dict, List, Optional, Tuple
import requests
from flask import Flask, jsonify, request
from google import genai
from google.genai import types

app = Flask(__name__)

# ---------- CONFIGURATION ----------
ETSY_BASE = "https://api.etsy.com/v3"
ETSY_API_KEY = os.environ.get("ETSY_API_KEY", "").strip()
ETSY_SHARED_SECRET = os.environ.get("ETSY_SHARED_SECRET", "").strip()
ETSY_REFRESH_TOKEN = os.environ.get("ETSY_REFRESH_TOKEN", "").strip()
ETSY_SHOP_ID = str(os.environ.get("ETSY_SHOP_ID", "")).strip()
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()

TOKEN_CACHE_FILE = "etsy_token_cache.json"

def fail(msg: str) -> None:
    print(f"❌ HATA: {msg}", file=sys.stderr)
    raise RuntimeError(msg)

def clean_json(text: str) -> Dict[str, Any]:
    text = text.strip().replace("```json", "").replace("```", "").strip()
    return json.loads(text)

def generate_listing_content(concept: str) -> Dict[str, Any]:
    prompt = f"""
    You are an expert Etsy SEO generator. Create a high-converting listing for: "{concept}".
    Return ONLY valid JSON with keys: title, description, tags, price, image_prompts.
    Rules:
    - title: English SEO title, max 140 characters.
    - description: Engaging, formatted English description.
    - tags: Exactly 13 unique English tags, max 20 characters each.
    - price: Realistic number.
    - image_prompts: A list of 3 detailed English prompts for Imagen 3:
      1. Ultra-realistic professional product photography of the item.
      2. Normal product-focused shot from a different angle.
      3. Another normal product-focused shot highlighting details.
    """
    client = genai.Client(api_key=GEMINI_API_KEY)
    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=prompt,
        config=types.GenerateContentConfig(response_mime_type="application/json")
    )
    return clean_json(response.text)

def generate_image(prompt: str) -> bytes:
    client = genai.Client(api_key=GEMINI_API_KEY)
    print(f"🎨 Üretiliyor: {prompt[:50]}...")
    result = client.models.generate_images(
        model='imagen-3.0-generate-002',
        prompt=prompt,
        config=types.GenerateImagesConfig(
            number_of_images=1,
            output_mime_type="image/jpeg",
            aspect_ratio="1:1"
        )
    )
    return result.generated_images[0].image.image_bytes

def get_formatted_api_key() -> str:
    if ":" in ETSY_API_KEY:
        return ETSY_API_KEY
    if ETSY_SHARED_SECRET:
        return f"{ETSY_API_KEY}:{ETSY_SHARED_SECRET}"
    return ETSY_API_KEY

def refresh_etsy_token() -> str:
    current_refresh = ETSY_REFRESH_TOKEN
    if os.path.exists(TOKEN_CACHE_FILE):
        try:
            with open(TOKEN_CACHE_FILE, "r") as f:
                cached = json.load(f).get("refresh_token")
                if cached: current_refresh = cached
        except Exception: pass

    payload = {
        "grant_type": "refresh_token",
        "client_id": ETSY_API_KEY.split(":")[0].strip(),
        "refresh_token": current_refresh,
    }
    resp = requests.post("https://api.etsy.com/v3/public/oauth/token", data=payload, timeout=30)
    if resp.status_code != 200:
        fail(f"Token yenileme hatası: {resp.text}")
    
    data = resp.json()
    token = data.get("access_token", "").strip()
    new_refresh = data.get("refresh_token", "").strip()

    if new_refresh:
        try:
            with open(TOKEN_CACHE_FILE, "w") as f:
                json.dump({"refresh_token": new_refresh}, f)
        except Exception: pass
    return token

def etsy_request(method: str, path: str, token: str, json_body: dict = None, files: dict = None) -> dict:
    url = f"{ETSY_BASE}{path}"
    headers = {"x-api-key": get_formatted_api_key(), "Authorization": f"Bearer {token}"}
    if files is None: headers["Content-Type"] = "application/json"
    
    resp = requests.request(method.upper(), url, headers=headers, json=json_body, files=files, timeout=60)
    if resp.status_code not in (200, 201):
        fail(f"Etsy API hatası ({path}): {resp.text}")
    return resp.json() if resp.text.strip() else {}

@app.route("/uret", methods=["POST"])
def create_product_automatically():
    data = request.json
    concept = data.get("concept", "").strip()
    if not concept:
        return jsonify({"ok": False, "error": "Konsept gerekli"}), 400

    print(f"🚀 Başlatılıyor: {concept}")
    listing_data = generate_listing_content(concept)
    
    token = refresh_etsy_token()
    
    # Kargo profili çek
    profiles = etsy_request("GET", f"/application/shops/{ETSY_SHOP_ID}/shipping-profiles", token)
    if not profiles.get("results"):
        fail("Kargo profili bulunamadı.")
    shipping_id = profiles["results"][0]["shipping_profile_id"]

    # İlanı oluştur
    payload = {
        "quantity": 10,
        "title": listing_data["title"][:140],
        "description": listing_data["description"],
        "price": float(listing_data["price"]),
        "who_made": "i_did",
        "when_made": "made_to_order",
        "taxonomy_id": 1063,
        "shipping_profile_id": shipping_id,
        "tags": listing_data["tags"][:13],
        "type": "physical",
        "state": "draft"
    }
    
    created = etsy_request("POST", f"/application/shops/{ETSY_SHOP_ID}/listings", token, json_body=payload)
    listing_id = int(created["listing_id"])
    print(f"✅ İlan oluşturuldu (ID: {listing_id}). Görseller üretiliyor...")

    # 3 Görseli Sırayla Üret ve Yükle
    image_prompts = listing_data.get("image_prompts", [])
    if len(image_prompts) < 3:
        # Fallback eğer model az üretirse
        image_prompts = [
            f"Professional product photography of {concept}, realistic",
            f"Product shot of {concept} from different angle",
            f"Close up detail shot of {concept}"
        ]

    for index, prompt in enumerate(image_prompts[:3]):
        try:
            img_bytes = generate_image(prompt)
            files = {"image": (f"product_{index}.jpg", io.BytesIO(img_bytes), "image/jpeg")}
            print(f"🖼️ Yükleniyor görsel {index+1}...")
            etsy_request("POST", f"/application/shops/{ETSY_SHOP_ID}/listings/{listing_id}/images", token, files=files)
        except Exception as e:
            print(f"⚠️ Görsel {index+1} yüklenemedi: {e}")

    return jsonify({
        "ok": True,
        "listing_id": listing_id,
        "title": listing_data["title"],
        "images_uploaded": min(3, len(image_prompts))
    })

@app.route("/")
def index():
    return "Sistem aktif. POST /uret adresine JSON ile { 'concept': 'ürünün' } gönder."

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
