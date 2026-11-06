import os
import io
import sys
import json
import time
import base64
import re
from typing import Any, Dict, List, Optional, Tuple

import requests
from google import genai
from google.genai import types
from openai import OpenAI

ETSY_BASE = "https://api.etsy.com/v3"
TOKEN_URL = "https://api.etsy.com/v3/public/oauth/token"

# ---------- CONFIG ----------

ETSY_API_KEY = os.environ.get("ETSY_API_KEY", "").strip()
ETSY_SHARED_SECRET = os.environ.get("ETSY_SHARED_SECRET", "").strip()
ETSY_REFRESH_TOKEN = os.environ.get("ETSY_REFRESH_TOKEN", "").strip()
ETSY_SHOP_ID = str(os.environ.get("ETSY_SHOP_ID", "")).strip()

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()

TEXT_PROVIDER = os.environ.get("TEXT_PROVIDER", "gemini").strip().lower()
IMAGE_PROVIDER = os.environ.get("IMAGE_PROVIDER", "gemini").strip().lower()

GEMINI_TEXT_MODEL = os.environ.get("GEMINI_TEXT_MODEL", "gemini-2.5-flash").strip()
GEMINI_IMAGE_MODEL = os.environ.get("GEMINI_IMAGE_MODEL", "imagen-3.0-generate-002").strip()
OPENAI_TEXT_MODEL = os.environ.get("OPENAI_TEXT_MODEL", "gpt-4o-mini").strip()
OPENAI_IMAGE_MODEL = os.environ.get("OPENAI_IMAGE_MODEL", "gpt-image-1").strip()

DEFAULT_QUANTITY = int(os.environ.get("DEFAULT_QUANTITY", "10"))
WHO_MADE = os.environ.get("ETSY_WHO_MADE", "i_did").strip()
WHEN_MADE = os.environ.get("ETSY_WHEN_MADE", "made_to_order").strip()
LISTING_TYPE = os.environ.get("ETSY_LISTING_TYPE", "physical").strip()
# İlk denemede mağazan kapanmasın diye varsayılan olarak Draft (Taslak) bırakır. 
# Direkt yayınlansın istersen Render'da AUTO_ACTIVATE=true yapabilirsin.
AUTO_ACTIVATE = os.environ.get("AUTO_ACTIVATE", "false").strip().lower() == "true"

REQUEST_TIMEOUT = 60
TOKEN_CACHE_FILE = "etsy_token_cache.json"


# ---------- HELPERS ----------

def fail(msg: str) -> None:
    print(f"❌ HATA: {msg}", file=sys.stderr)
    raise RuntimeError(msg)


def clean_json(text: str) -> Dict[str, Any]:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return json.loads(text.strip())


def normalize_tags(tags: Any) -> List[str]:
    if isinstance(tags, str):
        tags = [x.strip() for x in tags.split(",") if x.strip()]
    if not isinstance(tags, list):
        fail("Tags listesi alınamadı.")
    out = []
    seen = set()
    for t in tags:
        # Etsy özel karakterleri kabul etmez, sadece harf, rakam ve boşluk
        t = re.sub(r"[^a-zA-Z0-9\s]", "", str(t)).strip()
        if not t:
            continue
        t = t[:20].strip()
        key = t.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(t)
        if len(out) == 13:
            break
    return out


def choose_text_provider() -> str:
    if TEXT_PROVIDER == "gemini" and GEMINI_API_KEY:
        return "gemini"
    if TEXT_PROVIDER == "openai" and OPENAI_API_KEY:
        return "openai"
    if GEMINI_API_KEY:
        return "gemini"
    if OPENAI_API_KEY:
        return "openai"
    fail("Metin üretimi için GEMINI_API_KEY veya OPENAI_API_KEY gerekli.")
    return ""


def choose_image_provider() -> str:
    if IMAGE_PROVIDER == "gemini" and GEMINI_API_KEY:
        return "gemini"
    if IMAGE_PROVIDER == "openai" and OPENAI_API_KEY:
        return "openai"
    if GEMINI_API_KEY:
        return "gemini"
    if OPENAI_API_KEY:
        return "openai"
    fail("Görsel üretimi için GEMINI_API_KEY veya OPENAI_API_KEY gerekli.")
    return ""


