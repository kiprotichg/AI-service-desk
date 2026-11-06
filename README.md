# Ideal Dynamics Assistant — AI chat for the Service and Sales modules

An AI chat assistant, branded with the Ideal Dynamics logo, with a ChatGPT-style
interface and three **answer modes**, switched at the top of the chat:
- **Service** answers questions about the **Service module** (service requests,
  technician scheduling, job cards, spare parts, warranties, contracts and SLAs,
  preventive maintenance, service billing, reports) from its knowledge base, and
  **escalates to a human ticket** when needed.
- **Sales** answers questions about the **Sales module** (quotations, sales orders,
  invoices, credit notes, pricing, customers, reports) from its own knowledge base.
- **General** answers any question. Every message is tagged with **intent** and **sentiment**. People can attach
**photos and files** (images, PDFs, text) for the AI to read. A collapsible sidebar
holds **New chat** and **Recent chats**, and each chat can be deleted. Agents manage
tickets and see analytics.

- **Backend:** Python, Flask, Flask-SQLAlchemy (SQLite), Claude or Gemini API
- **Frontend:** React 18 + Vite

## Project structure

```
backend/
  run.py                    # entry point (loads .env, starts Flask on :5000)
  requirements.txt
  .env.example
  app/
    __init__.py             # create_app() factory: config, DB, CORS, blueprints, seeding
    config.py               # Config / TestConfig
    extensions.py           # SQLAlchemy instance
    auth.py                 # signed agent + customer tokens, @agent_required
    models.py               # Agent, Conversation, Message, Attachment, Ticket, FAQ
    seed.py                 # default FAQ articles + optional first agent
    routes/
      auth.py               # /api/auth/login, /api/auth/me
      chat.py               # /api/conversations ... (send with files, delete chat)
      files.py              # /api/attachments/<id> (download an uploaded file)
      tickets.py            # /api/tickets ...
      faqs.py               # /api/faqs ...
      analytics.py          # /api/analytics, /api/health
    services/
      ai_service.py         # Claude / Gemini calls (structured JSON) + offline fallback
      knowledge_base.py     # keyword FAQ search used to ground replies
      attachments.py        # upload checks (type sniffing, size limits), storage
  tests/
    test_api.py             # API, validation and auth tests
    test_ai_providers.py    # Claude + Gemini paths with faked SDK clients
    test_uploads.py         # uploads, delete chat, titles, DB upgrade
    test_modes.py           # Service vs General answer modes
    test_sales.py           # Sales mode: own knowledge base, prompt, tickets
frontend/
  vite.config.js            # proxies /api -> Flask in dev
  src/
    App.jsx                 # layout: sidebar + chat / tickets / analytics
    api/client.js           # fetch wrapper; agent + chat tokens, file uploads
    lib/storage.js          # recent chats, sidebar, settings, theme (localStorage)
    lib/files.js            # client-side file checks, image URL cache
    components/
      Sidebar.jsx           # toggle, New chat, Recent chats, delete, agent tools
      ModeSwitch.jsx        # Service / Sales / General switch at the top of the chat
      Logo.jsx              # Ideal Dynamics logo (src/assets/logo.png)
      ThemeToggle.jsx       # light / dark button (top right)
      ProfileMenu.jsx       # profile button + dropdown: Settings, Sign out
      SettingsDialog.jsx    # name, appearance, default mode, delete all chats
      ChatView.jsx          # one conversation: load, send, drag & drop, banner
      Composer.jsx          # auto-growing input, attach button, file previews
      Message.jsx           # user bubble / assistant reply with copy button
      Attachment.jsx        # photo thumbnail or file download chip
      Icons.jsx             # inline SVG icons
      LoginForm.jsx         # agent sign-in (guards Tickets + Analytics)
      TicketsPanel.jsx      # filter + change status/priority
      AnalyticsPanel.jsx    # stat cards + bar charts
    styles.css              # ChatGPT-style light/dark theme
```

## Running it

