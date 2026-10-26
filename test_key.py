import os
from dotenv import load_dotenv
from google import genai

load_dotenv(override=True)
key = os.getenv("GEMINI_API_KEY")

print(f"Loaded Key prefix: {key[:8]}...")

client = genai.Client(api_key=key)
response = client.models.generate_content(
    model="gemini-2.5-flash",
    contents="Say hello!"
)

print("SUCCESS! Gemini Response:", response.text)