"""
Step 3 of the pipeline: turn the week's headline story into an actual meme
image, using Imgflip's template library + captioning API.

Requires GitHub secrets: IMGFLIP_USERNAME, IMGFLIP_PASSWORD (any free
Imgflip account works — sign up once, store the credentials as secrets).
Some templates are Imgflip-Premium-only; caption_image will error on those,
so we only offer Claude the templates flagged safe/free.
"""

import json
import os
import random
import re
import time
import requests
from config import PATHS, CLAUDE_MODEL

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
IMGFLIP_USERNAME = os.environ["IMGFLIP_USERNAME"]
IMGFLIP_PASSWORD = os.environ["IMGFLIP_PASSWORD"]

# A curated shortlist rather than dumping Imgflip's full top-100 at Claude —
# keeps template choice relevant to fantasy-football-shaped stories instead
# of drifting into templates that don't fit a recap. Extend this list freely;
# names must match Imgflip's own template names exactly for the lookup below.
CANDIDATE_TEMPLATES = [
    "Drake Hotline Bling",
    "Distracted Boyfriend",
    "Two Buttons",
    "Change My Mind",
    "This Is Fine",
    "Expanding Brain",
    "Woman Yelling at Cat",
    "Surprised Pikachu",
    "Disaster Girl",
    "Is This A Pigeon",
    "Gru's Plan",
    "Bernie I Am Once Again Asking For Your Support",
    # Added for variety. Names that don't match Imgflip exactly are skipped
    # by get_templates(), and anything with box_count > 2 is filtered out.
    "Batman Slapping Robin",
    "Mocking Spongebob",
    "Evil Kermit",
    "Tuxedo Winnie The Pooh",
    "Roll Safe Think About It",
    "One Does Not Simply",
    "Futurama Fry",
    "Waiting Skeleton",
    "Monkey Puppet",
    "Unsettled Tom",
    "Hide the Pain Harold",
    "Leonardo Dicaprio Cheers",
    "Success Kid",
    "Third World Skeptical Kid",
]

# Templates used in the last N newsletters are withheld from the model so
# the same one can't win every week. Python owns this, not the prompt.
RECENT_EXCLUDE_COUNT = 4
HISTORY_PATH = os.path.join(os.path.dirname(PATHS["meme"]), "meme_history.json")


def load_history():
    try:
        with open(HISTORY_PATH) as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_history(history, template_name):
    history = [h for h in history if isinstance(h, str)]
    history.append(template_name)
    os.makedirs(os.path.dirname(HISTORY_PATH), exist_ok=True)
    with open(HISTORY_PATH, "w") as f:
        json.dump(history[-30:], f, indent=2)


def apply_rotation(templates, history):
    """Drop recently used templates and shuffle the rest (the model leans
    toward whatever is listed first). Never returns an empty list."""
    recent = set(history[-RECENT_EXCLUDE_COUNT:])
    fresh = [t for t in templates if t["name"] not in recent]
    if len(fresh) < 3:
        # pool too small to exclude safely: only exclude the single latest
        fresh = [t for t in templates if t["name"] != (history[-1] if history else None)]
    pool = fresh or templates
    random.shuffle(pool)
    return pool


def get_templates():
    resp = requests.get("https://api.imgflip.com/get_memes", timeout=20)
    resp.raise_for_status()
    all_templates = resp.json()["data"]["memes"]
    by_name = {t["name"]: t for t in all_templates}
    candidates = [by_name[name] for name in CANDIDATE_TEMPLATES if name in by_name]
    # Filter to box_count <= 2 at runtime rather than trusting prompt
    # compliance for higher box counts — a 4-box template like Expanding
    # Brain kept rendering with blank panels because the model reliably
    # under-delivers captions on complex templates even when told the
    # exact count required. Determined from Imgflip's own live metadata,
    # not a hardcoded guess about which named templates have how many boxes.
    filtered = [t for t in candidates if t.get("box_count", 99) <= 2]
    return filtered if filtered else candidates[:1]  # never return an empty list


