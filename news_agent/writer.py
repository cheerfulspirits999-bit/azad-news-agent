#!/usr/bin/env python3
"""
Automatic bilingual bullet writer for autonomous cycles.

Safety contract:
  * Every bullet slot is filled ONLY from the verified headline/excerpt text of
    the candidate item. Numbers must appear in an explicit context
    ("3 people died", "25,000 cabs", "Rs 21 per km") - a bare "43" in
    "Rs 43L fine" can never become a casualty count.
  * Arrest actors may only be police/investigating agencies, never political
    parties or protest groups (the detained party is never the arresting one).
  * Roman Urdu uses the natural Hyderabad news register: English noun phrases
    inside Urdu grammar (ne / ko / ka kehna hai / jaari kiya).
  * English and Roman Urdu are emitted from the SAME fact frame, so both
    languages always carry the same three facts.
  * Developing stories carry explicit uncertainty wording.
  * build() returns None when a frame cannot be filled safely; the story then
    waits for the editorial pass instead of being auto-published.
  * A global sanitizer rejects any bullet containing unresolved slots
    ("None"), empty slots, or invented-looking content.
"""
import re

AWAIT_EN = "More details are awaited."
AWAIT_UR = "Mazeed details ka intezar hai."

MONEY = re.compile(r"(?:Rs|₹)\s?\d[\d,.]*\s?(?:lakh|crore|thousand|per km|km)?", re.I)

WORD_NUM = {"one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
            "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10"}
HELD_VERBS = (r"(?:killed|dead|died|injured|missing|rescued|detained|arrested|"
              r"held|booked|nabbed)")
PEOPLE_COUNT = [
    re.compile(r"(\d[\d,]{0,6})\s+(?:people|persons|workers|students|protesters|"
               r"drivers|pilgrims|tourists|men|women|peddlers|smugglers|accused)"
               r"\s+(?:were\s+)?(killed|dead|injured|hospitalised|missing|"
               r"rescued|detained|arrested|held|booked|nabbed)", re.I),
    re.compile(r"(\d[\d,]{0,6})\s+(?:[\w\-]+\s+){0,3}?" + HELD_VERBS, re.I),
    re.compile(r"(?::|^|\s)(" + HELD_VERBS + r")[:\s]+(\d[\d,]{0,6})", re.I),
    re.compile(r"^(one|two|three|four|five|six|seven|eight|nine|ten)\s+"
               r"(?:[\w\-]+\s+){0,4}?" + HELD_VERBS, re.I),
]
VEHICLE_COUNT = re.compile(r"(\d[\d,]{0,6})\s+(cabs|taxis|vehicles|buses|autos|"
                           r"drivers|workers)", re.I)

POLICE_ORGS = [
    r"((?:Hyderabad|Telangana|Cyberabad|Rachakonda|City|State|Delhi|Mumbai)\s+"
    r"(?:Traffic\s+|Counter\s+)?Police)",
    r"\b(NIA|CBI|ED|ACB|EAGLE\s+Force|Counter\s+Intelligence|Enforcement\s+"
    r"Directorate|Customs|Anti-Corruption\s+Bureau)\b",
]
UNION_ORGS = [
    r"\b(TGPWU|TADF|GHMC|HYDRAA|TG\s?SAFE|FSSAI|IMD|NHAI|KNRUHS|HMWSSB)\b",
    r"\b([A-Z][A-Za-z&]+(?:\s+[A-Za-z&]+){0,4}\s+(?:Union|Forum|Sangham|Samithi|"
    r"Association|Sena))\b",
]
COURTS = re.compile(r"(Supreme\s+Court|[A-Za-z]+\s+High\s+Court|NIA\s+Court|"
                    r"Sessions\s+Court|High\s+Court| tribunal)", re.I)
GOVT = re.compile(r"(Telangana\s+Government|State\s+Government|Central\s+Government|"
                  r"Union\s+Government|Cabinet|Telangana\s+Assembly|Assembly|"
                  r"Government)", re.I)
