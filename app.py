import base64
import csv
import io
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional, Tuple
from flask import Flask, jsonify, render_template_string, request, send_file
from google import genai
from google.genai import types
import requests

app = Flask(__name__)

# ==========================================
# 1. JARVIS KAYIT VE AYAR DOSYALARI (TAM KAPASİTE)
# ==========================================
CSV_FILE = "jarvis_kayitlar.csv"
AYARLAR_FILE = "jarvis_ayarlar.json"
TOKEN_CACHE_FILE = "etsy_token_cache.json"

# ---------- ETSY & GEMINI AYARLARI ----------
ETSY_BASE = "https://api.etsy.com/v3"
ETSY_API_KEY = os.environ.get("ETSY_API_KEY", "").strip()
ETSY_SHARED_SECRET = os.environ.get("ETSY_SHARED_SECRET", "").strip()
ETSY_REFRESH_TOKEN = os.environ.get("ETSY_REFRESH_TOKEN", "").strip()
ETSY_SHOP_ID = str(os.environ.get("ETSY_SHOP_ID", "")).strip()
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()


def ayarlari_yukle() -> Dict[str, Any]:
  if os.path.exists(AYARLAR_FILE):
    try:
      with open(AYARLAR_FILE, "r", encoding="utf-8") as f:
        return json.load(f)
    except Exception:
      pass
  return {"varsayilan_konsept": "Wooden baby crib mobile", "otomatik_mod": True}


def ayarlari_kaydet(yeni_ayarlar: Dict[str, Any]) -> None:
  mevcut = ayarlari_yukle()
  mevcut.update(yeni_ayarlar)
  with open(AYARLAR_FILE, "w", encoding="utf-8") as f:
    json.dump(mevcut, f, ensure_ascii=False, indent=2)


def csv_kaydet(
    concept: str, listing_id: Any, title: str, price: Any, images_count: int
) -> None:
  dosya_var = os.path.exists(CSV_FILE)
  with open(CSV_FILE, "a", newline="", encoding="utf-8") as f:
    writer = csv.writer(f)
    if not dosya_var:
      writer.writerow(
          ["Tarih", "Konsept", "Listing_ID", "Baslik", "Fiyat", "Gorsel_Sayisi"]
      )
    writer.writerow([
        time.strftime("%Y-%m-%d %H:%M:%S"),
        concept,
        listing_id,
        title,
        price,
        images_count,
    ])


# ==========================================
# 2. ETSY & GEMINI ÜRETİM FONKSİYONLARI
# ==========================================
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
      model="gemini-2.5-flash",
      contents=prompt,
      config=types.GenerateContentConfig(response_mime_type="application/json"),
  )
  return clean_json(response.text)


