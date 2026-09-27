# QUICKSTART — 3 tareeqe, jo aapke setup par suit kare

Aapka agent: hourly cron → news fetch → logo card + 3–5 bullets (EN + Roman Urdu) → Facebook page.
Sirf **translation step** badalna hai. Neeche 3 options hain, aasan se mushkil tak.

---

## OPTION 1 — Sirf prompt paste karein (0 code, 0 hosting) ★ sab se aasan

Agar aapka agent AI Arena / n8n / Make / Zapier ke AI node se ban gaya hai, to
bas yeh karein:

**Step 1.** `prompts/system_prompt.txt` khol kar poora text copy karein.

**Step 2.** Apne workflow mein **AI/LLM node** add karein (ya jo abhi Roman Urdu bana raha
hai, usi node ko edit karein):

| Field | Value |
|---|---|
| System / Instructions | `prompts/system_prompt.txt` ka poora text paste |
| User / Input | `{{title_en}}` newline `{{bullet_1}}` newline `{{bullet_2}}` … |
| Temperature | `0.2` |
| Output type | Plain text (**JSON mode OFF**) |

**Step 3.** Node ke baad ek chhota sa **Code/Set node** lagayein jo output ko clean kare:

```javascript
// n8n Code node / Make Text parser / Zapier Formatter
let raw = ($json.message?.content ?? $json.text ?? $json.output ?? '').trim();

// fences aur "Translation:" jaisi extra baatein hatayein
raw = raw.replace(/^```[a-z]*\n?/i, '').replace(/```$/i, '').trim();
raw = raw.replace(/^(roman urdu|translation|translated text)\s*[:\-]\s*/i, '').trim();

// Urdu/Arabic script ko kabhi card tak na jaane dein
raw = raw.replace(/[\u0600-\u06FF\u0750-\u077F\uFB50-\uFDFF\uFE70-\uFEFF\u0900-\u097F]/g, '');

const lines = raw.split('\n');
const title_ur   = lines[0] || '';
const bullets_ur = lines.slice(1)
  .map(l => l.replace(/^\s*[-*\u2022]\s*/, '').trim())
  .filter(Boolean);

return [{ json: {
  title_ur,
  bullets_ur,
  card_text_ur: [title_ur, ...bullets_ur.map(b => '- ' + b)].join('\n'),
  translation_ok: bullets_ur.length >= 3 && bullets_ur.length <= 5
}}];
```

**Step 4.** `translation_ok` false ho to ek IF branch lagayein → dobara translate node
chalayein (1 retry) → phir bhi fail ho to aapko alert bhejein, post skip karein.

**Step 5.** Card renderer ko `card_text_en` aur `card_text_ur` dono dein (same card,
English upar / Roman Urdu neeche), aur Facebook caption mein bhi dono.

> Bas. Yeh 90% quality problem theek kar dega, kyunke aapka current prompt
> model ko structure aur Roman-script ki sakht hidayat nahi de raha.

---

## OPTION 2 — Python module apne agent script mein import karein

Agar aapka agent ek Python script hai:

```bash
# folder ko apne project mein copy karein
cp -r roman-urdu-translator /path/to/your-news-agent/
cd /path/to/your-news-agent/roman-urdu-translator
export RU_PROVIDER=openai RU_MODEL=gpt-4o-mini RU_API_KEY=sk-...
python3 tests/test_offline.py      # 31 tests pass hone chahiye
```

```python
# aapke agent ka news-posting hissa
from roman_urdu import translate_news

def make_card_data(title_en: str, bullets_en: list[str]) -> dict:
    # bullets ko ek hi call mein bhejein - context better rehta hai
    src = "\n".join("- " + b for b in bullets_en)
    res = translate_news(src)                       # validate + retry + fallback khud hota hai

    bullets_ur = [l.lstrip("-*• ").strip() for l in res["lines_ur"]]

    # defensive alignment: count kabhi mismatch na ho
    if len(bullets_ur) != len(bullets_en):
        bullets_ur = (bullets_ur + bullets_en[len(bullets_ur):])[:len(bullets_en)]

    if not res["ok"] or res.get("warning"):
        log.warning("Roman Urdu quality issue: %s", res["problems"] or res["warning"])
        # yahan aap alert bhej sakte hain (Telegram/Slack/email)

    return {
        "title_en": title_en,
        "bullets_en": bullets_en,
        "title_ur": translate_news(title_en)["roman_urdu"],
        "bullets_ur": bullets_ur,
        "card_text_en": "\n".join([title_en] + [f"- {b}" for b in bullets_en]),
        "card_text_ur": "\n".join([translate_news(title_en)["roman_urdu"]]
                                  + [f"- {b}" for b in bullets_ur]),
    }

data = make_card_data(
    "Earthquake of magnitude 6.2 hits eastern Afghanistan",
    ["At least 18 people were killed and 40 injured",
     "Rescue teams reached the affected area after six hours",
     "The UN has promised emergency aid",
     "Roads and communication networks remain badly damaged"],
)
render_card(data["card_text_en"], data["card_text_ur"])   # aapka existing renderer
post_to_facebook(caption=data["card_text_en"] + "\n\n" + data["card_text_ur"])
```

Ya JSON ke sath:

```python
from roman_urdu import translate_payload
out = translate_payload({"title_en": "...", "bullets_en": ["...", "..."], "summary_en": "..."})
print(out["roman_urdu"])     # card-ready
print(out["bullets_ur"])     # list, bullets_en ke sath aligned
```

---

## OPTION 3 — HTTP microservice (n8n / Make / Zapier ke liye best)

**Local / VPS par:**

```bash
cd roman-urdu-translator
cp .env.example .env          # RU_API_KEY bharein
chmod +x run.sh
./run.sh                      # ya:  ./run.sh --mock   (test mode)
```

**Docker:**

```bash
docker compose up -d --build
# ya
docker build -t roman-urdu-translator .
docker run -d --name ru-translate -p 8080:8080 --env-file .env roman-urdu-translator
```

**Systemd (VPS par 24/7):**

```bash
sudo mkdir -p /opt/roman-urdu-translator && sudo cp -r ./* /opt/roman-urdu-translator/
sudo cp .env.example /opt/roman-urdu-translator/.env    # API key bharein
sudo cp deploy/roman-urdu-translator.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now roman-urdu-translator
journalctl -u roman-urdu-translator -f
```

**Aapke automation se call:**

```bash
curl -X POST http://YOUR-HOST:8080/translate-card \
  -H 'Content-Type: application/json' \
  -d '{"title_en":"Petrol price may increase by Rs 8 per litre this week",
       "bullets_en":["The government is reviewing oil prices today",
                     "Petrol could become Rs 8 per litre more expensive",
                     "New prices will apply from midnight"]}'
```

Response:

```json
{
  "ok": true,
  "title_en": "Petrol price may increase by Rs 8 per litre this week",
  "title_ur": "Is haftay petrol ki qeemat mein 8 rupay prati litre izafa hone ka imkaan",
  "bullets_en": ["...", "...", "..."],
  "bullets_ur": ["Hukumat aaj oil prices ka jaeza le rahi hai", "...", "..."],
  "card_text_en": "TITLE\n- b1\n- b2\n- b3",
  "card_text_ur": "TITLE\n- b1\n- b2\n- b3",
  "bullet_count": 3
}
```

n8n ke ready nodes: `n8n_workflow_snippet.json` import kar lein.

---

## Deploy ke baad 5-minute checklist

1. `GET /health` → `"key_present": true` hona chahiye (warna `.env` theek nahi).
2. Browser mein `http://YOUR-HOST:8080/` khol kar demo page par **Translate & build card**
   dabayein → banner par "LIVE" likha ho, "MOCK MODE" nahi.
3. `python3 tests/test_offline.py` → `31 passed, 0 failed`.
4. Ek real news item par test karein, aur check karein:
   - [ ] koi Urdu/Arabic harf nahi
   - [ ] bullets ki ginti same (3–5)
   - [ ] numbers/dates bilkul same (`6.2`, `Rs 45 billion`, `12 March`)
   - [ ] naam/brand English mein (`Babar Azam`, `Google`, `UN`)
   - [ ] emojis/hashtags barqarar
   - [ ] koi extra line jaise "Sure, here is..." nahi
5. Aapke Facebook post mein English + Roman Urdu dono ek hi card par aa rahe hain.
6. Public internet par host kar rahe hain to `RU_SERVER_TOKEN` set karein
   (warna koi bhi aapki API key ka bill chala sakta hai).

## Cost / speed (rough)

| Model | 1 news item (title + 5 bullets, ~900 tokens with few-shot) | Quality |
|---|---|---|
| `gpt-4o-mini` | ~$0.0003 | Acha, hourly posting ke liye best value |
| `gpt-4o` | ~$0.004 | Behtar idiomatic Urdu |
| `gemini-2.0-flash` | ~$0.0002 | Acha + tez |
| Groq `llama-3.3-70b` | Free tier available | Acha, sub-second |
| Ollama local | Free | Theek, lekin chhota model Urdu mein kamzor ho sakta hai |

Hourly posting = ~720 items/month ≈ **$0.22/month** with `gpt-4o-mini`.
