"""One-off: re-render the current meme with new caption text, same template.
Usage: MEME_CAPTIONS='["top","bottom"]' python recaption_meme.py
Does not touch the newsletter text or meme rotation history."""
import json, os, sys
sys.path.insert(0, os.path.dirname(__file__))
import requests
from config import PATHS

captions = json.loads(os.environ["MEME_CAPTIONS"])
with open(PATHS["meme"]) as f:
    meme = json.load(f)
memes = requests.get("https://api.imgflip.com/get_memes", timeout=20).json()["data"]["memes"]
tpl = next(t for t in memes if t["name"] == meme["template_name"])
if len(captions) != tpl["box_count"]:
    raise SystemExit(f"{tpl['name']} needs {tpl['box_count']} captions, got {len(captions)}")
data = {"template_id": tpl["id"], "username": os.environ["IMGFLIP_USERNAME"], "password": os.environ["IMGFLIP_PASSWORD"]}
data.update({f"text{i}": c for i, c in enumerate(captions)})
r = requests.post("https://api.imgflip.com/caption_image", data=data, timeout=30).json()
if not r.get("success"):
    raise SystemExit(f"Imgflip failed: {r.get('error_message')}")
meme.update(image_url=r["data"]["url"], captions=captions)
with open(PATHS["meme"], "w") as f:
    json.dump(meme, f, indent=2)
print("Re-captioned:", r["data"]["url"])