# ---------- AI (METİN VE GÖRSEL ÜRETİMİ) ----------

def generate_listing_content(concept: str) -> Dict[str, Any]:
    prompt = f"""
You are an expert Etsy SEO listing generator.
Create a high-converting Etsy listing package for this physical product concept:
"{concept}"

Return ONLY valid JSON with this exact structure:
{{
  "title": "English SEO title, maximum 140 characters, separated by commas",
  "description": "Engaging, detailed English product description with bullet points and dimensions",
  "tags": ["tag1", "tag2", "tag3", "tag4", "tag5", "tag6", "tag7", "tag8", "tag9", "tag10", "tag11", "tag12", "tag13"],
  "price": 34.90,
  "image_prompt": "Ultra-realistic professional product photography of {concept}, natural warm studio lighting, aesthetic minimalist background, 8k resolution, commercial Etsy style"
}}

Rules:
- Title max 140 chars.
- Exactly 13 unique tags, each tag MUST BE 20 characters or less, letters and spaces only.
- Price must be a realistic number.
"""
    provider = choose_text_provider()
    print(f"🤖 Metin üretiliyor ({provider})...")

    if provider == "gemini":
        client = genai.Client(api_key=GEMINI_API_KEY)
        response = client.models.generate_content(
            model=GEMINI_TEXT_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json"
            )
        )
        data = clean_json(response.text)
    else:
        client = OpenAI(api_key=OPENAI_API_KEY)
        response = client.chat.completions.create(
            model=OPENAI_TEXT_MODEL,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": "Return only valid JSON."},
                {"role": "user", "content": prompt},
            ],
        )
        data = clean_json(response.choices[0].message.content)

    title = str(data.get("title", "")).strip()[:140]
    description = str(data.get("description", "")).strip()
    tags = normalize_tags(data.get("tags", []))
    price = round(float(data.get("price", 29.99)), 2)
    image_prompt = str(data.get("image_prompt", "")).strip()

    if not title or not description or not image_prompt:
        fail("Başlık, açıklama veya görsel komutu eksik üretildi.")

    return {
        "title": title,
        "description": description,
        "tags": tags,
        "price": price,
        "image_prompt": image_prompt,
    }


def generate_product_image_bytes(image_prompt: str) -> Tuple[bytes, str]:
    provider = choose_image_provider()
    print(f"🎨 Görsel üretiliyor ({provider})...")

    if provider == "gemini":
        try:
            client = genai.Client(api_key=GEMINI_API_KEY)
            result = client.models.generate_images(
                model=GEMINI_IMAGE_MODEL,
                prompt=image_prompt,
                config=types.GenerateImagesConfig(
                    number_of_images=1,
                    output_mime_type="image/jpeg",
                    aspect_ratio="1:1",
                )
            )
            if result.generated_images:
                return result.generated_images[0].image.image_bytes, "image/jpeg"
        except Exception as e:
            print(f"⚠️ Gemini görsel hatası ({e}), OpenAI varsa ona geçiliyor...")
            if not OPENAI_API_KEY:
                raise

    # OpenAI (Yedek veya Ana Seçenek)
    client = OpenAI(api_key=OPENAI_API_KEY)
    response = client.images.generate(
        model=OPENAI_IMAGE_MODEL,
        prompt=image_prompt,
        size="1024x1024",
    )
    b64 = response.data[0].b64_json
    if b64:
        return base64.b64decode(b64), "image/png"
    elif response.data[0].url:
        img_resp = requests.get(response.data[0].url, timeout=REQUEST_TIMEOUT)
        return img_resp.content, "image/png"

    fail("Görsel üretilemedi.")
    return b"", ""


# ---------- ETSY AUTH & REQUEST ----------

def get_formatted_api_key() -> str:
    # Etsy v3 yeni standartlarında x-api-key: keystring:shared_secret ister
    if ":" in ETSY_API_KEY:
        return ETSY_API_KEY
    if ETSY_SHARED_SECRET:
        return f"{ETSY_API_KEY}:{ETSY_SHARED_SECRET}"
    return ETSY_API_KEY