Backend (terminal 1):

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env          # set AI_PROVIDER + its key, SECRET_KEY, AGENT_PASSWORD
python run.py
```

The agent account in `AGENT_USERNAME` / `AGENT_PASSWORD` is created on first start.
To add more agents, or reset a password:

```bash
flask --app run create-agent alice
```

Frontend (terminal 2):

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173. The header shows which AI is answering. With no key for the
chosen provider, the bot runs in **offline mode** (keyword matching).

Tests: `cd backend && python -m pytest -q`

> Windows tip: if `pip install` fails with *"filename or extension is too long"*, move the
> project to a short path such as `C:\dev\ai-service` or enable Windows long paths.

## API

**Access:** *public*; *agent* = `Authorization: Bearer <token>` from login;
*customer* = `X-Conversation-Token` returned when that chat was started.

| Method | Path | Access | Purpose |
|---|---|---|---|
| GET | `/api/health` | public | Status + AI mode (`claude` / `gemini` / `offline`) |
| GET | `/api/config` | public | Help desk contact `{helpdesk: {name, whatsapp, hours}}` |
| POST | `/api/auth/login` | public | `{username, password}` → `{token, agent}` |
| GET | `/api/auth/me` | agent | Current agent |
| POST | `/api/conversations` | public | Start a chat `{customer_name, mode}` → includes `token` |
| GET | `/api/conversations` | agent | List chats |
| GET | `/api/conversations/<id>` | customer or agent | Chat with messages |
| POST | `/api/conversations/<id>/messages` | customer | Send `{content, mode}` as JSON, or multipart `content`, `mode` + up to 3 `files` → AI reply, tags, optional ticket |
| POST | `/api/conversations/<id>/messages/<msg_id>/regenerate` | customer | Replace the latest reply (`{mode}` optional) |
| PATCH | `/api/conversations/<id>/messages/<msg_id>` | customer | Edit your message (`{content, mode}`); later messages are replaced |
| DELETE | `/api/conversations/<id>` | customer or agent | Delete the chat, its messages and files (tickets are kept) |
| GET | `/api/attachments/<id>` | customer or agent | Download an uploaded file |
| POST | `/api/conversations/<id>/close` | customer or agent | Close a chat |
| GET | `/api/tickets?status=` | agent | List tickets |
| PATCH | `/api/tickets/<id>` | agent | Update `{status, priority, serial_number, customer_site}` |
| GET | `/api/faqs?module=service\|sales` | public | List the knowledge base (all, or one module) |
| POST/DELETE | `/api/faqs[/<id>]` | agent | Add (`{question, answer, keywords, module}`) or delete an article |
| GET | `/api/analytics` | agent | Counts, intents, sentiment, escalation rate |

## Answer modes: Service, Sales and General

| | Service | Sales | General |
|---|---|---|---|
| Answers | Service module: service requests, technicians, job cards, parts, warranties, contracts, maintenance, billing, reports | Sales module: quotations, sales orders, invoices, credit notes, pricing, customers, reports | Anything |
| Knowledge base | Service articles | Sales articles | None |
| Off-topic questions | Declined, with a pointer to the right mode | Declined, with a pointer to the right mode | Service or Sales questions get a pointer to that mode |
| Opens tickets | Yes, tagged **Service** | Yes, tagged **Sales** | Never (enforced on the server) |
| AI instructions | `SERVICE_PROMPT` | `SALES_PROMPT` | `GENERAL_PROMPT` |

**Replace the sample articles with your real documentation.** The 8 Service and 8
Sales articles in `backend/app/seed.py` are generic samples about a typical ERP
Service and Sales module. They are not the actual Ideal Dynamics screens or menu
names. Agents can add real articles with `POST /api/faqs` and `"module": "service"`
or `"sales"`, or you can edit `seed.py` on a fresh database. Both assistants are told
never to invent menu names or features. When the articles don't cover something,
they explain the general idea and say the exact steps in your setup may differ.

Upgrading from the earlier online-store version: on startup, the 6 old store
articles (only unedited copies) are removed and the Service module articles are
added. Articles an agent wrote are never removed. Note that if any custom Service
articles exist, the sample ones aren't added. Set `PRODUCT_NAME` in `.env` to
change the product name it uses (default "Ideal Dynamics").

Tickets show their module (Service or Sales) on the Tickets page.

### Equipment serial number and customer site (Service tickets)

- **What the assistant does:** in Service mode it picks up the **equipment serial
  number** and the **customer site** (the location or branch where the equipment
  is) from anywhere in the chat, copied exactly as written. It never guesses one.
- **Missing details:** if a ticket is open and either is missing, the assistant asks
  for it, and the user's next reply fills it in on the same ticket.
- **Without the AI (offline mode):** it recognises explicit forms such as
  `serial no: AB-1234`, `S/N 99812` and `site: Nairobi West branch`, and adds the
  question for anything still missing.
- **On the Tickets page:** agents see and can edit both, in the Equipment serial and
  Customer site columns. Missing values show as a dashed "Missing" box. Sales
  tickets don't use them.
- **API:** `PATCH /api/tickets/<id>` accepts `serial_number` (max 100 characters) and
  `customer_site` (max 200).

- **Switching:** the switch at the top can be changed at any time, even in the middle
  of a chat. Each message is answered in the mode selected when it's sent, and the
  chat remembers the last mode used.
- **Default:** new chats start in the default mode set in Settings (Service unless you
  change it).
- **Analytics:** a "Chat modes" chart shows how many chats use each mode.

## Profile, settings and theme

- **Profile button** (top right): the dropdown shows your name, **Settings**, and
  **Sign out** when an agent is signed in, or **Agent sign in** otherwise.
- **Settings:**
  - your name, which is used for new chats and the profile initial
  - appearance (System, Light or Dark)
  - the default mode for new chats
  - **Delete all chats**
  Changes save immediately in this browser.
- **Sun/moon button:** switches between light and dark. "System" in Settings follows
  your computer's setting. The theme is applied before the page draws, so it doesn't
  flash.

## WhatsApp help desk button

A green **Help desk** button sits at the bottom right of every page. It opens a small
panel with an editable message, and **Open WhatsApp** starts a chat with the person in
charge of the ICT help desk, in the WhatsApp app or WhatsApp Web.
- It uses WhatsApp's official click-to-chat link (`https://wa.me/<number>?text=...`),
  so there are no API keys, no WhatsApp Business account and no cost.
