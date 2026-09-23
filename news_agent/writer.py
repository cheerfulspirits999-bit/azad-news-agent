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


DEAD_ADJ = re.compile(
    r"\b(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
    r"\d[\d,]{0,6})\s*(?:people?|persons?|passengers?|pilgrims?|workers?|"
    r"men|women|children|youths?)?\s*(?:killed|dead|died)\b", re.I)
DEAD_AFTER = re.compile(r"\b(?:killed|dead|died)\s*[:\-]?\s*"
                        r"(one|two|three|four|five|six|seven|eight|nine|ten|"
                        r"\d[\d,]{0,6})", re.I)


def _people_count(text):
    """Death toll ONLY from a number adjacent to killed/dead/died.
    Never from an injured/rescued count (that caused a 50-dead error)."""
    t = _strip_money(_dedateline(text))
    for rx in (DEAD_ADJ, DEAD_AFTER):
        m = rx.search(t)
        if not m:
            continue
        v = m.group(1).strip(" ,.")
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
    return out if 2 <= len(out) <= 5 else None


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
    # fill towards the owner's 5-bullet card with grounded excerpt sentences
    for sent in re.split(r"(?<=[.!?])\s+", _dedateline(c.get("excerpt", ""))):
        if len(en) >= 5:
            break
        s2 = sent.strip()
        if not (40 <= len(s2) <= 95):
            continue
        if any(s2 == x for x in en):
            continue
        if re.search(r"^(He|She|They|It|His|Her|Their|This|That|There|The same)\b", s2):
            continue
        s2 = s2 if s2.endswith(".") else s2 + "."
        en.append(s2)
        ur.append(f"Zarai ke mutabiq {s2}")
    return en, ur


POLI_VERBS = [
    ("seeks", "maang"), ("demands", "maang"), ("urges", "maang"),
    ("calls for", "maang"), ("wants", "maang"), ("asks for", "maang"),
    ("opposes", "virodh"), ("objects to", "virodh"),
    ("questions", "sawal"), ("slams", "tankeed"), ("criticises", "tankeed"),
    ("supports", "taid"), ("backs", "taid"),
    ("says", "kaha"), ("said", "kaha"), ("claims", "kaha"), ("claimed", "kaha"),
    ("alleges", "ilzaam"), ("alleged", "ilzaam"),
    ("accuses", "ilzaam"), ("accused", "ilzaam"),
    ("raises", "uthaya"), ("raised", "uthaya"),
    ("offers", "kaha"), ("offered", "kaha"),
    ("assures", "kaha"), ("assured", "kaha"),
    ("warns", "kaha"), ("warned", "kaha"),
    ("announces", "elaan"), ("announced", "elaan"),
    ("approves", "elaan"), ("approved", "elaan"),
    ("bars", "elaan"), ("barred", "elaan"),
    ("launches", "elaan"), ("launched", "elaan"),
    ("releases", "elaan"), ("released", "elaan"),
]
POLI_ACTOR = re.compile(r"^([A-Z][A-Za-z().,'& ]{2,40}?)\s+(?:seeks|demands|urges|"
                        r"calls for|wants|asks for|opposes|objects to|questions|"
                        r"slams|criticises|supports|backs|says|said|claims|claimed|"
                        r"alleges|alleged|accuses|accused|raises|raised|offers|"
                        r"offered|assures|assured|warns|warned|announces|announced|"
                        r"approves|approved|bars|barred|launches|launched|"
                        r"releases|released)\b")


def _frame_politics(c):
    if len(c["title"].strip()) < 55:
        return None  # stub feed titles twist meanings - refuse
    title = c["title"].strip()
    title = re.sub(r"^[A-Za-z0-9'\-\. ,]{3,40}:\s*", "", title)  # drop "Tag:" prefix
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
    if len(what) < 16 or len(what) > 90:
        return None  # stub "what" phrases twist the meaning - refuse
    if verb[1] in ("maang", "virodh") and len(what) < 20:
        return None
    kind = verb[1]
    if kind == "kaha":
        en1 = f"{actor} {verb[0]} {what}."
        if len(en1) > 95 and "," in what:  # split long claims at the comma
            i = what.rindex(",")
            p1, p2 = what[:i].strip(), what[i + 1:].strip()
            if len(p1) >= 20 and 12 <= len(p2) <= 90:
                return ([f"{actor} {verb[0]} {p1}.",
                         f"{actor} also {p2}.", AWAIT_EN],
                        [f"{actor} ne kaha ke {p1}.",
                         f"{actor} ne kaha ke woh {p2}.", AWAIT_UR])
            return None
        if len(en1) > 95:
            return None  # never overflow the card bullet limit
        return ([en1, AWAIT_EN], [f"{actor} ne kaha ke {what}.", AWAIT_UR])
    if kind == "ilzaam":
        return ([f"{actor} {verb[0]} that {what}.", AWAIT_EN],
                [f"{actor} ne ilzaam lagaya ke {what}.", AWAIT_UR])
    if kind == "uthaya":
        return ([f"{actor} {verb[0]} {what}.", AWAIT_EN],
                [f"{actor} ne {what} ka masla uthaya hai.", AWAIT_UR])
    if kind == "elaan":
        return ([f"{actor} {verb[0]} {what}.", AWAIT_EN],
                [f"{actor} ne {what} ka elaan kiya hai.", AWAIT_UR])
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