def get_client_id() -> str:
    # OAuth token yenilerken sadece keystring (iki noktadan önceki kısım) kullanılır
    return ETSY_API_KEY.split(":")[0].strip()


def refresh_etsy_access_token() -> str:
    # Önce önbellekte güncel refresh_token var mı kontrol et
    current_refresh_token = ETSY_REFRESH_TOKEN
    if os.path.exists(TOKEN_CACHE_FILE):
        try:
            with open(TOKEN_CACHE_FILE, "r") as f:
                cached = json.load(f)
                if cached.get("refresh_token"):
                    current_refresh_token = cached["refresh_token"]
        except Exception:
            pass

    payload = {
        "grant_type": "refresh_token",
        "client_id": get_client_id(),
        "refresh_token": current_refresh_token,
    }
    resp = requests.post(TOKEN_URL, data=payload, timeout=REQUEST_TIMEOUT)
    if resp.status_code != 200:
        fail(f"Etsy token yenileme hatası: {resp.status_code} - {resp.text}")
    
    data = resp.json()
    token = data.get("access_token", "").strip()
    new_refresh = data.get("refresh_token", "").strip()

    # Yeni refresh token'ı kaydet ki bir sonraki çalışmada patlamasın
    if new_refresh:
        try:
            with open(TOKEN_CACHE_FILE, "w") as f:
                json.dump({"refresh_token": new_refresh, "updated_at": int(time.time())}, f)
            print(f"🔑 Yeni Etsy Refresh Token önbelleğe kaydedildi.")
        except Exception:
            pass

    if not token:
        fail("Etsy access token alınamadı.")
    return token