def parse_best_json(text, required_keys):
    """See generate_draft_recap.py for the full rationale — raw_decode from
    every '{' handles a genuinely malformed first attempt without letting
    it corrupt parsing of a valid object elsewhere in the text."""
    decoder = json.JSONDecoder(strict=False)
    results = []
    for i, ch in enumerate(text):
        if ch != "{":
            continue
        try:
            obj, _ = decoder.raw_decode(text, i)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            results.append(obj)

    if not results:
        raise ValueError(f"No JSON object found in model response. Raw text was:\n{text}")

    for obj in reversed(results):
        missing = [k for k in required_keys if k not in obj]
        if not missing:
            return obj

    missing_summary = [[k for k in required_keys if k not in o] for o in results]
    raise ValueError(
        f"Found {len(results)} JSON object(s) in the response but none had all "
        f"required keys ({required_keys}). Missing per object: {missing_summary}\nRaw text was:\n{text}"
    )


MAX_ATTEMPTS = 3

SELF_CORRECTION_MARKERS = (
    "actually, no", "actually no", "wait, i need", "wait, let me",
    "let me reconsider", "let me redo", "let me re-do", "let me provide the",
    "on second thought", "correction:", "scratch that", "i made an error",
)
SELF_CORRECTION_PARENTHETICAL = re.compile(r"\(\s*actually\b[^)]*\)", re.IGNORECASE)
FIELD_NAME_LEAK_PATTERN = re.compile(r"\b[a-z][a-z0-9]*_[a-z0-9_]*\b")


def find_self_correction_artifact(obj):
    """Same check as generate_newsletter.py — see that file for the full
    rationale. Lower-risk here given short caption output, but the retry
    is cheap insurance."""
    if isinstance(obj, str):
        lowered = obj.lower()
        for marker in SELF_CORRECTION_MARKERS:
            if marker in lowered:
                return marker
        m = SELF_CORRECTION_PARENTHETICAL.search(obj)
        if m:
            return m.group(0)
        return None
    if isinstance(obj, dict):
        for v in obj.values():
            found = find_self_correction_artifact(v)
            if found:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = find_self_correction_artifact(v)
            if found:
                return found
    return None


def find_field_name_leak(obj):
    """Same check as generate_newsletter.py — see that file for the full
    rationale."""
    if isinstance(obj, str):
        m = FIELD_NAME_LEAK_PATTERN.search(obj)
        if m:
            return m.group(0)
        return None
    if isinstance(obj, dict):
        for v in obj.values():
            found = find_field_name_leak(v)
            if found:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = find_field_name_leak(v)
            if found:
                return found
    return None