- Nothing is sent automatically: the user presses send in WhatsApp.

Set it up in `backend/.env`, then restart the backend:

```
HELPDESK_WHATSAPP_NUMBER=+254 712 345 678   # with the country code; spaces/dashes are fine
HELPDESK_NAME=ICT Help Desk                 # shown on the panel
HELPDESK_HOURS=Mon-Fri, 8am-5pm             # optional
```

- **No number set:** the panel says the help desk hasn't been set up yet.
- **Local number:** a number without the country code (starting with 0) is rejected
  with a warning in the backend log, because wa.me links need the full international
  number.
- **API:** the page reads these settings from `GET /api/config`, which is public,
  because the number is meant to be contacted.

## Copy, Regenerate and Edit (ChatGPT-style message actions)

- **Copy:** under every reply, and on hover under your own messages. It falls back to
  an older copy method if the browser refuses clipboard access.
- **Regenerate:** under the latest reply. It replaces that reply with a fresh answer
  to the same question, in the mode currently selected at the top, so you can also
  re-ask in a different mode.
- **Edit:** the pencil on hover over any of your messages. Change the text and press
  Send (or Enter). Escape cancels.
  - Everything after that message is removed, including files uploaded later, and
    the assistant answers the edited message.
  - Photos and files attached to the edited message itself are kept and sent again.
  - Editing your first message also renames the chat.
- **On failure:** if either request fails, the chat is put back exactly as it was.
- **Tickets:** tickets that earlier replies opened are kept, so agents don't lose them.

## Chat tools (Claude-style)