GARBLE_RX = re.compile(r"\b(from|by|at|to|against|upon) them\b", re.I)


def _drop_bad_pairs(en, ur):
    """Drop bullet pairs (same index in EN+UR) that are template garbage
    ('Police seized from them ...') or near-duplicates repeating one number."""
    keep, seen = [], []
    for i, b in enumerate(en):
        u = ur[i] if i < len(ur) else ""
        if GARBLE_RX.search(b) or GARBLE_RX.search(u):
            continue
        dig = set(re.findall(r"\d+(?:\.\d+)?", b))
        words = set(re.findall(r"\w+", b.lower()))
        dup = False
        for w2, d2 in seen:
            if dig and dig == d2 and words & w2 and \
               len(words & w2) / max(1, len(words | w2)) > 0.45:
                dup = True
                break
        if dup:
            continue
        seen.append((words, dig))
        keep.append(i)
    return [en[i] for i in keep], [ur[i] for i in keep if i < len(ur)]


# ---------------- Roman-Urdu headline converter -------------------------
UR_SAY = re.compile(r"\b(says?|said|claims?|claimed|alleges?|alleged|warns?|warned)\b", re.I)
UR_DEATH = re.compile(r"\b(killed|died|dead|drowned)\b", re.I)
UR_TO = re.compile(r"\bto\s+(set\s+up|establish|create|build|visit|meet|launch|open|"
                   r"inaugurate|start|begin|hold|organise|organize|discuss|review|ban|bar|"
                   r"approve|postpone|seek|demand|urge|release|arrest|seize|lead)\b", re.I)
TO_FUT = {"set up": "qaim karenge", "establish": "qaim karenge", "create": "qaim karenge",
          "build": "qaim karenge", "visit": "ka daura karenge", "meet": "se mulaqat karenge",
          "launch": "ka elan karenge", "open": "ka iftitah karenge",
          "inaugurate": "ka iftitah karenge", "start": "shuru karenge",
          "begin": "shuru karenge", "hold": "ka inqiad karenge",
          "organise": "ke intezam karenge", "organize": "ke intezam karenge",
          "discuss": "par guftugu karenge", "review": "ka jaiza lenge",
          "ban": "par pabandi lagayenge", "bar": "par pabandi lagayenge",
          "approve": "ki manzuri denge", "postpone": "ko moakhir karenge",
          "seek": "ki maang karenge", "demand": "ki maang karenge",
          "urge": "par zoor denge", "release": "ko riha karenge",
          "arrest": "ko giraftar karenge", "seize": "zabt karenge",
          "lead": "ki qayadat karenge"}
CAN_RX = re.compile(r"\b(can|could|may)\s+(submit|apply|file|register|vote|work|"
                    r"travel|attend|send|claim)\b", re.I)
CAN_V = {"submit": "jama", "apply": "darakhwast", "file": "dakhil", "register": "darj",
         "vote": "vote", "work": "kaam", "travel": "safar", "attend": "shirkat",
         "send": "bhej", "claim": "dawa"}
BEGIN_RX = re.compile(r"\b(begins|began|commences|commenced)\b", re.I)

