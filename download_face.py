import requests
import os

URL = "https://raw.githubusercontent.com/serengil/deepface/master/tests/dataset/img1.jpg"
OUTPUT = "test_face.jpg"

print(f"Downloading from {URL}...")
try:
    headers = {'User-Agent': 'Mozilla/5.0'}
    r = requests.get(URL, headers=headers, timeout=30)
    if r.status_code == 200:
        with open(OUTPUT, "wb") as f:
            f.write(r.content)
        print(f"Success! Saved to {OUTPUT} ({len(r.content)} bytes)")
    else:
        print(f"Failed: HTTP {r.status_code}")
except Exception as e:
    print(f"Error: {e}")