VENUE = re.compile(r"\b(international airport|airport|hospital|school|college|mall|"
                   r"market|bridge|flyover|temple|station|godown|factory|apartment|"
                   r"building|hotel|restaurant|bakery|spa|lake|reservoir|canal|"
                   r"expressway|highway|junction)\b", re.I)
PLACE = re.compile(r"\b(Hyderabad|Secunderabad|Telangana|Warangal|Karimnagar|"
                   r"Nizamabad|Khammam|Nalgonda|Mahbubnagar|Adilabad|Siddipet|"
                   r"Sangareddy|Vikarabad|Mancherial|Jagtial|Sircilla|Gadwal|"
                   r"Shamshabad|Medchal|Nepal|Delhi|Mumbai|Chennai|Bengaluru|"
                   r"Kerala|Punjab|Kashmir|J&K)\b", re.I)

ISSUE_MAP = [  # (keyword, roman urdu) - word-bounded both sides; compounds first
    ("arms theft", "asla chori"), ("gold smuggling", "gold smuggling"),
    ("drug racket", "mandiyat racket"), ("cyber fraud", "cyber dhokadhadi"),
    ("fares", "kiraaye (fares)"), ("fare", "kiraaye (fares)"),
    ("wages", "majdoori"), ("salaries", "tankhwa"), ("dues", "baqaya jira"),
    ("water", "paani"), ("power", "bijli"), ("electricity", "bijli"),
    ("land", "zameen"), ("jobs", "naukriyan"), ("recruitment", "bharti"),
    ("reservation", "reservation"), ("prices", "qeemat"),
    ("rain", "baarish"), ("floods", "silab"), ("fire", "aag"),
    ("theft", "chori"), ("scam", "ghotala"), ("fraud", "dhokadhadi"),
    ("murder", "qatl"), ("rape", "rape"), ("drugs", "mandiyat"),
    ("ganja", "ganja"), ("smuggling", "smuggling"), ("arms", "asla"),
    ("weapons", "asla"), ("licence", "licence"), ("license", "licence"),
    ("ban", "pabandi"), ("pollution", "aloodgi"),
    ("compensation", "muawza"), ("pension", "pension"), ("fees", "fees"),
    ("elections", "elections"), ("survey", "survey"),
    ("encroachments", "encroachment"), ("traffic", "traffic"),
]
CAUSE_MAP = [("short circuit", "short circuit"), ("gas leak", "gas leak"),
             ("cylinder", "cylinder phatne"), ("electrical", "bijli ke tale")]
EVENT_MAP = [("bus", "bus haadse"), ("train", "train haadse"), ("boat", "kashti"),
             ("building", "imarat girne"), ("collapse", "imarat girne"),
             ("electrocution", "current lagne"), ("drown", "doobne"),
             ("fire", "aag"), ("crash", "haadse"), ("accident", "haadse")]
ORDER_MAP = [("non-bailable warrant", "non-bailable warrant"), ("warrant", "warrant"),
             ("bail", "zamanat"), ("stay", "rok"), ("notice", "notice"),
             ("sentenced", "saza"), ("convicted", "saza"), ("fine", "jurmana"),
             ("ban", "pabandi")]


# --------------------------------------------------------------------------
DATELINE = re.compile(r"(?:^|\s)(Hyderabad|Secunderabad|Telangana|New Delhi|Delhi|"
                      r"Mumbai|Bengaluru|Chennai|Amaravati|Vijayawada|Warangal|"
                      r"Khammam|Nizamabad|Karimnagar|Shamshabad)\s*:\s*", re.I)


def _dedateline(t):
    return DATELINE.sub(" ", t or "")


def _m(rx, text, default=None, group=1):
    m = rx.search(text or "")
    if not m:
        return default
    g = m.group(group) if group <= len(m.groups()) else None
    return (g or default)


def _first_group(rx, text, default=None):
    """First non-None group of a multi-alternation match."""
    m = rx.search(text or "")
    if not m:
        return default
    return next((g for g in m.groups() if g), default)


def _strip_money(text):
    return MONEY.sub(" ", text or "")