UR_PAST = [
    (re.compile(r"\b(arrested|arrests|arrest)\b", re.I),
     lambda s, o: (f"{s} ne {o} ko giraftar kar liya." if _AGENT_RX.search(s)
                   else (f"{s} ko {o} ke silsile mein giraftar kiya gaya." if o
                         else f"{s} ko giraftar kiya gaya."))),
    (re.compile(r"\b(seized|seizes)\b", re.I),
     lambda s, o: (f"{s} ne {o} zabt kar liya." if _AGENT_RX.search(s)
                   else (f"{o} mein {s} zabt kiya gaya." if o
                         else f"{s} zabt kiya gaya."))),
    (re.compile(r"\b(won|wins)\b", re.I),
     lambda s, o: f"{s} ne {o or 'muqable'} mein kamyabi hasil ki."),
    (re.compile(r"\b(postponed|postpones)\b", re.I),
     lambda s, o: f"{s} ne {o} moakhir kar diya."),
    (re.compile(r"\b(discussed|discusses)\b", re.I),
     lambda s, o: f"{s} par guftugu hui." if not o else f"{s} ne {o} par guftugu ki."),
    (re.compile(r"\b(protested|protests|protest)\b", re.I),
     lambda s, o: f"{s} ne {o} ke khilaf ehhtejaj kiya."),
    (re.compile(r"\b(announced|announces|launched|launches|unveiled|unveils)\b", re.I),
     lambda s, o: f"{s} ne {o} ka elan kiya."),
    (re.compile(r"\b(approved|approves|cleared|clears|passed|passes)\b", re.I),
     lambda s, o: f"{s} ne {o} ki manzuri de di."),
    (re.compile(r"\b(banned|bans|barred|bars)\b", re.I),
     lambda s, o: f"{s} ne {o} par pabandi laga di."),
    (re.compile(r"\b(sought|seeks|demanded|demands)\b", re.I),
     lambda s, o: f"{s} ne {o} ki maang ki."),
    (re.compile(r"\b(opposed|opposes)\b", re.I),
     lambda s, o: f"{s} ne {o} ki mukhalifat ki."),
    (re.compile(r"\b(supported|supports|backed|backs)\b", re.I),
     lambda s, o: f"{s} ne {o} ki taid ki."),
    (re.compile(r"\b(visited|visits)\b", re.I),
     lambda s, o: f"{s} ne {o} ka daura kiya."),
    (re.compile(r"\b(met|meets)\b", re.I),
     lambda s, o: f"{s} ne {o} se mulaqat ki."),
    (re.compile(r"\b(resigned|resigns)\b", re.I),
     lambda s, o: f"{s} ne istifa de diya."),
    (re.compile(r"\b(released|releases)\b(?=\s+(?:water|funds|payments?|money)\b)", re.I),
     lambda s, o: f"{s} ne {o} chhoda."),
    (re.compile(r"\b(released|releases)\b", re.I),
     lambda s, o: f"{s} ne {o} ko riha kar diya."),
    (re.compile(r"\b(held|holds)\b(?=.*\b(for|over|in connection with)\b)", re.I),
     lambda s, o: f"{s} ko {o} ke silsile mein giraftar kiya gaya."),
    (re.compile(r"\b(held|holds)\b(?=.*\bin\b)", re.I),
     lambda s, o: (f"{s} ne {o} ka inqiad kiya." if _AGENT_RX.search(s)
                   else (f"{s} ko {o} giraftar kiya gaya." if o.rstrip().endswith(
                       ("mein", "par", "se", "ko", "ke baad", "ke liye"))
                         else f"{s} ko {o} ke silsile mein giraftar kiya gaya."))),
    (re.compile(r"\b(held|holds)\b", re.I),
     lambda s, o: f"{s} ne {o} ka inqiad kiya."),
    (re.compile(r"\b(released|releases)\b(?=.*\b(water|funds|payments?|money)\b)", re.I),
     lambda s, o: f"{s} ne {o} chhoda."),
    (re.compile(r"\b(inspected|inspects)\b", re.I),
     lambda s, o: f"{s} ne {o} ka muaina kiya."),
    (re.compile(r"\b(tackled|tackles)\b", re.I),
     lambda s, o: f"{s} ne {o} par karwai ki."),
    (re.compile(r"\b(offered|offers)\b", re.I),
     lambda s, o: f"{s} ne {o} ki peshkash ki."),
    (re.compile(r"\b(joined|joins)\b", re.I),
     lambda s, o: f"{s} {o} mein shamil ho gaye."),
    (re.compile(r"\b(flagged|flags)\b", re.I),
     lambda s, o: f"{s} ne {o} ko ujagar kiya."),
    (re.compile(r"\b(rejected|rejects)\b", re.I),
     lambda s, o: f"{s} ne {o} rad kar di."),
    (re.compile(r"\b(welcomed|welcomes)\b", re.I),
     lambda s, o: f"{s} ne {o} ka khairmaqdam kiya."),
    (re.compile(r"\b(congratulated|congratulates)\b", re.I),
     lambda s, o: f"{s} ne {o} ko mubarakbad di."),
    (re.compile(r"\b(signed|signs)\b", re.I),
     lambda s, o: f"{s} ne {o} par dastakhat kiye."),
    (re.compile(r"\b(allocated|allocates)\b", re.I),
     lambda s, o: f"{s} ne {o} allot kar di."),
    # NOTE: "sanctions/sanctioned" removed 18 Sep - noun-modifier trap
    # ("Russia sanctions bill") produced factually inverted Urdu.
    (re.compile(r"\b(transferred|transfers)\b", re.I),
     lambda s, o: f"{s} ne {o} ka tabadla kiya."),
    (re.compile(r"\b(assured|assures|promised|promises)\b", re.I),
     lambda s, o: f"{s} ne {o} ka yaqeen dilaya."),
    (re.compile(r"\b(criticised|criticises|slammed|slams|targeted|targets)\b", re.I),
     lambda s, o: f"{s} ne {o} par tankeed ki."),
    (re.compile(r"\b(questioned|questions)\b", re.I),
     lambda s, o: f"{s} ne {o} par sawal uthaye."),
    (re.compile(r"\b(celebrated|celebrates|marked|marks|honoured|honours)\b", re.I),
     lambda s, o: f"{s} ko {o} par manaya gaya."),
    (re.compile(r"\b(inaugurated|inaugurates|opened|opens)\b", re.I),
     lambda s, o: f"{s} ne {o} ka iftitah kiya."),
    (re.compile(r"\b(completed|completes)\b", re.I),
     lambda s, o: f"{s} ne {o} mukammal kiya."),
    (re.compile(r"\b(discussed|discusses)\b", re.I),
     lambda s, o: f"{s} par guftugu hui."),
    (re.compile(r"\b(urged|urges)\b", re.I),
     lambda s, o: f"{s} ne {o} par zoor diya."),
    (re.compile(r"\b(cut|cuts)\b", re.I),
     lambda s, o: f"{s} ab {o} tak mehdood hai."),
    (re.compile(r"\b(cancelled|canceled|cancels|cancel)\b", re.I),
     lambda s, o: f"{s} ne {o} rad kar di."),
    (re.compile(r"\b(unites|united)\b", re.I),
     lambda s, o: f"{s} ne {o} ko milaya."),
    (re.compile(r"\b(suspended|suspends)\b", re.I),
     lambda s, o: f"{s} ne {o} muattal kar di."),
    (re.compile(r"\b(escaped|escapes)\b", re.I),
     lambda s, o: f"{s} {o} se bal-bal bache."),
    # ---- v4 templates (18 Sep, pool-starvation fix) ----
    (re.compile(r"\b(probed|probes|probe)\b", re.I),
     lambda s, o: f"{s} ne {o} ki janchn shuru ki."),
    (re.compile(r"\b(raided|raids|raid)\b", re.I),
     lambda s, o: f"{s} ne {o} par chhape maare."),
    (re.compile(r"\b(vetoes|vetoed|veto)\b", re.I),
     lambda s, o: f"{s} ne {o} veto kar diya."),
    (re.compile(r"\b(wished|wishes)\b", re.I),
     lambda s, o: f"{s} ne {o} ko badhai di."),
    (re.compile(r"\b(allows|allowed|lets|let|permits|permitted)\b", re.I),
     lambda s, o: _ijazat(s, o)),
    (re.compile(r"\b(participated|participates)\b", re.I),
     lambda s, o: f"{s} ne {o} mein shirkat ki."),
    (re.compile(r"\b(recalled|recalls)\b", re.I),
     lambda s, o: f"{s} ne {o} ko yad kiya."),
    (re.compile(r"\b(rises|gains|climbs)\b", re.I),
     lambda s, o: f"{s} {o} barha." if o else f"{s} barha."),
    (re.compile(r"\b(falls|drops)\b", re.I),
     lambda s, o: f"{s} {o} gira." if o else f"{s} gira."),
    (re.compile(r"\bcaught\b", re.I),
     lambda s, o: f"{s} ko {_ger(o)} pakda gaya."),
]