**+ menu** (left of the message box):
- **Upload or add files** (Ctrl+U).
- **Take a screenshot:** the browser asks which screen, window or tab to capture; one
  picture is attached. PNG, or JPEG if it would be over the 5 MB limit.
- **Add to project:** puts the chat in a project, or picks one before the first message.
- **Skills:** picks the Service, Sales or General assistant (the same as the switch at
  the top).
- **Add connector:** opens the WhatsApp ICT help desk panel.
- **Web search:** a toggle that's remembered in this browser. While it's on, a
  "Web search" chip shows by the + button.
  - The AI can search the web: Gemini uses Google Search grounding, Claude its web search
    tool.
  - The pages it used are listed as **Sources** under the answer.
  - If search fails, the answer comes without it, with a note saying so. After a quota
    error ("429"), search is skipped for 15 minutes so messages aren't slowed down.
    Google Search grounding needs search quota on your Gemini key, and the free tier may
    have little or none.

**Voice:**
- **Mic:** voice typing; words appear as you speak. The small arrow next to it picks the
  language: English (Kenya, US, UK) or Kiswahili.
- **Waveform button** (shown instead of Send when the box is empty): **voice mode**. Talk,
  and when you stop, it sends, reads the answer aloud, then listens again.
  - Tap the circle to interrupt the answer, or to send early.
  - "Sound off" stops the read-aloud.
  - The conversation is saved in the chat as text.
- **Browser support:** both need **Chrome or Edge**, which send the audio to their own
  speech service, so an internet connection is needed. Firefox has no speech recognition.

**Chat title menu** (top centre; also the ⋯ on each chat in the sidebar):
- **Pin (P):** pinned chats show under **Pinned** at the top of the sidebar.
- **Rename (R).**
- **Add to project.**
- **Share chat.**
- **Delete (D).**

The letters are shortcuts while the menu is open.

**Projects:** named folders in the sidebar, private to your account. Create one with the
**+** next to "Projects". Each project's ⋯ menu can rename or delete it; deleting a
project moves its chats back to Recent.

**Share** (top right):
- **Creates a public, read-only link** (`/share/<id>`) that anyone can open without
  signing in.
- **The link shows the chat as it was when shared.** Update link adds newer messages,
  and Stop sharing turns the link off.
- **Files aren't included,** only their names.
- **When hosting the built app,** route `/share/*` to `index.html`. The Vite dev server
  already does this.

**Temporary chat** (the box-with-a-circle icon at the top right, on every chat; from a saved chat it opens a new temporary one): the chat
isn't listed in Recent and can't be pinned, shared or put in a project.
- **Deleted when you leave it:** a new chat, another chat, signing out, or closing the tab.
- **Leftovers** are removed by the server after a day.

### API

| Method | Path | Purpose |
|---|---|---|
| PATCH | `/api/conversations/<id>` | `{title}`, `{pinned}`, `{project_id}` (null = none) |
| POST / DELETE | `/api/conversations/<id>/share` | Create/update, or stop, the public link |
| GET | `/api/conversations/shared/<share_id>` | Public read-only copy |
| GET / POST | `/api/projects` | List / create (`{name}`) your projects |
| PATCH / DELETE | `/api/projects/<id>` | Rename / delete |

Creating a chat accepts `"temporary": true`. Sending, regenerating and editing accept
`"web_search": true`.

## Suggested follow-up questions

After every reply, up to three follow-up questions appear under it. Click one to ask
it.
- **Where they come from:** the AI writes them with each answer (a `suggestions` list in
  its JSON reply), about what was just discussed and in the current mode.
- **Only under the latest reply:** they disappear once you send another message, and a
  regenerated or edited answer brings its own.
- **Cleaned up:** list numbers are removed, and repeats of your question, duplicates and
  blanks are dropped. Each one is cut to 100 characters.
- **Saved with the reply:** they're still there when you reopen the chat. They're stored
  in `message.suggestions_json`, a column added automatically to existing databases.
- **Fallback:** if the AI gives none or is offline, Service and Sales offer that topic's
  usual next questions, matched to the help articles so they get answers even offline.
  General mode offers none when offline, because it can't answer then.