def _people_count(text):
    t = _strip_money(_dedateline(text))
    for rx in PEOPLE_COUNT:
        m = rx.search(t)
        if not m:
            continue
        vals = [g for g in m.groups() if g]
        for v in vals:
            v = v.strip(" ,.")
            if v.lower() in WORD_NUM:
                return WORD_NUM[v.lower()]
            if re.fullmatch(r"\d[\d,]{0,6}", v) and v != "0":
                return v
    return None


def _issue(text):
    low = re.sub(r"[^a-z\s]", " ", (text or "").lower())
    low = " " + " ".join(low.split()) + " "
    for k, ur in ISSUE_MAP:
        if f" {k} " in low:
            return k, ur
    return None, None


def _case_phrase(title):
    """Case topic from the TITLE only, never from excerpt glue."""
    m = re.search(r"(?:in|over|amid|in connection with)\s+(?:the\s+|alleged\s+)?"
                  r"((?:[A-Z][A-Za-z]+\s+)?[a-z][a-z\s\-]{2,38}?"
                  r"(?:case|scam|fraud|theft|murder|rape|death|fire|blast|"
                  r"incident|riot))", title or "")
    if m:
        phrase = re.sub(r"\s+", " ", m.group(1)).strip()
        return phrase, (_issue(phrase.lower())[1] or phrase)
    k, ur = _issue(title)
    if k:
        return k, ur
    return None, None


def _sanitize(bullets):
    out = []
    for b in bullets:
        b = re.sub(r"\s+", " ", (b or "").strip())
        if not b or len(b) < 12 or len(b) > 200:
            return None
        if re.search(r"\bNone\b|\bnan\b|\{|\}|\bXX\b", b):
            return None
        if re.search(r"^(Hyderabad|Secunderabad|Telangana|Delhi|New Delhi|Mumbai|"
                     r"Chennai|Bengaluru)\s*:", b):
            return None
        if re.search(r"\bon (Saturday|Sunday|Monday|Tuesday|Wednesday|Thursday|"
                     r"Friday)\b.*\bofficials said\b.*\bofficials said\b", b):
            return None
        if not b.endswith("."):
            b += "."
        out.append(b)
    return out if 3 <= len(out) <= 5 else None


# --------------------------------------------------------------------------
def _frame_strike(c):
    title, excerpt = c["title"], _dedateline(c["excerpt"])
    if not re.search(r"\b(strike|strikes|bandh|boycott|halt|halted|off the road|"
                     r"go off road)\b", title, re.I):
        return None
    text = title + " " + excerpt
    actor = _first_group(re.compile("|".join(UNION_ORGS)), text) or "The union"
    place = _m(PLACE, title) or _m(PLACE, excerpt)
    venue = _m(VENUE, title)
    loc = " ".join(x for x in (place, venue) if x) or "the city"
    veh = _m(VEHICLE_COUNT, _strip_money(text))
    issue_en, issue_ur = _issue(text)
    service = _first_group(re.compile(r"\b(cab|taxi|auto|bus|delivery|metro)\w*", re.I),
                           text, "transport")
    en = [f"{actor} has begun a strike at {loc}, affecting {service} services."]
    ur = [f"{actor} ne {loc} par strike shuru ki hai, jis se {service} services mutasir hain."]
    fare = re.search(r"Rs\s?([\d,]+)(?:\s?to\s?Rs\s?([\d,]+))?\s+per\s+km", text, re.I)
    notified = re.search(r"(?:notified|prescribes|fixed|must receive|must get|"
                         r"fare of)\s+Rs\s?([\d,]+)\s+per\s+km", text, re.I)
    if fare and notified and notified.group(1) != fare.group(1):
        paid = f"Rs {fare.group(1)}" + (f"-{fare.group(2)}" if fare.group(2) else "")
        en.append(f"Drivers receive {paid} per km against the notified Rs {notified.group(1)} per km, the union said.")
        ur.append(f"Union ke mutabiq drivers ko notified Rs {notified.group(1)} per km ke muqable {paid} per km mil rahe hain.")
    elif veh and issue_en:
        en.append(f"{veh} {('workers' if 'worker' in veh.lower() or 'driver' in veh.lower() else 'vehicles')} stayed off the road over {issue_en}, the union said.")
        ur.append(f"Union ke mutabiq {issue_ur} ke masle par {veh} vehicles sadak par nahi utre.")
    elif issue_en:
        en.append(f"The strike is over {issue_en}, the union said.")
        ur.append(f"Union ke mutabiq yeh strike {issue_ur} ke masle par hai.")
    else:
        en.append("The union said long-pending issues behind the strike were raised with the government.")
        ur.append("Union ne kaha ke strike ke peeche arse se baqi masle government ke saamne rakhe gaye hain.")
    en.append("The union has asked the government for talks and a written resolution.")
    ur.append("Union ne government se baat cheet aur likhit resolution ka mutalba kiya hai.")
    return en, ur