def generate_image(prompt: str) -> bytes:
  client = genai.Client(api_key=GEMINI_API_KEY)
  result = client.models.generate_images(
      model="imagen-3.0-generate-002",
      prompt=prompt,
      config=types.GenerateImagesConfig(
          number_of_images=1, output_mime_type="image/jpeg", aspect_ratio="1:1"
      ),
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
        if cached:
          current_refresh = cached
    except Exception:
      pass

  payload = {
      "grant_type": "refresh_token",
      "client_id": ETSY_API_KEY.split(":")[0].strip(),
      "refresh_token": current_refresh,
  }
  resp = requests.post(
      "https://api.etsy.com/v3/public/oauth/token", data=payload, timeout=30
  )
  if resp.status_code != 200:
    fail(f"Token yenileme hatası: {resp.text}")

  data = resp.json()
  token = data.get("access_token", "").strip()
  new_refresh = data.get("refresh_token", "").strip()

  if new_refresh:
    try:
      with open(TOKEN_CACHE_FILE, "w") as f:
        json.dump({"refresh_token": new_refresh}, f)
    except Exception:
      pass
  return token


def etsy_request(
    method: str,
    path: str,
    token: str,
    json_body: dict = None,
    files: dict = None,
) -> dict:
  url = f"{ETSY_BASE}{path}"
  headers = {
      "x-api-key": get_formatted_api_key(),
      "Authorization": f"Bearer {token}",
  }
  if files is None:
    headers["Content-Type"] = "application/json"

  resp = requests.request(
      method.upper(), url, headers=headers, json=json_body, files=files, timeout=60
  )
  if resp.status_code not in (200, 201):
    fail(f"Etsy API hatası ({path}): {resp.text}")
  return resp.json() if resp.text.strip() else {}


# ==========================================
# 3. WEB ROTALARI (/uret, /ayarlar, /kayitlar)
# ==========================================
@app.route("/uret", methods=["POST", "GET"])
def create_product_automatically():
  try:
    ayarlar = ayarlari_yukle()
    data = request.get_json(silent=True)
    raw_text = request.get_data(as_text=True).strip()

    concept = ""
    if isinstance(data, dict):
      concept = (
          data.get("concept") or data.get("urun") or data.get("u") or ""
      ).strip()
    elif raw_text and not raw_text.startswith("{"):
      concept = raw_text

    if not concept:
      concept = request.args.get(
          "concept", ayarlar.get("varsayilan_konsept", "Wooden baby crib mobile")
      ).strip()

    listing_data = generate_listing_content(concept)
    token = refresh_etsy_token()

    profiles = etsy_request(
        "GET", f"/application/shops/{ETSY_SHOP_ID}/shipping-profiles", token
    )
    if not profiles.get("results"):
      fail("Kargo profili bulunamadı.")
    shipping_id = profiles["results"][0]["shipping_profile_id"]

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
        "state": "draft",
    }

    created = etsy_request(
        "POST",
        f"/application/shops/{ETSY_SHOP_ID}/listings",
        token,
        json_body=payload,
    )
    listing_id = int(created["listing_id"])

    image_prompts = listing_data.get("image_prompts", [])
    if len(image_prompts) < 3:
      image_prompts = [
          f"Professional product photography of {concept}, realistic",
          f"Product shot of {concept} from different angle",
          f"Close up detail shot of {concept}",
      ]

    uploaded_count = 0
    for index, prompt in enumerate(image_prompts[:3]):
      try:
        img_bytes = generate_image(prompt)
        files = {
            "image": (
                f"product_{index}.jpg",
                io.BytesIO(img_bytes),
                "image/jpeg",
            )
        }
        etsy_request(
            "POST",
            f"/application/shops/{ETSY_SHOP_ID}/listings/{listing_id}/images",
            token,
            files=files,
        )
        uploaded_count += 1
      except Exception as e:
        print(f"⚠️ Görsel {index+1} yüklenemedi: {e}")

    # Üretilen ürünü CSV dosyasına kaydet
    csv_kaydet(
        concept,
        listing_id,
        listing_data["title"],
        listing_data["price"],
        uploaded_count,
    )

    return (
        jsonify({
            "ok": True,
            "concept": concept,
            "listing_id": listing_id,
            "title": listing_data["title"],
            "price": listing_data["price"],
            "images_uploaded": uploaded_count,
            "csv_kayit": "Basarili",
        }),
        200,
    )

  except Exception as e:
    return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/ayarlar", methods=["GET", "POST"])
def ayarlar_endpoint():
  if request.method == "POST":
    yeni = request.get_json(silent=True) or {}
    ayarlari_kaydet(yeni)
    return jsonify({"ok": True, "ayarlar": ayarlari_yukle()})
  return jsonify(ayarlari_yukle())


@app.route("/kayitlar", methods=["GET"])
def kayitlari_indir():
  if os.path.exists(CSV_FILE):
    return send_file(CSV_FILE, as_attachment=True)
  return jsonify({"ok": False, "mesaj": "Henuz kayitli urun yok."})


@app.route("/")
def index():
  return jsonify({
      "durum": "Jarvis Tam Kapasite Aktif!",
      "rotalar": {
          "/uret": (
              "Otomatik Etsy urun ve gorsel uretir (POST veya GET)"
          ),
          "/ayarlar": "Ayarlari goruntuler ve gunceller",
          "/kayitlar": "Uretilen urunlerin CSV dosyasini indirir",
      },
  })


if __name__ == "__main__":
  port = int(os.environ.get("PORT", 10000))
  app.run(host="0.0.0.0", port=port)