## Photos and files

- **Accepted:** PNG, JPEG, GIF and WebP images, and PDFs, up to 5 MB each. Text files
  (`.txt .md .csv .json .log`, UTF-8) up to 200 KB each. Up to 3 files per message.
- **Ways to attach:** the paperclip button, dragging files onto the chat, or pasting an
  image into the message box.
- **How types are checked:** the backend decides a file's type from its contents, not
  its name, so a renamed `.exe` is rejected. Files are stored under random names in
  `backend/instance/uploads/`, and served with `nosniff` and a sandboxing CSP.
- **What the AI sees:** images and PDFs are sent to Gemini or Claude directly, and text
  files are sent as text. Earlier files in the chat are sent again (newest first, up to
  12 MB in total), so follow-ups like "what colour was it?" work. Older files beyond
  that are replaced by a short note.
- **Deleting a chat** removes its files from disk. Its ticket stays for the agents.

## Recent chats

Customers have no accounts, so the Recent list is kept in **this browser**
(`localStorage`). Each entry holds the chat's access token, which is valid for 30 days.
Clearing site data or switching browser or device starts a fresh list, while the chats
themselves stay on the server.

## User accounts (sign-in page)

Anyone can create an account and use the assistant. The start page has four screens:

1. **Create account:** full name, email and password. The password needs 8+
   characters with at least one letter and one number; the ticks update as you type.
   Each email can only register once.
2. **Sign in:** email and password. "Keep me signed in" keeps you signed in after the
   browser closes; untick it to sign out when the tab closes. A wrong email and a
   wrong password give the same message, so nobody can find out who has an account.
3. **Forgot password:** enter your email and a **6-digit code** is emailed to you.
   The page answers the same whether or not the email has an account.
4. **Reset password:** enter the code and a new password, and you're signed in.
   - The code works once, for 15 minutes, with up to 5 tries.
   - A new code can be requested every 60 seconds, and it replaces the old one.
   - Only a keyed hash of the code is stored.
   - Resetting signs out every other session of that account.

After signing in:
- **Chats belong to the account,** so the Recent list follows you to any device.
- **Older chats:** any made in a browser before accounts existed move into the first
  account that signs in there.
- **Profile menu:** shows your name and email, Settings, agent tools sign-in, and
  **Sign out**.
- **Agents are separate:** the agent sign-in under Agent tools still guards Tickets
  and Analytics for support staff, so users can't see each other's tickets.

### Sending the reset emails

Fill in the SMTP lines in `backend/.env`, then restart the backend:

```
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=helpdesk@yourcompany.com
SMTP_PASSWORD=<app password>
SMTP_FROM=AI in Ideal <helpdesk@yourcompany.com>
```

- **Gmail:** turn on 2-step verification and create an **App Password**. Your normal
  Gmail password won't work.
- **Outlook / Microsoft 365:** use `smtp.office365.com`, port 587.
- **For port 465:** set `SMTP_USE_SSL=true`.
- **Check the settings** before trying the reset page: run
  `flask --app run send-test-email you@example.com` in `backend`. It sends one email
  straight away, or explains what's wrong (for example "use an App Password").
- **No SMTP yet:** while `SMTP_HOST` is empty, the email (with the code) is printed in
  the **backend terminal** instead, and the reset page says so. That's handy for testing.

### Account API

| Method | Path | Access | Purpose |
|---|---|---|---|
| POST | `/api/users/register` | public | `{name, email, password}` → `{token, user}` |
| POST | `/api/users/login` | public | `{email, password}` → `{token, user}` |
| GET | `/api/users/me` | user | The signed-in account |
| POST | `/api/users/forgot-password` | public | `{email}`; emails a 6-digit code |
| POST | `/api/users/reset-password` | public | `{email, code, password}` → `{token, user}` |
| GET | `/api/users/me/conversations` | user | Your chats, each with an access token |
| POST | `/api/users/me/claim-chats` | user | `{tokens}`: move this browser's older chats into the account |