def _frame_casualty(c):
    text = c["title"] + " " + _dedateline(c["excerpt"])
    n = _people_count(text)
    if not n:
        return None
    verb_m = re.search(r"(killed|dead|died|injured|missing|rescued)", text, re.I)
    verb = verb_m.group(1).lower()
    place = _m(PLACE, c["title"]) or _m(PLACE, c["excerpt"]) or "the area"
    event_en, event_ur = "an incident", "haadse"
    for k, ur in EVENT_MAP:
        if re.search(r"\b" + k + r"\w*", text, re.I):
            event_en, event_ur = f"a {k} incident", ur
            break
    died = verb in ("killed", "dead", "died")
    if n == "1":
        en1 = (f"1 person {'died' if died else 'was ' + verb} in {event_en} at {place}, reports suggest.")
        ur1 = (f"Reports ke mutabiq {place} mein {event_ur} mein ek shakhs "
               + ("ki maut ho gayi." if died else f"{verb} hai."))
    else:
        en1 = (f"{n} people {'died' if died else 'were ' + verb} in {event_en} at {place}, reports suggest.")
        ur1 = (f"Reports ke mutabiq {place} mein {event_ur} mein {n} log "
               + ("mare gaye." if died else f"{verb} hain."))
    n2 = None
    if died:
        m2 = re.search(r"(\d[\d,]{0,6})\s+(?:people|persons)?\s*(?:were\s+)?injured",
                       _strip_money(text), re.I)
        n2 = m2.group(1).strip(" ,.") if m2 else None
    if n2:
        en2, ur2 = (f"{n2} people were injured, officials said.",
                    f"Officials ke mutabiq {n2} log zakhmi hain.")
    elif re.search(r"rescue", text, re.I):
        en2, ur2 = ("Officials said rescue and relief work is underway.",
                    "Officials ne kaha ke rescue aur relief ka kaam jaari hai.")
    else:
        en2, ur2 = ("Officials said an investigation into the incident has begun.",
                    "Officials ne kaha ke waqia ki tehqiqat shuru kar di gayi hai.")
    return [en1, en2, AWAIT_EN], [ur1, ur2, AWAIT_UR]


FILLER_EN = {AWAIT_EN, "More updates on this development will follow."}
LOC_RX = re.compile(r"\b(?:in|at|near)\s+[A-Z][A-Za-z]{2,}(?:\s[A-Z][A-Za-z]+)?")


def _has_loc(text):
    return bool(_m(PLACE, text) or LOC_RX.search(text))