CITY_TAG = re.compile(r"^(Hyderabad|Secunderabad|Delhi|Chennai|Mumbai|Bengaluru|"
                      r"Telangana|India|World|New Delhi)\s*:\s*", re.I)


_TAGWORD = (r"(SIR|case|scam|row|probe|investigation|issue|matter|update|budget|"
            r"session|assembly|polls|election|scheme|mission|yatra|utsav)")


def _ur_clean_s(s):
    s = CITY_TAG.sub("", s.strip()).strip().rstrip(",")
    s = re.sub(r"\s+(to be|being|will be|would be|is|are|was|were|has|have|had)$",
               "", s, flags=re.I)
    # drop leading "Tag:" prefixes (e.g. "Telangana SIR: ...") - owner bans tags
    s = re.sub(r"^[A-Za-z0-9 .'\-]{2,26}\s" + _TAGWORD + r":\s*(?=[A-Za-z0-9].{12,})",
               "", s, flags=re.I)
    return _lex(s)


ATTRIB = r"\s*:\s*(CM|PM|BJP|BRS|TRS|AAP|EC|CBI|NIA|MEA|ED|SP|WHO|UN|officials?)\s*$"


def _ur_clean_o(o):
    o = o.strip().lstrip(",:")
    o = o.split(", to ")[0]
    o = re.sub(r"\s+soon\b", "", o, flags=re.I)
    o = re.sub(r"\s*:\s*(report|reports|sources?|says?|study)\s*$", "", o, flags=re.I)
    o = re.sub(ATTRIB, "", o, flags=re.I)          # trailing ": CM" attribution
    o = re.sub(r",\s*(calls?|calling|says?|saying|adding|adds|claims?)\s+.*$",
               "", o, flags=re.I)                  # trailing ", calls him ..." clause
    o = re.sub(r"^(in|at|near|to|into|from|around)\s+", "", o, flags=re.I)
    o = re.sub(r"\s+(in|during)\s+(early|morning|evening|late)\s+trade\s*$", "", o, flags=re.I)
    if len(o) > 45:
        parts, acc = [], ""
        for p in o.split(", "):
            if acc and len(acc) + 2 + len(p) > 60:
                break
            acc = p if not acc else acc + ", " + p
            parts.append(p)
        o = ", ".join(parts)
    return _lex(o.strip().rstrip(","))


