import base64
import io
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional, Tuple
from flask import Flask, jsonify, request
from google import genai
from google.genai import types
import requests

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
      model="gemini-2.5-flash",
      contents=prompt,
      config=types.GenerateContentConfig(response_mime_type="application/json"),
  )
  return clean_json(response.text)


def generate_image(prompt: str)