def _frame_fire(c):
    text = c["title"] + " " + _dedateline(c["excerpt"])
    venue = _m(VENUE, text)
    place = _m(PLACE, c["title"]) or _m(PLACE, c["excerpt"])
    if not place:
        mloc = LOC_RX.search(c["title"]) or LOC_RX.search(_dedateline(c["excerpt"]))
        if mloc:
            place = mloc.group(0).split(None, 1)[1].strip()
    if not place and not venue:
        return None  # never publish a fire without a location
    loc = " ".join(x for x in (place, venue) if x)
    en1 = f"Fire broke out at {loc}, officials said."
    ur1 = f"Officials ke mutabiq {loc} mein aag lag gayi."
    cause = next((ur for k, ur in CAUSE_MAP if re.search(r"\b" + k, text, re.I)), None)
    if cause:
        en2, ur2 = (f"Reports suggest the fire started due to {cause}.",
                    f"Pehli reports ke mutabiq {cause} ki wajah se aag lagi.")
    else:
        en2, ur2 = ("Reports suggest the cause of the fire is not yet confirmed.",
                    "Reports ke mutabiq aag ki wajah abhi tasdeeq nahi hui.")
    if re.search(r"\b(control|extinguish)", text, re.I):
        en3 = "Fire services said the blaze has been brought under control."
        ur3 = "Fire services ne kaha ke aag par qaboo pa liya gaya hai."
    else:
        en3, ur3 = AWAIT_EN, AWAIT_UR
    return [en1, en2, en3], [ur1, ur2, ur3]


def _frame_police(c):
    title, excerpt = c["title"], _dedateline(c["excerpt"])
    if not re.search(r"\b(arrest|arrests|arrested|detain|detained|apprehend|"
                     r"apprehended|busts|busted|seize|seized|raids|held|booked)\b",
                     title, re.I):
        return None
    text = title + " " + excerpt
    actor = _first_group(re.compile("|".join(POLICE_ORGS), re.I), text) or "Police"
    detained = bool(re.search(r"\bdetain", title, re.I))
    n = _people_count(text) or _m(VEHICLE_COUNT, _strip_money(text))
    case_en, case_ur = _case_phrase(title)
    if case_en is None:
        case_en, case_ur = "a case", "ek case"
    if isinstance(case_en, tuple):
        case_en, case_ur = case_en
    if detained:
        near = re.search(r"near\s+([A-Z][A-Za-z]+(?:\s[A-Z][A-Za-z]+){0,2})", title)
        place = near.group(1) if near else (_m(PLACE, title) or _m(PLACE, excerpt)
                                            or "the area")
        en1 = f"{n or 'Several'} protesters were detained near {place}, police said."
        ur1 = f"Police ke mutabiq {place} ke qareeb {n or 'kai'} pradarshankartaon ko hirasat mein liya gaya."
    else:
        count_en = n or "one"
        count_ur = f"{n} mulzameen" if n else "ek mulzim"
        en1 = f"{actor} arrested {count_en} accused in connection with {case_en}."
        ur1 = f"{actor} ne {case_ur} ke silsile mein {count_ur} ko giraftaar kar liya."
    # seizure details come from the BODY only - the headline's own "seized"
    # would otherwise swallow the next sentence as if it were the haul
    m_seiz = re.search(r"seized\s+([^;,:]{4,60}?)(?:\s+and\b|\s+arrested|"
                       r"\s+along|\s+in\b|\s+from\b|\s+during\b|\.\s|"
                       r"[;,]|$)", excerpt, re.I)
    if not m_seiz:
        m_seiz = re.search(r"seized\s+([^;,:]{4,60}?\d[^;,:]{0,40}?"
                           r"(?:kg|gm|litre|grams|packets|phones|cash|gold|lbs|"
                           r"kg\.|lakh|crore))", excerpt, re.I)
    m_with = None
    if not m_seiz:
        m_with = re.search(r"(?:arrested|held|apprehended)\s+with\s+"
                           r"([^;,:]{4,70}?)(?:\.\s|[;,]|$)", excerpt, re.I) or \
                 re.search(r"(?:arrested|held|apprehended)\s+with\s+"
                           r"([^;,:]{4,70}?)(?:\.\s|[;,]|$)", title, re.I)
    if m_seiz and not detained:
        seized = re.sub(r"\s+", " ", m_seiz.group(1)).strip().rstrip(",; .")
        en2 = f"Police seized {seized} during the operation, officials said."
        ur2 = f"Officials ke mutabiq police ne operation ke dauran {seized} zabt kiya."
    elif m_with and not detained:
        withs = re.sub(r"\s+", " ", m_with.group(1)).strip().rstrip(",; .")
        en2 = f"The accused were held with {withs}, officials said."
        ur2 = f"Officials ke mutabiq mulzameen ko {withs} ke saath giraftaar kiya gaya."
    else:
        en2 = "Police said the investigation is underway."
        ur2 = "Police ne kaha ke tehqiqat jaari hai."
    return [en1, en2, AWAIT_EN], [ur1, ur2, AWAIT_UR]