User requests send the token as `X-User-Token`. Creating a chat requires it (set
`REQUIRE_USER_LOGIN=false` to allow chatting without an account).

## How auth works

- **Agents** sign in with a username and password. Passwords are stored as salted
  hashes (Werkzeug's scrypt hashing). Login returns a signed token that expires after
  12 hours. The frontend keeps it in `sessionStorage`, so closing the tab signs you out,
  and any 401 response sends you back to the login form.
- **Customers** don't need accounts. Starting a chat returns a token signed for *that
  one conversation*, so a customer can't read or post to anyone else's chat.
- The two kinds of token use different signing salts, so a customer token can't be
  used as an agent token. An agent can read any chat but can't post as the customer.
- Login gives the same error, with similar timing, for an unknown user and for a wrong
  password, so usernames can't be probed.
- Deactivating an agent (`is_active = False`) locks them out immediately, even with a
  token that hasn't expired.

## Choosing the AI provider

Set `AI_PROVIDER` in `backend/.env`, then restart the backend:

| Provider | `AI_PROVIDER` | Key | Model setting (default) |
|---|---|---|---|
| Claude | `claude` | `ANTHROPIC_API_KEY` | `CLAUDE_MODEL` (`claude-opus-5-5`) |
| Gemini | `gemini` | `GEMINI_API_KEY` | `GEMINI_MODEL` (`gemini-3.5-flash-lite`) |

Both providers get the same instructions, knowledge-base articles and JSON reply
format, so nothing else in the app changes. You can keep both keys in `.env` and
switch with one line.

## How the AI works

1. `knowledge_base.search()` finds the top 3 FAQ articles for the message.
2. `ai_service` sends the conversation history plus those articles to the chosen
   provider with a **JSON-schema output** (Claude: `output_config.format`; Gemini:
   `response_json_schema`), so every reply comes back as
   `{reply, intent, sentiment, escalate, ticket_subject}`. Values outside the allowed
   intents or sentiments are reset to `general` / `neutral`.
3. If `escalate` is true, the conversation is marked escalated and one ticket is opened
   (high priority when the sentiment is negative). Later messages reuse that ticket, so
   there are no duplicates.
4. If the model declines (a Claude refusal, or a Gemini safety block with no text), the
   customer is told a human will help and a ticket is opened. If the API is down, the key
   is wrong, or the output isn't valid JSON, the offline responder answers instead, so the
   chat never breaks.

---

## Code review

### What's solid
- **Clear layering:** routes handle HTTP and validation only. The AI and search logic
  live in `services/`, and the models stay thin. Swapping SQLite for Postgres means
  changing `DATABASE_URL`.
- **App factory + TestConfig:** tests run against an in-memory DB with the AI turned off,
  so they're fast and free.
- **Structured AI output:** the JSON schema with enums guarantees valid intent and
  sentiment values for analytics.
- **Graceful degradation:** API errors, refusals and bad output all fall back to the
  offline responder.
- **Input validation:** empty or over-long messages are rejected with 400, closed chats
  with 409, and bad ticket status or priority values with 400. JSON 404/405 handlers
  replace HTML error pages.
- **Cache-friendly prompt:** the system prompt is static, and the per-request FAQ
  context goes in the user turn.
- **Frontend:** optimistic message send with rollback on error (the text goes back in
  the box), chat state kept across tab switches, accessible labels, dark mode, and a
  layout that works on mobile.

### Bug found and fixed during testing
- In offline mode, an angry complaint ("my package arrived damaged…") matched the
  *order-tracking* FAQ, so the reply was a canned tracking answer. Negative sentiment
  now always gets an apology plus escalation, and there's a test for it.

### Authentication (added)
- Agent login protects tickets, analytics, the chat list and FAQ editing. Customer
  chats are locked to their own conversation token. 12 auth tests cover login, a
  401 on every protected endpoint, cross-chat access, token swapping, deactivated
  agents and the `create-agent` command.

### Gemini support (added)
- `AI_PROVIDER` picks Claude or Gemini, and the rest of the app doesn't change. An
  unknown provider name stops the app at startup with a clear error. 11 provider tests
  fake each SDK and check the request (model, schema, history roles), the reply
  parsing, decline handling and the offline fallback.
- **Not yet tried with a real Gemini key.** A request sent with a fake key reached
  Google and was rejected only for the key. Google checks the key first, so this
  doesn't prove Gemini accepts the reply schema. Send one chat message with your
  key to confirm. If it fails, the backend log shows Google's error and customers
  get the offline answer meanwhile.

### Photo/file uploads and ChatGPT-style UI (added)
- 21 new tests cover type sniffing (including a renamed fake `.png`), size and count
  limits, the 413 response, private downloads, files reaching both AI providers, the
  history byte budget, chat titles, deleting chats with their files, and upgrading an
  old database.
- Checked in the browser with Gemini: it read a photo ("says HI in white capital
  letters"), read a text file ("6 eggs"), remembered the photo in a follow-up question,
  and the delete, new chat, sidebar toggle and phone layout all worked.
- `_add_missing_columns()` adds new columns to an existing database at startup.
  It's a stop-gap until Flask-Migrate is set up.

### Answer modes, profile menu, settings and theme (added)
- 10 new tests cover each mode's instructions and knowledge-base use, General never
  opening tickets (even if the model asks to), switching mid-chat, invalid modes,
  offline replies and analytics. 65 backend tests pass in total.
- Checked in the browser with Gemini: Service mode declined "Why is the sky blue?" and
  pointed to General, and General then answered it in the same chat. The theme toggle,
  the Settings dialog (name, Light/Dark), the profile menu's Sign out and the phone
  layout all worked.

### Sales mode and logo (added)
- 14 new tests cover the separate Sales articles and search, the Sales prompt
  (product name, no invented menus), Sales-tagged tickets, the Sales intents, offline
  Sales answers, upgrading an old database (its own articles are kept as Service, with
  no duplicate defaults), and the Gemini retry settings. 79 backend tests pass in total.
- **Fixed: a weak FAQ match.** "Why is the sky blue?" matched the credit-limit article
  only because both contain "why". Common question words are now ignored, and a
  match needs at least one keyword or two shared words.
- **Fixed: Gemini "503 high demand" errors.** These made some replies silently fall
  back to offline answers (2 of 3 attempts in one test). The Gemini client now retries
  503, 429 and other temporary errors with backoff (up to 4 attempts), and a live
  re-test passed 3 of 3.
- Checked in the browser: the logo at the top left (on a light chip in dark mode), the
  three-way switch, the Sales welcome screen and a Sales answer.

### Service mode retargeted to the ERP Service module (added)
- The Service assistant, its topics (`SERVICE_INTENTS`), its offline keyword
  matching, its help articles and the chat screen texts now cover the Ideal Dynamics
  Service module instead of online-store support.
- 16 new tests cover the new prompt, articles and topic detection, and the upgrade
  path: old store articles are replaced and custom articles are kept. 95 backend
  tests pass in total.
- Checked live with Gemini: it combined two Service articles into one answer about
  assigning a technician and checking warranty. It explained warranty vs service
  contract, and it redirected an online-order question to the right mode.

### Serial number and site on Service tickets (added)
- 15 new tests cover extraction (including "serial number please" and "site visit",
  which must not count), the reply format, filling a missing detail from a later
  message, Sales and General never carrying these fields, offline asking and
  filling, and agent edits with validation. 110 backend tests pass in total.
- Checked live with Gemini on a throwaway database: "the air compressor at our Kisumu
  depot keeps tripping" opened a Service ticket with site "Kisumu depot" and asked
  for the serial. The reply "The serial number is CMP-7781" then filled it in on the
  same ticket.

### Copy, Regenerate and Edit (added)
- Sending, regenerating and editing now share one "answer this message" step on the
  server, so tickets, serial/site capture and modes work the same way for all three.
- 11 new tests cover regenerating only the latest reply, switching mode on
  regenerate, edit replacing later messages and deleting their files, keeping the
  edited message's own files, renaming the chat, validation, access and closed chats.
  121 backend tests pass in total.
- **Fixed: reused ids.** SQLite reused a deleted row's id, so after an edit a new
  photo could show a deleted photo from the browser's cache. New databases now never
  reuse ids, and the image cache uses each file's unique storage key.
- **Fixed: Copy did nothing** when the browser refused clipboard access. There's now
  a fallback, plus a "Couldn't copy" message if both methods fail.
- Checked in the browser: Regenerate replaced the reply, editing "Kenya" to "Uganda"
  replaced the answer and renamed the chat, and Copy showed "Copied".

### WhatsApp help desk button (added)
- 13 new tests cover number clean-up ("+254 712-345 678" becomes "254712345678", and
  local "0712..." numbers are rejected), the config endpoint with and without a number,
  and the warning for an invalid one. 134 backend tests pass in total.
- Checked in the browser: the button at the bottom right moves above the message box
  on smaller screens and shows icon-only on phones. The panel shows "not set up yet"
  with no number. With a placeholder number, it built the right wa.me link with the
  typed message, opening in a new tab. The link wasn't opened.

### User accounts with password reset by email (added)
- 22 new tests cover registration rules and duplicate emails, sign-in without
  revealing which emails exist, the reset flow, one-use codes, the 5-try limit,
  expiry, no resend within a minute, codes stored hashed, old sessions ending after a
  reset, chats owned by accounts, and claiming older chats. 156 backend tests pass in
  total.
- Checked in the browser on a throwaway copy of the app:
  - creating an account, including the "passwords don't match" check
  - signing out
  - forgot password, including rejecting a wrong code
  - resetting with the logged code
  - the old password refused and the new one accepted
  - the chat history coming back with the account
  - the phone layout
- **Not yet tested: real email delivery.** It needs your SMTP settings. Until then,
  codes appear in the backend terminal.

### Issues to address before production
1. **No rate limiting on sign-in, registration, reset codes or chat.** Passwords can be guessed without limit, and
   each chat message is a paid AI call. Add Flask-Limiter: about 5 login attempts
   per minute per IP, and a per-conversation message limit.
2. **Tokens can't be revoked one at a time.** They're signed, not stored. To cut off an
   agent, deactivate the account. To cut off everyone, change `SECRET_KEY`. Add a
   token table if you need per-device sign-out.
3. **Customers lose their chat on page reload.** The chat token is only held in
   memory. Save it in `sessionStorage` if customers should be able to resume.
4. **HTTPS required in production.** Bearer tokens sent over plain HTTP can be
   intercepted. Also note that `sessionStorage` can be read by any script running on
   the page, so keep the frontend free of untrusted scripts.
5. **Blocking AI call.** The request waits for the model to finish. That's fine for short
   replies, but for long ones switch to streaming (SSE) so text appears as it's written.
6. **No migrations.** `db.create_all()` won't change existing tables. Add Flask-Migrate
   (Alembic) once the schema starts changing.
7. **Dev server.** `run.py` uses Flask's debug server. Deploy with gunicorn or waitress,
   and set `debug=False`.
8. **Basic knowledge-base search.** Keyword overlap misses synonyms. For a larger FAQ,
   use full-text search (SQLite FTS5 or Postgres `tsvector`) or embeddings.
9. **Uploads aren't scanned for malware**, and there's no storage quota per chat. Add
   virus scanning and a cleanup job before accepting files from the public.
10. **Gemini can stay overloaded longer than the retries.** Retries cover about 7
    seconds of "503 high demand". If Google stays busy longer, that message gets the
    offline answer, as happened once in testing. Consider a paid Gemini tier, or
    switch `AI_PROVIDER` to Claude.
11. **Offline mode is a fallback, not a product.** Its intent and sentiment detection is
   regex-based and will misclassify sometimes.
12. **Broad fallback catch.** `generate_reply` catches every exception from the provider
    call, so a coding bug there also falls back silently. It's logged with a full
    traceback, so check the backend log if replies are always offline.