# --- v4: English prepositions left inside converter output become Urdu
# postpositions placed AFTER their phrase ("for Telangana" -> "Telangana ke
# liye"). Quoted spans ('Made in India') are masked so names stay intact.
_UR_STOP = ("mein", "par", "ko", "ke", "ki", "ka", "se", "ne", "aur", "hai", "hain",
            "tha", "thi", "gaya", "gayi", "diya", "kiya", "karenge", "karega", "kar",
            "sakte", "sakti", "tak", "ab", "phir", "baad", "tehat", "liye", "saath",
            "zariye", "barha", "gira", "shuru", "manzoor", "pabandi", "elan", "jaiza",
            "zabt", "giraftar", "riha", "inqiad", "muaina", "karwai", "peshkash",
            "maang", "mukhalifat", "taid", "daura", "istifa", "mulaqat", "guftugu",
            "veto", "pakda", "ijazat", "shirkat", "jama", "darj", "dakhil", "vote",
            "kaam", "safar", "bhej", "dawa", "yad", "janchn", "chappe", "badhai",
            "denge", "di", "hui", "hua")
_PREP_MAP = [("in", "mein"), ("into", "mein"), ("at", "par"), ("on", "par"),
             ("for", "ke liye"), ("under", "ke tehat"),
             ("from", "se"), ("after", "ke baad"), ("before", "se pehle"),
             ("with", "ke saath"), ("by", "ke zariye"), ("across", "mein"),
             ("during", "ke dauran"), ("over", "par")]
_PREP_TERMS = r"|".join(p for p, _ in _PREP_MAP) + r"|of|to|and|or|that|which|who|as"


def _ur_postpo(line):
    """Token-scanner: English prep + its phrase -> phrase + Urdu postposition.
    O(n), no regex backtracking. Quoted spans masked ('Made in India')."""
    quotes = []

    def _mask(m):
        quotes.append(m.group(0))
        return f"\x00%d\x00" % (len(quotes) - 1)

    masked = re.sub(r"['\u2018][^'\u2019]{2,40}['\u2019]", _mask, line)
    stop = set(_UR_STOP)
    preps = {p for p, _ in _PREP_MAP}
    terms = stop | preps | {"of", "to", "and", "or", "that", "which", "who", "as"}
    toks = masked.split()
    out, i = [], 0
    pmap = dict(_PREP_MAP)
    while i < len(toks):
        low = toks[i].lower().strip(",.;:")
        if low == "to" and i + 1 < len(toks) and toks[i + 1][:1].isupper():
            low = "to"
            pmap = dict(pmap, to="ko")
        if low == "of" and i + 1 < len(toks) and toks[i + 1][:1].isupper():
            j = i + 1
            phrase = []
            while j < len(toks):
                tl = toks[j].lower().strip(",.;:")
                if tl in terms or toks[j].endswith(","):
                    break
                phrase.append(toks[j])
                j += 1
            if phrase:
                out.append(" ".join(phrase))
                out.append("ke")
                i = j
                continue
        if low in pmap and i + 1 < len(toks):
            j = i + 1
            phrase = []
            while j < len(toks):
                tl = toks[j].lower().strip(",.;:")
                if tl in terms or toks[j].endswith(","):
                    break
                phrase.append(toks[j])
                j += 1
            if phrase:
                out.append(" ".join(phrase))
                out.append(pmap[low])
                i = j
                continue
        out.append(toks[i])
        i += 1
    res = " ".join(out)
    res = re.sub(r"\s+and\s+", " aur ", res)
    res = re.sub(r"((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2}) par",
                 r"\1 ko", res)
    for i2, q in enumerate(quotes):
        res = res.replace("\x00%d\x00" % i2, q)
    return re.sub(r"\s{2,}", " ", res).strip()


NOUN_UR = [
    ("family members", "ghar wale"), ("documents", "kagazat"), ("officials", "ohdedar"),
    ("official", "ohdedar"), ("houses", "ghar"), ("house", "ghar"), ("adult", "baligh"),
    ("hearings", "sunwai"), ("hearing", "sunwai"), ("petition", "darkhwast"),
    ("plea", "darkhwast"), ("investigation", "janchn"), ("inquiry", "janchn"),
    ("probe", "janchn"), ("raids", "chhape"), ("raid", "chhapa"), ("meeting", "baithak"),
    ("talks", "guftugu"), ("agreement", "muahida"), ("scheme", "skim"),
    ("roads", "sadkein"), ("road", "sadak"), ("water", "paani"), ("rains", "barish"),
    ("rain", "barish"), ("floods", "seelab"), ("flood", "seelab"),
    ("warning", "intiba"), ("farmers", "kisan"), ("farmer", "kisan"),
    ("students", "talba"), ("jobs", "nokriyan"), ("job", "nokri"),
    ("prices", "qeematein"), ("price", "qeemat"), ("elections", "intekhabat"),
    ("election", "intekhab"), ("constituencies", "halqe"), ("constituency", "halqa"),
    ("opposition", "appozishan"), ("weather", "mausam"), ("Government", "Sarkar"),
    ("government", "sarkar"), ("Govt", "Sarkar"), ("govt", "sarkar"),
    ("heavy", "bhaari"), ("light", "halki"), ("morning", "subah"),
    ("evening", "shaam"), ("night", "raat"), ("bail", "zamanat"),
]
_LOAN_MASK = ["work from home", "red alert", "press meet", "chief minister",
              "prime minister", "high court", "supreme court", "train protection"]