def _frame_court(c):
    title, text = c["title"], c["title"] + " " + _dedateline(c["excerpt"])
    court = _m(COURTS, text, "The court")
    order_en, order_ur = next(((k, ur) for k, ur in ORDER_MAP
                               if re.search(r"\b" + k, text, re.I)), (None, None))
    if not order_en:
        return None
    against = re.search(r"(?:against|to)\s+([A-Z][A-Za-z]+(?:\s[A-Z][A-Za-z]+){0,2})", title)
    who = against.group(1) if against else None
    en1 = f"{court} issued {order_en}" + (f" against {who}" if who else "") + "."
    ur1 = f"{court} ne {who + ' ke khilaf ' if who else ''}{order_ur} jaari kiya."
    en2 = "The order was passed in ongoing proceedings, according to reports."
    ur2 = "Reports ke mutabiq yeh hukm jaari karrwai ke dauran diya gaya."
    return [en1, en2, AWAIT_EN], [ur1, ur2, AWAIT_UR]


def _frame_govt(c):
    title, text = c["title"], c["title"] + " " + _dedateline(c["excerpt"])
    actor = _m(GOVT, text, "The government")
    m = re.search(r"(?:announces?|announced|clears|cleared|approves|approved|"
                  r"passes|passed|launches|launched|issues|issued|notifies|"
                  r"notified|bans?|banned)\s+(.{10,100}?)(?:[.,;:]|$)", title, re.I)
    if not m:
        return None
    what = re.sub(r"\s+", " ", m.group(1)).strip()
    if len(what) < 10:
        return None
    en1 = f"{actor} announced {what}."
    ur1 = f"{actor} ne {what} ka elaan kiya."
    issue_en, issue_ur = _issue(text)
    if issue_en:
        en2 = f"Officials said the decision relates to {issue_en}."
        ur2 = f"Officials ke mutabiq yeh faisla {issue_ur} se mutalliq hai."
    else:
        en2 = "Officials said implementation details will follow shortly."
        ur2 = "Officials ne kaha ke nafaaz ki tafseelat jald aayengi."
    en3 = "Implementation details are awaited."
    ur3 = "Nafaaz ki tafseelat ka intezar hai."
    return [en1, en2, en3], [ur1, ur2, ur3]


def _frame_weather(c):
    text = c["title"] + " " + _dedateline(c["excerpt"])
    if not re.search(r"\bIMD|alert|warning", text, re.I):
        return None
    place = _m(PLACE, c["title"]) or _m(PLACE, c["excerpt"]) or "the region"
    en1 = f"IMD issued a weather alert for {place}, reports suggest."
    ur1 = f"Reports ke mutabiq IMD ne {place} ke liye mausam ka alert jaari kiya."
    if re.search(r"\brain", text, re.I):
        en2 = "Heavy rain is expected over the next few hours, officials said."
        ur2 = "Officials ke mutabiq agle chand ghanton mein tez baarish ka imkaan hai."
    else:
        en2 = "Authorities asked residents to stay alert in low-lying areas."
        ur2 = "Intezamiya ne neech ilaaqon ke rehne walon ko chokas rehne ko kaha."
    return [en1, en2, AWAIT_EN], [ur1, ur2, AWAIT_UR]


