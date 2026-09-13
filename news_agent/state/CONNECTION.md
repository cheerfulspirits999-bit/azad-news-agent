# Facebook connection status — ACTION NEEDED

**Page:** Azad Daily — `facebook.com/azaddaily` (share link `14oNZKNAB1j`)
**Checked:** 12 Sep 2026, ~4:45 PM IST

## Diagnosis of the token supplied on 12 Sep

| Probe | Result |
|---|---|
| `GET /me?fields=category` | error → token is a **USER** token, not a Page token |
| `GET /me/permissions` | only `public_profile` granted |
| `GET /me/accounts` | **empty** → token manages no Pages |
| `GET /azaddaily?fields=id` | blocked → app lacks Page Public Metadata Access |

**Verdict:** cannot publish. A Page post needs a **Page access token** (or a user
token that carries `pages_manage_posts` + `pages_read_engagement` +
`pages_show_list` and belongs to an admin/editor of Azad Daily).

The rejected token was deliberately NOT stored in `config.json`, so the
autonomous daemon keeps monitoring, verifying and packaging posts without
hitting Facebook (no error loop, nothing lost).

## How to get the right token (5 minutes)

1. Open https://developers.facebook.com/tools/explorer/ logged in as a
   Facebook account that is **admin or editor of Azad Daily**.
2. In the permissions list tick: `pages_show_list`, `pages_manage_posts`,
   `pages_read_engagement`.
3. Click **Generate Access Token**, accept the prompts, and when asked choose
   the Page **Azad Daily** (or press the "Get Page Access Token" button).
4. The token now shown (starts with `EAA…`) is the **Page token**: verify with
   `GET /me` — it must return Azad Daily's name, not your personal profile.
5. Paste that token here. The agent stores it in
   `news_agent/config.json → page.page_access_token`, resolves the numeric
   Page ID automatically via `/me`, and publishing starts on the next cycle
   (pending queue publishes first, max 2 posts per cycle).

Notes
* Graph API Explorer tokens are short-lived (hours). For a permanent setup,
  create a Meta Business app, add the same three permissions, and exchange for
  a long-lived Page token; paste that instead.
* If the token ever expires mid-operation, the daemon writes
  `state/ALERT.md`, stops, and asks for a fresh token — it never retries
  blindly and never posts without verification.
* Security: the previously pasted token is dead weight (public_profile only);
  you may revoke it in Meta Business Suite → Security. Do not paste tokens
  into public channels.