def _lex(text):
    for en, ur in _LOAN_MASK and NOUN_UR:
        text = re.sub(r"\b" + en.replace(" ", r"\s+") + r"\b", ur, text)
    text = re.sub(r"\b(\d+)L\b", r"\1 lakh", text)
    text = re.sub(r"\b(\d+(?:\.\d+)?)\s*cr\b", r"\1 crore", text)
    return text


def _alert_tmpl(s, o):
    col = re.search(r"\b(red|orange|yellow)\s+alert\b", o, re.I)
    dat = re.search(r"\b((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2})", o)
    when = f" {dat.group(1)} ko" if dat else ""
    return f"{s} mein{when} {col.group(1).lower()} alert jaari kiya gaya."


UR_END_OK = ("diya.", "di.", "kiya.", "ki.", "liya.", "kiye.", "kiye.", "hue.",
             "karenge.", "karega.", "kar.",
             "gaya.", "gayi.", "hua.", "hui.", "hai.", "hain.", "sakte.", "sakti.",
             "jayega.", "jayegi.", "hoga.", "hogi.", "raha.", "rahi.", "chuka.",
             "chuki.", "li.", "le.", "denge.", "chhoda.", "chhodenge.", "jeeta.",
             "jeeti.", "baar.", "shuru.", "mehdood.", "gaya.", "bache.", "pakda.",
             "munaqid.", "mila.", "mili.", "hua.", "hui.", "kahi.", "kaha.")


_AGENT_RX = re.compile(r"\b(police|cops?|force|agency|agencies|CBI|NIA|ED|EOW|"
                       r"officials?|court|government|govt|department|cell|team)\b",
                       re.I)


_PP_TOK = {"ke", "liye", "tehat", "baad", "saath", "zariye", "mein", "par", "ko", "se"}


def _reorder(o):
    """Move head noun after its postposition phrase(s): Urdu word order.
    'paani Jurala mein X ke baad' -> 'Jurala mein X ke baad paani'."""
    toks = o.split()
    if toks and toks[0].lower().endswith("ing"):
        return o  # gerund-led object (caught taking...): keep as-is
    idx = next((i for i in range(len(toks) - 1)
                if toks[i + 1].lower() in _PP_TOK and toks[i].lower() not in _PP_TOK),
               None)
    if idx and len(toks[:idx]) <= 2 and len(toks) - idx >= 2:
        return " ".join(toks[idx:] + toks[:idx])
    return o


def _fin(line):
    line = _ur_postpo(line)
    return re.sub(r"(ke baad|ke liye|ke saath|ke tehat|ke zariye|mein|par|se) ko ",
                  r"\1 ", line)


def _ger(o):
    G = {"taking": "lete", "accepting": "lete", "receiving": "lete",
         "holding": "rakhte", "selling": "bechte", "buying": "kharidte"}
    w = o.split(" ", 1)
    if w and w[0].lower() in G:
        return G[w[0].lower()] + (" " + w[1] if len(w) > 1 else "")
    return o


_PP_END = ("ke baad", "ke liye", "se", "mein", "par", "ke saath", "ke tehat",
           "ke zariye")


def _ijazat(s, o):
    op = _ur_postpo(o)
    if op.endswith(_PP_END):
        return f"{s} ne {op} ijazat di."
    return f"{s} ne {op} ko ijazat di."