def pick_template_and_captions(meme_brief, templates):
    template_options = [{"name": t["name"], "box_count": t["box_count"]} for t in templates]
    system_prompt = ("You pick the best-fit meme template for a fantasy "
                      "football story and write the caption text. "
                      "CRITICAL: base your captions ONLY on facts literally "
                      "stated in the Story text below — do not substitute, "
                      "invent, or 'correct' any team name, player name, or "
                      "specific detail. If Story says a team, a player, or a "
                      "number, quote it exactly; do not swap in a different "
                      "one from your own general knowledge, even if it feels "
                      "like a more familiar or common reference. Each template "
                      "in the list has a box_count — you MUST return exactly "
                      "that many captions, in order top-to-bottom/left-to-right, "
                      "or the image will render with blank boxes. Return ONLY "
                      "valid JSON: {\"template_name\": \"...\", \"captions\": "
                      "[\"...\", \"...\"]} where captions has EXACTLY box_count "
                      "entries for the template you chose. Keep captions short "
                      "and punchy — longtime friends who talk real trash, not "
                      "corporate-safe humor. Choose the template whose format "
                      "genuinely fits the joke; do not default to a generic "
                      "disaster or panic template unless the story really is one.")
    last_error = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            resp = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": CLAUDE_MODEL,
                    "max_tokens": 1000,
                    "thinking": {"type": "disabled"},
                    "system": system_prompt,
                    "messages": [{
                        "role": "user",
                        "content": f"Story: {meme_brief}\n\nAvailable templates (with required box_count): {json.dumps(template_options)}"
                    }],
                },
                timeout=60,
            )
            if not resp.ok:
                print(f"---- API ERROR RESPONSE (attempt {attempt}/{MAX_ATTEMPTS}) ----")
                print(f"Status: {resp.status_code}")
                print(f"Body: {resp.text}")
                print("---- END API ERROR RESPONSE ----")
            resp.raise_for_status()
            data = resp.json()
            text = "".join(b["text"] for b in data["content"] if b["type"] == "text")
            print(f"---- RAW MEME MODEL RESPONSE (attempt {attempt}/{MAX_ATTEMPTS}) ----")
            print(text)
            print("---- END ----")
            if not text.strip():
                raise RuntimeError(f"Model returned no text content. Full API response: {json.dumps(data)}")
            parsed = parse_best_json(text, ["template_name", "captions"])
            artifact = find_self_correction_artifact(parsed)
            if artifact:
                raise ValueError(f"Response contains a visible self-correction artifact ({artifact!r}) — treating as a failed attempt.")
            leak = find_field_name_leak(parsed)
            if leak:
                raise ValueError(f"Response contains a leaked internal field name ({leak!r}) — treating as a failed attempt.")
            return parsed
        except (ValueError, RuntimeError, requests.exceptions.RequestException) as e:
            last_error = e
            print(f"Attempt {attempt}/{MAX_ATTEMPTS} failed: {e}")
            if attempt < MAX_ATTEMPTS:
                print("Retrying...")
                time.sleep(3)
    raise RuntimeError(f"All {MAX_ATTEMPTS} attempts failed to produce valid output. Last error: {last_error}")


def render_meme(template_id, captions):
    """captions is a list — length MUST match the template's box_count, or
    Imgflip silently leaves the missing boxes blank (this was the original
    bug: only text0/text1 were ever sent, so 4-box templates like Expanding
    Brain rendered with two empty panels)."""
    data = {
        "template_id": template_id,
        "username": IMGFLIP_USERNAME,
        "password": IMGFLIP_PASSWORD,
    }
    for i, caption in enumerate(captions):
        data[f"text{i}"] = caption

    resp = requests.post("https://api.imgflip.com/caption_image", data=data, timeout=30)
    resp.raise_for_status()
    result = resp.json()
    if not result.get("success"):
        raise RuntimeError(f"Imgflip captioning failed: {result.get('error_message')}")
    return result["data"]["url"]


def main():
    with open(PATHS["newsletter_draft"]) as f:
        draft = json.load(f)

    meme_brief = draft["meme_brief"]
    history = load_history()
    templates = apply_rotation(get_templates(), history)
    print(f"Recent templates (excluded): {history[-RECENT_EXCLUDE_COUNT:]}")
    print(f"Offered to model: {[t['name'] for t in templates]}")
    choice = pick_template_and_captions(meme_brief, templates)

    template = next((t for t in templates if t["name"] == choice["template_name"]), None)
    if template is None:
        print(f"WARNING: model chose {choice['template_name']!r}, which was not offered; using {templates[0]['name']!r}.")
        template = templates[0]
        choice["template_name"] = template["name"]
    captions = choice.get("captions", [])
    if len(captions) != template["box_count"]:
        print(f"WARNING: model returned {len(captions)} captions but "
              f"{choice['template_name']} needs {template['box_count']} — "
              f"padding/truncating to fit rather than failing the run.")
        captions = (captions + [""] * template["box_count"])[:template["box_count"]]

    image_url = render_meme(template["id"], captions)

    with open(PATHS["meme"], "w") as f:
        json.dump({"image_url": image_url, "template_name": choice["template_name"], "captions": captions}, f, indent=2)

    save_history(history, choice["template_name"])
    print(f"Meme rendered: {image_url}")


if __name__ == "__main__":
    main()