def etsy_request(
    method: str,
    path: str,
    access_token: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    json_body: Optional[Dict[str, Any]] = None,
    files: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    url = f"{ETSY_BASE}{path}"
    headers = {
        "x-api-key": get_formatted_api_key(),
        "Authorization": f"Bearer {access_token}",
    }
    if files is None:
        headers["Content-Type"] = "application/json"

    resp = requests.request(
        method=method.upper(),
        url=url,
        headers=headers,
        params=params,
        json=json_body if files is None else None,
        files=files,
        timeout=REQUEST_TIMEOUT,
    )
    if resp.status_code not in (200, 201):
        fail(f"Etsy API hatası ({path}): {resp.status_code} - {resp.text}")
    return resp.json() if resp.text.strip() else {}


# ---------- ETSY AUTO-ID ----------

def get_shipping_profile_id(access_token: str) -> int:
    data = etsy_request("GET", f"/application/shops/{ETSY_SHOP_ID}/shipping-profiles", access_token)
    profiles = data.get("results") or []
    if not profiles:
        fail("Mağazada Shipping Profile (Kargo Profili) bulunamadı. Etsy panelinden en az 1 kargo profili açmalısın.")
    return int(profiles[0]["shipping_profile_id"])


def get_readiness_state_id(access_token: str) -> Optional[int]:
    try:
        data = etsy_request("GET", f"/application/shops/{ETSY_SHOP_ID}/readiness-state-definitions", access_token)
        states = data.get("results") or []
        if states:
            for key in ("readiness_state_id", "readiness_state_definition_id", "id"):
                if key in states[0]:
                    return int(states[0][key])
    except Exception as e:
        print(f"⚠️ Readiness state çekilemedi, opsiyonel olarak geçiliyor: {e}")
    return None


def get_taxonomy_id(access_token: str, concept: str, title: str) -> int:
    try:
        data = etsy_request("GET", "/application/seller-taxonomy/nodes", access_token)
        roots = data.get("results") or []
        nodes = []

        def walk(node: Dict[str, Any]) -> None:
            nodes.append(node)
            for child in node.get("children", []) or []:
                walk(child)

        for r in roots:
            walk(r)

        target_words = set(re.findall(r"[a-z0-9]+", f"{concept} {title}".lower()))
        best_id = 1063  # Varsayılan: Home & Living
        best_score = 0

        for n in nodes:
            nid = n.get("id")
            name = str(n.get("name", "")).lower()
            words = set(re.findall(r"[a-z0-9]+", name))
            score = len(target_words.intersection(words))
            if score > best_score and nid:
                best_score = score
                best_id = int(nid)
        return best_id
    except Exception:
        return 1063


# ---------- ETSY LISTING & IMAGE ----------

def create_etsy_listing(
    access_token: str,
    title: str,
    description: str,
    tags: List[str],
    price: float,
    shipping_profile_id: int,
    readiness_state_id: Optional[int],
    taxonomy_id: int,
) -> int:
    payload: Dict[str, Any] = {
        "quantity": DEFAULT_QUANTITY,
        "title": title[:140],
        "description": description,
        "price": price,
        "who_made": WHO_MADE,
        "when_made": WHEN_MADE,
        "taxonomy_id": taxonomy_id,
        "shipping_profile_id": shipping_profile_id,
        "tags": tags[:13],
        "type": LISTING_TYPE,
        "state": "draft",
    }
    if readiness_state_id is not None:
        payload["readiness_state_id"] = readiness_state_id

    data = etsy_request(
        "POST",
        f"/application/shops/{ETSY_SHOP_ID}/listings",
        access_token,
        json_body=payload,
    )
    listing_id = data.get("listing_id")
    if not listing_id:
        fail("Listing oluşturuldu ama listing_id dönmedi.")
    return int(listing_id)


def upload_listing_image(
    access_token: str,
    listing_id: int,
    image_bytes: bytes,
    mime_type: str,
) -> Dict[str, Any]:
    extension = "jpg" if mime_type == "image/jpeg" else "png"
    files = {
        "image": (f"product.{extension}", io.BytesIO(image_bytes), mime_type),
    }
    return etsy_request(
        "POST",
        f"/application/shops/{ETSY_SHOP_ID}/listings/{listing_id}/images",
        access_token,
        files=files,
    )


def activate_listing(access_token: str, listing_id: int) -> Dict[str, Any]:
    return etsy_request(
        "PATCH",
        f"/application/shops/{ETSY_SHOP_ID}/listings/{listing_id}",
        access_token,
        json_body={"state": "active"},
    )


# ---------- PIPELINE ----------

def run_pipeline(concept: str) -> Dict[str, Any]:
    print(f"🚀 Jarvis Pipeline Başlatıldı -> Konsept: {concept}")
    listing = generate_listing_content(concept)
    print(f"✅ Başlık: {listing['title']}")

    image_bytes, mime_type = generate_product_image_bytes(listing["image_prompt"])
    print(f"✅ Görsel üretildi ({len(image_bytes)} bytes)")

    access_token = refresh_etsy_access_token()
    shipping_profile_id = get_shipping_profile_id(access_token)
    readiness_state_id = get_readiness_state_id(access_token)
    taxonomy_id = get_taxonomy_id(access_token, concept, listing["title"])

    print(f"📦 Etsy'de ilan açılıyor...")
    listing_id = create_etsy_listing(
        access_token=access_token,
        title=listing["title"],
        description=listing["description"],
        tags=listing["tags"],
        price=listing["price"],
        shipping_profile_id=shipping_profile_id,
        readiness_state_id=readiness_state_id,
        taxonomy_id=taxonomy_id,
    )
    print(f"✅ Taslak İlan Açıldı! ID: {listing_id}")

    print(f"🖼️ Görsel Etsy'ye yükleniyor...")
    image_result = upload_listing_image(access_token, listing_id, image_bytes, mime_type)
    print(f"✅ Görsel yüklendi!")

    activation_result = None
    if AUTO_ACTIVATE:
        print(f"📢 İlan aktif (yayına) alınıyor...")
        activation_result = activate_listing(access_token, listing_id)

    return {
        "ok": True,
        "listing_id": listing_id,
        "listing_url": f"https://www.etsy.com/your/shops/me/tools/listings/{listing_id}",
        "title": listing["title"],
        "price": listing["price"],
        "state": "active" if AUTO_ACTIVATE else "draft"
    }


def main() -> None:
    # Konsept verilmediyse bile hata verip çökmesin, varsayılan konseptle çalışsın
    concept = " ".join(sys.argv[1:]).strip() or os.environ.get("PRODUCT_CONCEPT", "Wooden Baby Crib Mobile Montessori Nursery Decor").strip()
    result = run_pipeline(concept)
    print("🎉 İŞLEM TAMAMLANDI:")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