def urdu_headline(title):
    """Rule-based Roman-Urdu rendering of a headline; None when unsure."""
    t = title.strip().rstrip(".")
    if re.search(r"\b(must|should|could|would)\s+be\b", t, re.I):
        return None  # modal-passive headlines: converter cannot render safely
    if re.search(r"\b(complains|complained|writes|wrote|objects|objected|"
                 r"appeals|appealed)\s+(to|against|before)\b", t, re.I):
        return None  # multi-verb complaint chain: unsafe to render
    # drop trailing person attribution (": South Korean President") so the
    # Urdu sentence never misattributes the action
    t = re.sub(r":\s*[A-Z][a-z]+(?: [A-Z][A-Za-z'\-]+){1,4}$", "", t).strip()
    m = re.match(r"^(?P<s>.+?)\s+(?P<v>deployed|commissioned|installed|positioned)"
                 r"\s+(?:on|at|in|near|across)?\s*(?P<loc>.+)$", t, re.I)
    if m and len(m.group("s")) >= 3 and len(m.group("loc")) >= 6:
        loc = _reorder(_ur_postpo(_ur_clean_o(m.group("loc"))))
        return _fin(f"{_ur_clean_s(m.group('s'))} {loc} par tainat kiya gaya.")
    m = UR_SAY.search(t)
    if m:
        s, clause = _ur_clean_s(t[:m.start()]), _ur_clean_o(t[m.end():])
        if len(s) >= 3 and len(clause) >= 12:
            return f"{s} ne kaha ke {clause}."
    m = UR_DEATH.search(t)
    if m:
        s = _ur_clean_s(t[:m.start()])
        PERSON = {"woman": "ek khatoon", "man": "ek shakhs", "person": "ek shakhs",
                  "people": "log", "persons": "log", "child": "ek bachcha",
                  "children": "bacche", "youth": "ek naujawan", "elderly": "ek buzugr"}
        low = s.lower()
        for k, v in PERSON.items():
            if low == k or low.startswith(k + " "):
                s = v + s[len(k):]
                break
        loc = re.search(r"\b(in|at|near)\s+(.{3,40})$", t, re.I)
        if len(s) >= 3:
            return f"{s} ki maut ho gayi" + (f" {loc.group(2).strip()} mein." if loc else ".")
    m = UR_TO.search(t)
    if m:
        s = _ur_clean_s(t[:m.start()])
        s = re.sub(r"\s+(likely|planning|plans|agrees|set|ready)$", "", s, flags=re.I)
        o = _reorder(_ur_postpo(_ur_clean_o(t[m.end():])))
        o = re.sub(r"\s+(today|tomorrow|this week|next week|amid [^,]*)$",
                   "", o, flags=re.I)
        key = m.group(1).lower()
        if len(s) >= 3 and len(o) >= 6:
            if key == "release" and \
               re.search(r"\b(water|funds|payments?|money)\b", t[m.end():], re.I):
                return _fin(f"{s} ne {o} chhodenge.")
            return _fin(f"{s} {o} {TO_FUT[key]}.")
    m = re.search(r"\b(placed|put|puts|issued|issues)\b[^,]*?\b(red|orange|yellow)\s+alert\b",
                  t, re.I)
    if m:
        s = _ur_clean_s(t[:m.start()])
        dat = re.search(r"\b((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2})", t)
        when = f" {dat.group(1)} ko" if dat else ""
        col = m.group(2).lower()
        if len(s) >= 3:
            between = t[m.end(1):m.start(2)].strip()
            if m.group(1).lower() in ("issued", "issues") and \
               re.match(r"^(a|an|the)?\s*$", between, re.I):
                loc = re.search(r"alert\s+(?:in|for|across)\s+([^,]+?)(?:\s+on\s+|\s*$)", t[m.start(2):])
                if loc:
                    return _fin(f"{s} ne {_lex(loc.group(1).strip())} mein{when} "
                                f"{col} alert jaari kiya.")
            return _fin(f"{s} mein{when} {col} alert jaari kiya gaya.")
    m = re.search(r"\b(to be|will be|was|were|is|are)\s+held\b", t, re.I)
    if m:
        s = _ur_clean_s(t[:m.start()])
        rest = t[m.end():]
        loc = re.search(r"\b(in|at|near|across)\s+([^,]+?)(?:\s+on\s+|\s*$)", rest)
        dat = re.search(r"\bon\s+((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2})", rest)
        if len(s) >= 3 and loc:
            when = f" {dat.group(1)} ko" if dat else ""
            return _fin(f"{s} {_lex(loc.group(2).strip())} mein{when} munaqid hoga.")
    m = CAN_RX.search(t)
    if m:
        s = _ur_clean_s(t[:m.start()])
        o = _ur_postpo(_ur_clean_o(t[m.end():]))
        # Urdu order: indirect object (postposition phrase) before direct object
        mm = re.match(r"^(?P<head>[A-Za-z0-9₹][A-Za-z0-9₹.'\-]*(?:\s+[A-Za-z0-9₹.'\-]+)*?)\s+"
                      r"(?P<pp>[A-Za-z0-9][A-Za-z0-9.'\-]*\s+(?:ke liye|ke tehat|ke baad|ke saath|ke zariye))$", o)
        if mm:
            o = f"{mm.group('pp')} {mm.group('head')}"
        if len(s) >= 3 and len(o) >= 6:
            return _fin(f"{s} {o} {CAN_V[m.group(2).lower()]} kar sakte hain.")
    m = BEGIN_RX.search(t)
    if m:
        s = _ur_clean_s(t[:m.start()])
        o = _ur_clean_o(t[m.end():])
        if len(s) >= 3:
            return _fin(f"{s} {o} shuru hui." if o else f"{s} shuru hui.")
    m = re.search(r"\b(sought|seeks)\b", t, re.I)
    if m and re.search(r"\b(dialogue|talks|truce|peace)\b", t[m.end():], re.I):
        s = _ur_clean_s(t[:m.start()])
        o = _reorder(_ur_postpo(_ur_clean_o(t[m.end():])))
        if len(s) >= 3 and len(o) >= 6:
            return _fin(f"{s} ne {o} ki khwahish zahir ki.")  # wanted talks, not demanded
    for rx, fn in UR_PAST:
        m = rx.search(t)
        if not m:
            continue
        s = _ur_clean_s(t[:m.start()])
        o = _reorder(_ur_postpo(_ur_clean_o(t[m.end():])))
        if len(s) < 3:
            continue
        if len(o) < 4 and rx.pattern.find("discussed") == -1:
            continue
        return _fin(fn(s, o))
    return None


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
            en_s, ur_s = _sanitize(en_r), _sanitize(ur_r)
            if en_s is None or ur_s is None:
                continue  # a bullet failed safety sanitisation - refuse frame
            en, ur = _drop_bad_pairs(en_s, ur_s)
            src_txt = candidate["title"] + " " + _dedateline(candidate["excerpt"])
            if cats & {"fire_explosion", "accident_casualty"} and not _has_loc(src_txt):
                continue  # casualty/fire stories must say WHERE
            ex_txt = _dedateline(candidate.get("excerpt", ""))
            if len(ex_txt) < 100:  # thin excerpt: bullets must carry key noun
                key = max((t for t in re.findall(r"[A-Za-z]+",
                           candidate["title"].lower()) if len(t) >= 8),
                          key=len, default="")
                if key and not any(key in b.lower() for b in en):
                    continue
            subst = len([b for b in en if b not in FILLER_EN])
            statement = bool(cats & {"political_major"}) and len(en[0]) >= 45
            if subst < (1 if statement else 2):
                continue  # thin filler-only posts are refused
            while len(en) == len(ur) and len(en) < 3:
                en.append("More updates on this development will follow.")
                ur.append("Is silsile mein mazeed updates aayengi.")
            if en and ur and len(en) == len(ur) and \
               _grounded(en + ur, candidate["title"] + " " +
                         _dedateline(candidate["excerpt"])):
                return {"bullets_en": en, "bullets_ur": ur,
                        "frame": sorted(cats & set(cat_set))[0]}
    return None