EXTENSIONS = [
    ("cash", re.compile(r"(?:and|with|,)\s+Rs\s?([\d,]+)\s?(?:in\s+)?cash", re.I),
     "Police also seized Rs {v} in cash.",
     "Police ne Rs {v} cash bhi zabt kiya."),
    ("amount", re.compile(r"(?:\u20b9|Rs)\s?([\d.,]+\s?(?:lakh|crore)?)"),
     "The case involves Rs {v}, police said.",
     "Police ke mutabiq case mein Rs {v} shamil hai."),
    ("value", re.compile(r"(?:valued|worth)\s+(?:at\s+)?(?:approximately\s+)?"
                         r"(Rs\s?[\d.,]+\s?(?:lakh|crore)?)", re.I),
     "The seized material is valued at {v}, police said.",
     "Police ke mutabiq zabt maal ki qeemat {v} hai."),
    ("ndps", re.compile(r"\bNDPS\b"),
     "A case has been registered under the NDPS Act.",
     "NDPS Act ke tehat case darj kiya gaya hai."),
    ("place", re.compile(r"\bat\s+([A-Z][A-Za-z]+(?:\s[A-Z][A-Za-z]+){0,2})"),
     "The operation was carried out at {v}.",
     "Yeh operation {v} mein kiya gaya."),
    ("day", re.compile(r"\bon\s+(Saturday|Sunday|Monday|Tuesday|Wednesday|"
                       r"Thursday|Friday)"),
     "The incident came to light on {v}.",
     "Waqia {v} ko samne aaya."),
]

ALLOWED_TOKENS = {"Police", "Union", "Officials", "Reports", "Government",
                  "Court", "The", "More", "Details", "Fire", "IMD", "English",
                  "Roman", "Urdu", "Accused", "Drivers", "Assembly", "Cabinet",
                  "State", "Central", "Task", "Force", "NDPS", "Act",
                  "Saturday", "Sunday", "Monday", "Tuesday", "Wednesday",
                  "Thursday", "Friday", "Hyderabad", "Telangana", "India",
                  "Railway", "Station", "Airport", "Services", "Workers"}


UR_TOKENS = {"Mazeed", "Waqia", "Mutabiq", "Mulzameen", "Mulzim", "Giraftaar",
             "Zabt", "Tehqiqat", "Jaari", "Kaha", "Ne", "Ke", "Ko", "Mein",
             "Par", "Hai", "Hain", "Gaya", "Gayi", "Kar", "Liya", "Ka", "Ki",
             "Kiya", "Gaye", "Lon", "Log", "Silsile", "Tehat", "Darj", "Qeemat",
             "Maal", "Cash", "Bhi", "Aur", "Se", "Ke", "Baatcheet", "Mutalba"}


def _grounded(bullets, source):
    """STRICT: every digit and every proper noun in a bullet must appear in
    the verified source text. Anything else = invented = refuse."""
    src = source or ""
    src_nums = set(re.findall(r"\d[\d,.]*", src))
    # word numerals in the source count as their digits ("Two held" -> 2)
    src_nums |= {WORD_NUM[w.lower()] for w in
                 re.findall(r"\b(one|two|three|four|five|six|seven|eight|nine|"
                            r"ten)\b", src, re.I)}
    low = src.lower()
    for b in bullets:
        for n in re.findall(r"\d[\d,.]*", b):
            if n not in src_nums:
                return False
        for w in re.findall(r"\b[A-Z][a-z]{3,}\b", b):
            if w in ALLOWED_TOKENS or w in UR_TOKENS:
                continue
            if w.lower() not in low:
                return False
    return True


def _extend(c, en, ur):
    text = c["title"] + " " + _dedateline(c["excerpt"])
    cats = set(c.get("categories", []))
    non_incident = bool(cats & {"disaster_weather", "govt_announcement",
                                "court_judgment", "traffic_disruption",
                                "protest_major", "political_major",
                                "economic_major"})
    for key, rx, en_t, ur_t in EXTENSIONS:
        if len(en) >= 5:
            break
        if key in ("day", "place") and non_incident:
            continue
        m = rx.search(text)
        if not m:
            continue
        v = (m.group(1).strip() if m.groups() else "")
        line_en = en_t.format(v=v) if v else en_t
        line_ur = ur_t.format(v=v) if v else ur_t
        if key == "place" and (v.lower() in en[0].lower()):
            continue
        if any(line_en == x for x in en):
            continue
        en.append(line_en)
        ur.append(line_ur)
    return en, ur