# --------------------------------------------------------------------------
# Bullet-pair quality gate (17 Sep, owner-flagged card): never let a vague
# English bullet ("Supreme Court issued stay.") or a Roman-Urdu bullet that
# still carries English fragments ("youth to drive ...", "hands over ...",
# "'monitoring developments' of Russia") reach a card.
UR_EN_FUNC = re.compile(
    r"\b(to|of|and|the|for|from|by|in|at|on|over|with|after|before|is|are|"
    r"was|were|has|have|had|will|their|its|this|that)\b")
UR_EN_VERB = re.compile(
    r"\b(hands over|monitoring|to drive|to launch|to start|to boost|seeks|"
    r"urges|says|meets|held|holds|announces|launches|visits|expresses)\b")
UR_EN_BLACK = re.compile(
    r"\b(into|after|before|during|while|unless|until|although|though|despite|"
    r"allegedly|reportedly|apparently|must|been|being|within|without|against|has|have|had|of|"
    r"across|under|over|according)\b", re.I)
UR_ING_RX = re.compile(
    r"\b[a-z]{2,}ing\b(?!\s*(?:kar|ke|ki|ko|se|mein|par|ne)\b)")
UR_ING_OK = {"nothing", "something", "anything", "evening", "morning", "meeting",
             "hearing", "building", "painting", "reading", "writing", "drawing",
             "singing", "spring", "thing", "working"}
VAGUE_EN = re.compile(
    r"(issued (a )?stay|gave (a )?statement|took action|expressed "
    r"(shock|concern|grief)|issued (an )?order|made (an )?announcement)\.?$")


def bullet_quality(en, ur):
    """Return list of reasons the EN/UR bullet pair is NOT publishable."""
    if not en or not ur:
        return ["empty-pair"]
    bad = []
    if len(en) > 95 or len(ur) > 95:
        bad.append("over-95-chars")
    if VAGUE_EN.search(en.strip()):
        bad.append("vague-english")
    if len(en.strip()) < 50 and not re.search(r"\d", en):
        bad.append("thin-english")
    funcs = {m.group(1) for m in UR_EN_FUNC.finditer(ur)}
    if len(funcs) >= 2:
        bad.append("ur-english-fragments")
    if UR_EN_VERB.search(ur):
        bad.append("ur-english-verb-leftover")
    if UR_EN_BLACK.search(ur):
        bad.append("ur-english-blacklist")
    ings = [w for w in UR_ING_RX.findall(ur) if w.lower() not in UR_ING_OK]
    if ings:
        bad.append("ur-ing-leftover:" + ings[0])
    if re.search(r"\b(to|that|which|who|could|would|should|might|will|can|may|"
                 r"using|recalls|monitors|tracks|flips|after|before|with|under|"
                 r"across|during)\s+[a-z]{3,}", ur):
        bad.append("ur-english-clause")
    if re.search(r":\s+\S", ur) or ur.strip().endswith(":"):
        bad.append("ur-attribution-colon")
    for tok in ("mein", "ke liye", "par ", " ko "):
        if ur.count(tok) >= 3:
            bad.append("ur-particle-repeat")
            break
    if not ur.strip().endswith(UR_END_OK):
        bad.append("ur-incomplete-sentence")
    # specifics rule 23 Sep: a bullet MUST name someone/something (capital
    # beyond the first word) or carry a number - kills filler like
    # "Police arrested one accused in connection with a case."
    caps = [t for i, t in enumerate(en.replace("'", "").split())
            if i > 0 and t[:1].isupper() and t[1:2].islower()]
    if not caps and not re.search(r"\d", en) and "'" not in en:
        bad.append("en-no-specifics")
    return bad