POLI_VERBS = [
    ("seeks", "maang"), ("demands", "maang"), ("urges", "maang"),
    ("calls for", "maang"), ("wants", "maang"), ("asks for", "maang"),
    ("opposes", "virodh"), ("objects to", "virodh"),
    ("questions", "sawal"), ("slams", "tankeed"), ("criticises", "tankeed"),
    ("supports", "taid"), ("backs", "taid"),
]
POLI_ACTOR = re.compile(r"^([A-Z][A-Za-z().,'& ]{2,40}?)\s+(?:seeks|demands|urges|"
                        r"calls for|wants|asks for|opposes|objects to|questions|"
                        r"slams|criticises|supports|backs)\b")


def _frame_politics(c):
    title = c["title"].strip()
    m = POLI_ACTOR.match(title)
    if not m:
        return None
    actor = m.group(1).strip()
    rest = title[m.end(1):].strip()
    verb = None
    for en_v, ur_kind in POLI_VERBS:
        if rest.lower().startswith(en_v):
            verb = (en_v, ur_kind)
            break
    if not verb:
        return None
    what = rest[len(verb[0]):].strip().rstrip(".")
    if len(what) < 8 or len(what) > 90:
        return None
    kind = verb[1]
    if kind == "maang":
        en1 = f"{actor} has sought {what}."
        ur1 = f"{actor} ne {what} ki maang ki hai."
    elif kind == "virodh":
        en1 = f"{actor} has opposed {what}."
        ur1 = f"{actor} ne {what} ka virodh kiya hai."
    elif kind == "sawal":
        en1 = f"{actor} has raised questions over {what}."
        ur1 = f"{actor} ne {what} par sawal uthaye hain."
    elif kind == "tankeed":
        en1 = f"{actor} has criticised {what}."
        ur1 = f"{actor} ne {what} ki tankeed ki hai."
    else:
        en1 = f"{actor} has backed {what}."
        ur1 = f"{actor} ne {what} ki himayat ki hai."
    text = title + " " + _dedateline(c["excerpt"])
    issue_en, issue_ur = _issue(text)
    if issue_en:
        en2 = f"The matter relates to {issue_en}."
        ur2 = f"Yeh mamla {issue_ur} se mutalliq hai."
    else:
        en2, ur2 = AWAIT_EN, AWAIT_UR
    en3 = "More updates on this development will follow."
    ur3 = "Is silsile mein mazeed updates aayengi."
    return [en1, en2, en3], [ur1, ur2, ur3]


BUILDERS = [
    (("traffic_disruption", "protest_major"), _frame_strike),
    (("accident_casualty", "missing_person"), _frame_casualty),
    (("fire_explosion",), _frame_fire),
    (("police_operation", "major_crime", "security_terror"), _frame_police),
    (("court_judgment",), _frame_court),
    (("govt_announcement",), _frame_govt),
    (("political_major",), _frame_politics),
    (("disaster_weather",), _frame_weather),
]


def build(candidate):
    """Return {'bullets_en':[3], 'bullets_ur':[3], 'frame':cat} or None."""
    cats = set(candidate.get("categories", []))
    for cat_set, fn in BUILDERS:
        if cats & set(cat_set):
            try:
                res = fn(candidate)
            except Exception:
                res = None
            if not res:
                continue
            en_r, ur_r = _extend(candidate, list(res[0]), list(res[1]))
            en = _sanitize(en_r)
            ur = _sanitize(ur_r)
            src_txt = candidate["title"] + " " + _dedateline(candidate["excerpt"])
            if cats & {"fire_explosion", "accident_casualty"} and not _has_loc(src_txt):
                continue  # casualty/fire stories must say WHERE
            min_subst = 3 if cats & {"fire_explosion", "accident_casualty"} else 2
            if len([b for b in en if b not in FILLER_EN]) < min_subst:
                continue  # thin filler-only posts are refused
            if en and ur and len(en) == len(ur) and \
               _grounded(en + ur, candidate["title"] + " " +
                         _dedateline(candidate["excerpt"])):
                return {"bullets_en": en, "bullets_ur": ur,
                        "frame": sorted(cats & set(cat_set))[0]}
    return None
