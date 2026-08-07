# Feature Documentation

Trying my best to explain why I added these features and how to make the most of them. Currently it only works with Mantella below v 0.14.

## Directory

- [Character Tags](#character-tags)
- [Dynamic Events (tag feature)](#dynamic-events-tag-feature)
- [Lorebook](#lorebook)
- [Multiple Profile System](#multiple-profile-system)
- [Bio Sections to Exclude](#bio-sections-to-exclude)
- [Reload Character Data](#reload-character-data)
- [Save Summary Now](#save-summary-now)
- [Real World Timestamp](#real-world-timestamp)
- [Hot Swap](#hot-swap)
- [bios_and_summaries Prompt Variable](#bios_and_summaries-prompt-variable)

## Character Tags

### What are character tags?

Character tags are reusable labels you assign to NPCs. Each tag maps to a description in a **bio templates** CSV.
When an NPC loads, Mantella appends the matching descriptions to their bio under `## Additional info`.

### Why use character tags?

- Give NPCs shared knowledge (a hold, a guild, a questline, etc) without rewriting every bio by hand. Update one template (for example `whiterun_hold`) and every NPC with that tag picks up the change.
- Use community-ready assignments so you do not have to decide which NPCs belong to which hold or quest.
- Assign personalities or moods to NPCs randomly to add more variety to your gameplay.

Note that there are no correct answers for how to create tags. It is up to your creativity and the story you want to tell.

Enable or disable this feature with **Enable Character Tag Reading** in the LLM settings.

### Recommended files (Nexus)

Strongly recommended: use the character CSV and bio templates from [Nexus](https://www.nexusmods.com/skyrimspecialedition/mods/174770).

That pack is built for this tag system:

- Its **`skyrim_characters.csv`** has the correct columns (`tags`, `tags_overwrite`, and related fields). Use that file (or start from it) so columns match what Mantella expects.
- Its bio_templates CSVs include the essential tags and already assign them to the right NPCs. You do not need to figure out who should get `whiterun_hold` — that tag is already on the correct characters. It also includes tags for quests, some popular mods, and more.

Using those files is the fastest way to get a working, game-wide tag setup.

### Quick start: create a tag and give it to an NPC

Follow these steps to add one custom tag without touching the big base CSV.

**1. Create the tag definition**

Create (or edit) a CSV under your personal templates folder:

`Documents\\My Games\\Mantella\\data\\Skyrim\\bio_templates\\my_custom_tags.csv`

Contents:

```
tag,description
whiterun_hold, Ado is the dragonborn who saved the city from a dragon attack.
```

Any `.csv` name is fine. Columns must be `tag` and `description`. You can also use the files from the nexus page mentioned above, in which lots of tags are already predefined. 

**2. Assign the tag to a character (`tags` vs `tags_overwrite`)**

Open the character's existing row in your character override CSV. You only need the **`tags`** / **`tags_overwrite`** columns.

**When to use which column**

| Goal | Column to fill | Leave empty |
|------|----------------|-------------|
| **Add** tag(s) on top of what the NPC already has | `tags` | `tags_overwrite` |
| **Replace** the NPC's entire tag list with a new set | `tags_overwrite` | - |

- Use **`tags`** for most edits. Example: NPC already has `companion` in the base file; you set `tags` in your override CSV to `whiterun_hold` → effective tags become `companion,whiterun_hold`.
- Use **`tags_overwrite`** only when you don't like the tags the NPC is assigned in the base character file and want to clear them and replace them with your own. Example: you set `tags_overwrite` to `riften_hold,thieves_guild` → effective tags are **only** those two; everything from the `tags` column is ignored.
- Assign tags as a comma-separated list (for example: `warrior,mage,whiterun_hold`).

**3. Reload**

Go to **Other → Reload Character Data → Reload**.

**4. Check**

Start a new conversation with that NPC, or open **Bio Editor**, select them, and confirm **Runtime Tags** includes your tag (for example `whiterun_hold`). Their bio should gain the description under `## Additional info` in the log.

Also:

- Use the **Bio Editor** tab to edit a character's tags, see **Runtime Tags** (the merged effective list). It's a good place to debug.
- Tags are **case-sensitive** and must match the template name exactly.
- Duplicate tags are applied only once.
- Bios and templates may use `{player_name}`; it is replaced with the current player's name at runtime (or `"the player"` if none is present).


### File structure & override system

This section is for people who want to know how the base file, override files work and the best practices for using them.

Character file and tags file both use a **layered** file layout: a base file first, then optional override folders on top.
Later layers win (except `tags`, which appends).

#### Character data (who has which tags / bios)

| Layer | Role | Path |
|-------|------|------|
| **1. Base** | The base file, usually what you download from the Nexus page above. | `<Mantella install>\\data\\Skyrim\\skyrim_characters.csv` |
| **2. Mod overrides** | Shared / mod-manager overrides, usually from mod authors | `<Skyrim Data>\\SKSE\\Plugins\\MantellaSoftware\\data\\Skyrim\\character_overrides\\` |
| **3. Personal overrides** | Your private edits (safest place for you to customize) | `Documents\\My Games\\Mantella\\data\\Skyrim\\character_overrides\\` |

Load order: **base → mod overrides → personal overrides**.
Later layers win: mod overrides overwrite the base; personal overrides overwrite both. (Exception: `tags` appends instead of replacing, as mentioned above.)

**File naming (character overrides)**

- You can put **multiple files** in an override folder.
- Names are free: `my_edits.csv`, `companions.json`, `whiterun_npcs.csv`, etc. — only the extension matters.
- Supported extensions: **`.csv`** and **`.json`**.
- Every file in the folder is loaded; there is no required filename.

#### Bio templates (what each tag means)

| Layer | Role | Path |
|-------|------|------|
| **1. Base** | Default / Nexus template CSVs | `<Mantella install>\\data\\Skyrim\\bio_templates\\` |
| **2. Mod overrides** | Shared template packs from mod authors | `<Skyrim Data>\\SKSE\\Plugins\\MantellaSoftware\\data\\Skyrim\\bio_templates\\` |
| **3. Personal overrides** | Your private tag text edits | `Documents\\My Games\\Mantella\\data\\Skyrim\\bio_templates\\` |

Load order: **base → mod → personal**.
Later layers win: mod overwrites base; personal overwrites both. Same tag name in a later folder replaces the earlier description.

**File naming (bio templates)**

- You can put **multiple CSV files** in the same `bio_templates` folder.
- They do **not** have to be named `bio_templates.csv`. That name is only the default Mantella creates if the folder is empty.
- Any filename works as long as it ends in **`.csv`** — for example: `holds.csv`, `questlines.csv`, `my_custom_tags.csv`.
- Each file must have columns **`tag`** and **`description`**.
- All CSVs in the folder are loaded and merged into one tag dictionary. If the same tag name appears in more than one file (or a later layer), the **last one loaded wins** (later folder layers always beat earlier ones; within a folder, load order follows the directory listing).

Example layout:

```
data\\Skyrim\\bio_templates\\
  bio_templates.csv      ← optional; common default / Nexus name
  holds.csv              ← fine
  sidequests.csv         ← fine
  my_custom_tags.csv     ← fine
```

### Reloading after file changes

Editing CSVs on disk does **not** update Mantella automatically — character data and bio templates are loaded into memory at startup.

After you change `skyrim_characters.csv`, any `character_overrides` file, or any file under `bio_templates`:

1. Open the **Other** settings tab in the Mantella UI.
2. Find **Reload Character Data**.
3. Click the **Reload** button.

That reloads character CSVs, overrides, **and** bio templates from disk (it recreates the game data in memory). See also [Reload Character Data](#reload-character-data).

**Notes**

- If a conversation is active, Reload Character Data **ends that conversation** first, then reloads.
- You can also fully restart Mantella instead; Reload is the faster option while the UI is already open.
- After reloading, start a **new** conversation (or re-select the NPC in Bio Editor) to see updated tags/bios.

## Dynamic Events (tag feature)

### What are dynamic events?

Dynamic events are a **feature of the character tag system**. You write them inside a tag's description in a bio templates CSV — same place as normal tag text — but they behave differently at runtime.

A tag description can hold two kinds of content:

| Content | What it looks like | Where it goes |
|---------|--------------------|---------------|
| **Static tag text** | Ordinary sentences / bullets | Appended to the NPC's **bio** (`## Additional info`), like any other tag |
| **Dynamic event lines** | `- <unix_timestamp>: <event text>` | **Not** added to the bio. Merged into the NPC's **memory timeline** (conversation summaries) |

Format for an event line (must start the line):

```
- <unix_epoch_seconds>: <event text>
```

### Quick Start: create a dynamic event in a tag

Example tag description (static text + two dated event lines):

| tag | description |
|-----|-------------|
| `whiterun_hold` | `NPCs in Whiterun Hold know of the Dragonborn.` *(static — goes into the bio)* <br>`- 1739270400: {player_name} became Thane of Whiterun` *(dynamic event)* <br>`- 1740000000: {player_name} defeated Alduin` *(dynamic event)* |

Need a Unix timestamp? Open the **Other** tab and use **Real World Timestamp** → **Get Timestamp** (see [Real World Timestamp](#real-world-timestamp)), then paste it into your event line.

Assign the tag the same way as any other tag (`tags` / `tags_overwrite`). After editing templates, use **Other → Reload Character Data → Reload**.

---

Now let's talk about what a dynamic event does, and when you need it.

### Feature 1: Chronological mixing with summaries

Conversation summaries and dynamic events can both carry **Unix timestamps**. At runtime, when Mantella builds an NPC's memory for an active conversation, timestamped dynamic events are **mixed with timestamped summaries in time order**. Timestamps are stripped before the text is sent to the LLM — the model only sees the ordered story.

#### What about content that is *not* timestamped?

| Item | Behavior |
|------|----------|
| **Legacy summaries** (older summary paragraphs with no `ts=` marker) | Kept in their original file order and placed **before** the timestamped timeline |
| **Static tag text** (no event-line pattern) | Stays in the **bio** only — it never enters the memory timeline |
| **Timestamped summaries** + **dynamic events** | Merge-sorted together by timestamp (oldest → newest) |

So: undated memory stays at the front as a fixed block; everything with a timestamp is ordered by when it happened.

#### Why does order matter?

Sometimes *when* something became true matters as much as the fact itself.

Suppose a snobbish Whiterun noble talked down to you for weeks. Those rude conversations are already in their summary. If you only put *"I am now the Thane of Whiterun"* into static tag text, that fact sits in the bio forever — but the old summaries still read as if they chose to insult you *while* you were Thane. The LLM often keeps treating you poorly, which is wrong.

With a dynamic event, `"became Thane of Whiterun"` gets a timestamp **after** those earlier summaries. In the next conversation the memory timeline ends with the Thane appointment, so the noble can plausibly change tone — respect, flattery, sudden politeness — because the status change sits in the correct place in history.

The same pattern works for quest endings, romance turning points, guild promotions, and anything else where past behavior should no longer apply after a dated beat.

---

### Feature 2: New developments since last meeting

When you talk to an NPC again, Mantella also checks whether any dynamic events happened **after** your last recorded conversation with them.

**How detection works**

1. Find the **latest timestamp** among that NPC's timestamped summary blocks (the last time their memory was written with a `ts=` marker).
2. Any dynamic event whose timestamp is **strictly later** than that latest summary is treated as new.
3. If the NPC has **no** timestamped summaries yet, every dynamic event counts as new (baseline is `0`).

When the conversation starts, in runtime, those new events are listed in a separate block at the end of memory:

```
New developments since last meeting:
- {player_name} became Thane of Whiterun
- {player_name} defeated Alduin
```

That makes recent plot beats stand out: the NPC is not only remembering history in order — they are explicitly nudged about what changed since you last spoke. NPCs will react to these new developments automatically.

---

### Full example

**Shared setup**

Sildor is a Whiterun snob. You already talked twice; both times he insulted you. Those chats are already in his summaries:

| Timestamp | Summary |
|-----------|---------|
| `1739000000` | Sildor mocked {player_name} as a muddy traveler with no standing. He refused help and ended the talk curtly. |
| `1740000000` | Sildor dismissed {player_name} again, joking that Jarl Balgruuf would never take such a person seriously. |

His base personality (always in the bio):

```
Sildor is a wealthy, snobbish noble in Whiterun who looks down on outsiders and commoners.
```

You later become Thane of Whiterun. Below: same fact, two ways to put it on the `whiterun_hold` tag — and how that changes the next conversation.

---

#### A. Thane as **static** tag text (the problem)

**Tag**

| tag | description |
|-----|-------------|
| `whiterun_hold` | NPCs in Whiterun Hold know of the Dragonborn. {player_name} became Thane of Whiterun. |

**What the LLM sees**

Bio gets the Thane line under `## Additional info`. Memory is only the old summaries — no order that puts Thane *after* the insults:

```
## Additional info
NPCs in Whiterun Hold know of the Dragonborn. {player_name} became Thane of Whiterun.

(memory)
- Sildor mocked {player_name} as a muddy traveler…
- Sildor dismissed {player_name} again…
```

**How Sildor responds**

He often stays rude. The model sees past contempt *and* “you are Thane” at the same time, with no clear before/after — so it treats the insults as if he chose them while you were already Thane, and keeps that tone.

---

#### B. Thane as a **dynamic** event (the fix)

**Tag**

| tag | description |
|-----|-------------|
| `whiterun_hold` | NPCs in Whiterun Hold know of the Dragonborn.<br>`- 1740500000: {player_name} became Thane of Whiterun` |

**What the LLM sees**

Static tag text still goes to the bio. The dated line goes into memory, sorted after both summaries. Because its timestamp is later than the latest summary, it also appears under **New developments**:

```
## Additional info
NPCs in Whiterun Hold know of the Dragonborn.

(memory)
- Sildor mocked {player_name} as a muddy traveler…
- Sildor dismissed {player_name} again…

New developments since last meeting:
- {player_name} became Thane of Whiterun
```

**How Sildor responds**

He can change tone — respect, flattery, sudden politeness — because the memory clearly shows: insults first, *then* you became Thane.

After you talk again and a newer summary is saved, the Thane line stays in the chronological timeline but drops out of **New developments** (only events newer than the latest summary show there).

## Lorebook

### What is the lorebook?

The lorebook is a list of **keys** and **descriptions** (stored in CSV files).
It is very handy when you want to teach the LLM facts about custom lore, places, relationships, etc. **only when they come up**, instead of stuffing every bio with long irrelevant context all the time.


When Mantella builds a prompt, it looks for those keys in the context — for example the current `{location}`, recent chat, or in-game events (will talk more about where it scans for the key below). If a key is found and matched, its description is inserted wherever you put `{lorebook}` in the prompt.

It works for conversation prompts and also for vision, memory, and resummarize prompts if those include `{lorebook}`.

Unlike character tags (which permanently expand an NPC's bio), lorebook entries are **situational**: they only appear when a matching key shows up in the context.

**Examples**

1. **Breezehome (location-triggered).** You want NPCs to know how your Breezehome looks. Create a lorebook entry describing the decorations, hearth location, furniture, rooms, etc. Because Mantella supports `{location}`, when you are at Breezehome the location value is `Breezehome`, the lore is pulled in, and the NPC can be aware of your home and surroundings in the current conversation without that text living in every bio.

2. **Custom lore talked about in chat.** You opened a famous restaurant in Riverwood called **In and Out**. Instead of putting that in every bio, prompt, or tag (so it burns tokens and can distract from the current topic), put it in the lorebook. It only appears when you or an NPC mentions it — for example you say *"Hey, let's go to the In and Out."* You do not have to explain what that is; the lore is pulled up and the NPC already knows.

Note that there is no single "correct" lorebook. Keys and descriptions are up to your creativity and the story you want the LLM to know.

**Important:** lorebook text only appears if your prompt includes `{lorebook}`. Default prompts do not include it — add the variable yourself (see below).


### Tags vs lorebook

Lorebook is **not** meant to replace tags. They work **side by side** and complement each other.

**Tags** are for world knowledge that should **always** be present for the NPCs who have them — things that shape how people treat you and how they talk, whether the current conversation mentions them or not. Example: use a `whiterun_hold` tag so Whiterun people know you are Thane of Whiterun, or that you are the Dragonborn. Not every chat will bring those up, but people still speak with respect. Same idea for shared local knowledge: everyone in Whiterun knows where the Bannered Mare is, no matter what topic you are on. That belongs on a tag so it lives in the bio every time.

**Lorebook** is for context you only want **when it is relevant**. You do not want Lydia thinking about Breezehome's decorations while you two are in Solitude talking to the queen. You want her to compliment your little home when you are actually in Breezehome. That is where the lorebook comes in: inject the detail on demand (via location, a spoken name, an event, etc.), then leave it out of every other conversation.

| | **Tags** | **Lorebook** |
|--|----------|--------------|
| When it appears | Always (for NPCs with that tag) | Only when a key matches scanned text |
| Best for | Standing world knowledge, status, shared local facts | Situational detail (rooms, custom places, one-off lore) |
| Example | `whiterun_hold`: you are Thane / Dragonborn; Bannered Mare is in Whiterun | `Breezehome`: furniture and layout only while you are there |


### How matching works

Mantella scans text from:

| Source | Included? |
|--------|-----------|
| Prompt variables used in that prompt (e.g. `{location}`, `{name}`, `{weather}`, `{time}`, etc) | Yes |
| Conversation history (spoken turns) | Yes |
| Recent in-game / custom events | Yes |
| `{bio}` / `{bios}` / `{conversation_summary}` / `{bios_and_summaries}` / related memory blocks | **No** (excluded on purpose) |
| `{lorebook}` itself | **No** (avoids recursion) |

So `{location}` becoming `Breezehome`, or the player saying "Let's go to the In and Out", can trigger an entry — but text that only exists inside the NPC bio or summary will not, by design. Put shared world facts in the lorebook (or tags) instead of relying on bios to trigger keys.

Matched entries are sorted alphabetically by key and inserted wherever you placed `{lorebook}`.

### Quick start: create an entry

Follow these steps to add custom lorebook entries without touching a large base CSV. The examples below match the two use cases above.

**1. Create the entries**

Create (or edit) a CSV under your personal lorebook folder:

`Documents\\My Games\\Mantella\\data\\Skyrim\\lorebook\\my_custom_lore.csv`

Contents:

```
key,description
Breezehome,"Player home in Whiterun. Interior: warm firepit in the main room, weapon racks by the door, a cooking spit and pantry downstairs, a double bed and wardrobe upstairs, and shelves of books and trophies along the walls."
In and Out,"A famous restaurant in Riverwood known for quick meals and packed tables. Locals and travelers alike recommend it."
```

Any `.csv` name is fine. Columns must be **`key`** and **`description`**.

**2. Put `{lorebook}` in your prompt**

Open the relevant prompt in the **Prompts** tab (for example **Skyrim Prompt**) and add `{lorebook}` where you want matched entries to appear — often near the end, after location / weather / summary.

Example snippet:

```
The conversation takes place in {language}.
{conversation_summary}
{lorebook}
```

You can also add `{lorebook}` to multi-NPC, radiant, vision, memory, or resummarize prompts if those should receive lore too.

**3. Reload**

Go to **Other → Reload Character Data → Reload** (or restart Mantella).

**4. Check**

Start a **new** conversation, then either:

- Stand in **Breezehome** (so `{location}` contains `Breezehome`), or
- Say something like *"Let's go to the In and Out."*

Matched entries are injected as lines like:

```
- [Breezehome]: [Player home in Whiterun. Interior: ...]
- [In and Out]: [A famous restaurant in Riverwood ...]
```

If nothing matches, `{lorebook}` expands to empty text.

Also:

- Matching is **case-insensitive** (`breezehome` and `Breezehome` both hit the same entry).
- Keys match as whole words / tokens (word-boundary style). `Breezehome` matches inside `at Breezehome,` but not inside a longer glued word.
- Duplicate keys across files collapse to one entry; the **last loaded** description wins (see override order below).
- Empty keys or empty descriptions are skipped.

### File structure & override system

Lorebook files use the same **layered** layout as bio templates: a base folder first, then optional override folders on top. Later layers win for the same key.

| Layer | Role | Path |
|-------|------|------|
| **1. Base** | Default / shared lore CSVs shipped with Mantella (or packs you drop in) | `<Mantella install>\\data\\Skyrim\\lorebook\\` |
| **2. Mod overrides** | Shared packs from mod authors / mod managers | `<Skyrim Data>\\SKSE\\Plugins\\MantellaSoftware\\data\\Skyrim\\lorebook\\` |
| **3. Personal overrides** | Your private lore edits (safest place to customize) | `Documents\\My Games\\Mantella\\data\\Skyrim\\lorebook\\` |

Load order: **base → mod overrides → personal overrides**.
Later layers win: mod overwrites base; personal overwrites both. Same key name in a later folder replaces the earlier description.

**File naming**

- You can put **multiple CSV files** in the same `lorebook` folder.
- They do **not** have to be named `lorebook.csv`.
- Any filename works as long as it ends in **`.csv`** — for example: `places.csv`, `factions.csv`, `my_custom_lore.csv`.
- Each file must have columns **`key`** and **`description`**.
- All CSVs in the folder are loaded and merged into one dictionary. If the same key appears in more than one file (or a later layer), the **last one loaded wins** (later folder layers always beat earlier ones; within a folder, load order follows the directory listing).

Example layout:

```
data\\Skyrim\\lorebook\\
  lorebook.csv           ← optional; common default name
  places.csv             ← fine
  factions.csv           ← fine
  my_custom_lore.csv     ← fine
```

### Reloading after file changes

Editing CSVs on disk does **not** update Mantella automatically — lorebook entries are loaded into memory when the lorebook manager starts (conversation / summary setup).

After you change any file under `lorebook`:

1. Open the **Other** settings tab in the Mantella UI.
2. Find **Reload Character Data**.
3. Click the **Reload** button.

That recreates game data and the rememberer from disk (including lorebook CSVs). Starting a new conversation then loads a fresh lorebook for chat prompts as well. See also [Reload Character Data](#reload-character-data).

**Notes**

- If a conversation is active, Reload Character Data **ends that conversation** first, then reloads.
- You can also fully restart Mantella instead; Reload is the faster option while the UI is already open.
- After reloading, start a **new** conversation and mention a key (or rely on location / events) to confirm the entry appears under `{lorebook}`.
- Remember: without `{lorebook}` in the active prompt, entries never show up even if they match.

## Multiple Profile System

### Use cases

Why use multiple profiles?

Parameters such as `presence_penalty`, `temperature`, `top_p`, and `repetition_penalty` slightly change the tone of the LLM. Different providers can also produce different response quality.

- **Blind-test parameters or providers** — set up several profiles (different penalties/temps, or different OpenRouter providers) and use random selection with weights to find what works best for a model.
- **Less predictable replies** — give profiles different `presence_penalty` / `temperature` values and pick randomly so responses stay more varied.
- **Toggle reasoning/thinking** — enable, disable, or A/B test reasoning per profile; useful on models that otherwise ramble or overthink.
- **Easy manual switching** — keep multiple saved sets and flip between them even when you are not using random selection (no retyping).
- **Different jobs, different settings** — e.g. a creative profile for roleplay and a stricter one for summaries. (Summaries always use Profile 1 regardless of the apply toggles.)
- **Combine with random LLM pools** — both the model and its parameter set can vary together.

### What are model profiles?

**Model Profiles** store LLM parameter sets (temperature, max tokens, provider filters, thinking/reasoning options, etc.)
per service and model, so you do not have to re-enter settings when switching models.

Profiles are edited in the **Model Profiles** settings tab and saved to `data/model_profiles.json`.

### Multiple profiles per model

Each service + model combination can have up to **10 slots** (Profile 1–10). Each slot has:

- **Parameters** — JSON of LLM request parameters
- **Enabled** — whether the slot can be chosen when random selection is used
- **Weight** — relative chance of being picked when random selection is on. A non-negative number (float is fine): can be less than 1 (e.g. `0.5`) or greater than 1 (e.g. `3`). Only the ratios matter — weights `1` and `3` mean a 1:3 chance (25% / 75%). `0` excludes the slot from random picks (same as leaving Enabled off).

### When profiles are applied

Turn on the apply toggles (in Model Profiles / LLM settings) for the contexts you want:

- **Apply Profile for One-on-One Conversations**
- **Apply Profile for Multi-NPC Conversations**
- **Apply Profile for Summaries**

When a toggle is **enabled** and a profile exists for the selected model, that profile’s JSON parameters are used for that context.

When a toggle is **disabled**, Mantella falls back to the default LLM settings from the **LLM** tab (temperature, max tokens, stop sequences, custom token count, etc. — whatever you configured there for the active service/model). Profiles are ignored for that context.

### How a slot is chosen

- **Random LLM selection** — among slots that are enabled with weight > 0, one is chosen by weight.
  If none qualify, Profile 1 is used as a fallback.
- **Summaries** — always use Profile 1 (first slot), regardless of Enabled checkboxes or random selection.

### Example parameters JSON (glm 5.2)

```
{
    "max_tokens": 2000,
    "temperature": 1,
    "frequency_penalty": 0.1,
    "top_p": 0.95,
    "stop": [
        "#"
    ],
    "extra_body": {
        "repetition_penalty": 1.1,
        "presence_penalty": 0.1,
        "reasoning": {
            "exclude": true,
            "enabled": true,
            "effort": "xhigh"
        },
        "provider": {
            "only": [
                "z-ai"
            ]
        }
    }
}
```

Supported services for profiles: **OpenRouter**, **OpenAI**, and **NanoGPT**.

### Profiles vs random LLM pools

- **Random LLM pools** pick among different *models*.
- **Profiles** pick among different *parameter sets* for a given model.

They can work together: a model is chosen from the pool, then (when random selection is active)
a weighted profile slot for that model may be chosen.

## Bio Sections to Exclude

### Where to find it

**LLM** tab → **Enable Bio Section Filter (Single + Multi)** and **Bio Sections to Exclude (comma-separated)**.

### What it does

Many character bios are written with markdown section headers such as `## Race`, `## Appearance`, or `## Personal History`. This setting lets you **omit selected top-level sections from the prompt** sent to the LLM.

For example, I use it on a **`Personal Secret`** section. Those details would give away important things about NPCs the first time I meet them, so I keep that section excluded until we are close enough — then I turn the filter off (or remove that name from the exclude list) mid-conversation via hot swap so the secret can enter the prompt.

- It does **not** edit character CSV files or Bio Editor text. Filtering only happens when Mantella builds a conversation prompt.
- Matching is **case-insensitive** against `## Section Name` headers (top-level `##` only — `###` subsections are ignored as exclude targets).
- Applies to **single-NPC and multi-NPC** conversations that include the player. It does **not** apply to radiant conversations.

Use it when bios are long and you want to save tokens or keep the model focused (for example drop `Personal History` in casual chats, or drop `Race` if it is redundant with other context).

**Mid-conversation:** with **Enable Hot-Swap Settings** on (**Other** tab, default on), you can toggle **Enable Bio Section Filter** and edit **Bio Sections to Exclude** during an active conversation. The change applies on the next prompt without ending the chat — useful if you want full bios for a serious talk, then strip sections again afterward. See [Hot Swap](#hot-swap).

### Quick start

**1. Turn the filter on**

In the **LLM** tab, enable **Enable Bio Section Filter (Single + Multi)**.

**2. List sections to remove**

In **Bio Sections to Exclude (comma-separated)**, enter the header names **without** the `##`. Separate multiple names with commas.

Example:

```
Personal History, Race
```

That removes these blocks from the prompt:

```
## Race
- Nord

## Appearance
- Tall, grey eyes

## Personal History
- Grew up in Whiterun...
```

…leaving something like:

```
## Appearance
- Tall, grey eyes
```

**3. Check**

Start a conversation (or keep talking if one is already active and hot swap is on) and look at the log / prompt: excluded `##` sections should be gone for that NPC. Names that do not appear as `##` headers in the bio are simply skipped (nothing breaks).

**Notes**

- Spaces around commas are fine (`Race, Personal History`).
- Only exact top-level header titles count — `Personal History` will not remove a header named `History`.
- Leave the field empty (or turn the filter off) to send the full bio again.
- Filter on/off and the exclude list can be changed mid-conversation when hot swap is enabled.

## Reload Character Data

### Where to find it

**Other** tab → **Reload Character Data** → **Reload** button.

### What it does

Character CSVs, character overrides, bio templates, and lorebook files are loaded into memory when Mantella starts. Editing those files on disk does **not** update a running session by itself.

**Reload Character Data** recreates game data from disk so your latest edits apply without fully restarting Mantella. It refreshes:

- Base character CSV + character overrides
- Bio templates (character tags)
- Lorebook CSVs

### Quick start

Suppose you just added a tag in `Documents\\My Games\\Mantella\\data\\Skyrim\\bio_templates\\my_custom_tags.csv` and assigned it on an NPC override CSV.

1. Open the **Other** tab.
2. Find **Reload Character Data**.
3. Click **Reload**.
4. Start a **new** conversation with that NPC (or re-select them in **Bio Editor**) and confirm the change (e.g. **Runtime Tags** / bio text).

**Notes**

- If a conversation is active, Reload **ends that conversation first**, then reloads.
- The game / Mantella must already be started for the button to work.
- A full Mantella restart also reloads everything; Reload is the faster option while the UI is open.

## Save Summary Now

### Where to find it

**Other** tab → **Save Summary Now** → **Save Summary** button.

### What it does

Normally Mantella writes a conversation summary (and log, if enabled) when a conversation **ends**. **Save Summary Now** triggers that save **immediately** while the conversation **keeps going**.

**Use cases**

- **Crash / unexpected quit:** the game crashes, freezes, or you quit without Mantella receiving a normal end-conversation event — you can still preserve memory for the NPC before you lose the session.
- **Testing summary LLMs:** you want to find the best model for summaries. Keep the same conversation open, switch the summary LLM in settings, click **Save Summary**, and compare the written summary directly — no need to end and restart the chat for each candidate.

### Quick start

1. Have an **active** conversation in-game (at least enough dialogue to summarize).
2. Open the **Other** tab.
3. Find **Save Summary Now** and click **Save Summary**.
4. Confirm the UI message (something like "Summary/log save triggered") and/or check the NPC's summary file / log.

You can keep talking afterward. When the conversation later ends normally, Mantella may generate **another** summary as usual — so you might get both the mid-conversation save and the end-of-conversation save.

**Notes**

- Requires an active conversation; otherwise the button reports there is nothing to summarize.
- Uses the currently selected summary model / settings (including recent UI changes to the summary LLM when possible).
- After a manual save, a normal end-of-conversation summary may still run later — keep that in mind when comparing test summaries.

## Real World Timestamp

### Where to find it

**Other** tab → **Real World Timestamp** → **Get Timestamp** button.

### What it does

Generates the current **Unix timestamp** (seconds since epoch, UTC) so you can paste it into places that need a real-world time marker — most often **dynamic event** lines in bio templates.

## Hot Swap

### Where to find it

**Other** tab → **Enable Hot-Swap Settings** (default: on).

### What it does

When you change settings in the UI while Mantella is running, those changes need to reach the live game session. **Hot swap** applies many setting updates **without ending the current conversation**.

| Setting | Behavior |
|---------|----------|
| **Enabled** (default) | LLM model settings, prompts, bio filter, and similar config apply on the next game request; the conversation continues |
| **Disabled** | Settings changes fall back to classic behavior: the conversation is ended and the route restarts with the new config |

Character CSV / bio template / lorebook file edits are different — those still need **Reload Character Data** (or a restart), which ends an active conversation. Hot swap is for **config/UI settings**, not for reloading character files from disk.

### Quick start

**Example: toggle the bio section filter mid-chat**

1. Confirm **Other → Enable Hot-Swap Settings** is on.
2. Start (or continue) a conversation with an NPC.
3. Open the **LLM** tab and turn **Enable Bio Section Filter (Single + Multi)** on or off (optionally edit **Bio Sections to Exclude**).
4. Keep talking in-game — on the next reply, the prompt uses the new filter state without ending the conversation.

**Another example:** change a prompt or LLM parameter while chatting; with hot swap on, the next turn uses the updated settings instead of restarting the conversation.

**Notes**

- Hot swap runs when Mantella next processes a game request after config values have changed.
- If hot swap fails or is disabled, Mantella reinitializes the route (classic restart behavior).
- Use [Reload Character Data](#reload-character-data) when you edited CSVs/templates on disk; use hot swap when you changed settings in the UI. See [Reload Character Data](#reload-character-data).

## bios_and_summaries Prompt Variable

### Where to use it

**Prompts** tab — put `{bios_and_summaries}` in multi-NPC prompts (for example **Skyrim Multi-NPC Prompt** or **Skyrim Multi-NPC Director Prompt**) wherever you would otherwise put `{bios}` and `{conversation_summaries}` separately.

### What it does

`{bios_and_summaries}` builds **one block per NPC**: that character's bio, then their conversation summary / memory, kept together, with delimiters so other speakers are told not to use private info.

Instead of a pile of all bios followed by a pile of all summaries, each person gets their own paired section.

### Why it is better than separate bios + summary

The older / default style often looks like:

```
Here are their backgrounds:
{bios}
{conversation_summaries}
```

That **splits** who someone is from what they remember into two distant prompt regions. With several NPCs, the model sees every bio first, then every memory afterward — easy to mix up which summary belongs to which person, or to treat “background” and “memory” as unrelated piles.

`{bios_and_summaries}` keeps each NPC's identity and history **in one place**. That usually improves coherence (tone, relationships, and past events stay attached to the right speaker) and reduces the LLM confusing or cross-wiring characters.

| Approach | Layout | Typical issue |
|----------|--------|----------------|
| `{bios}` + `{conversation_summaries}` | All bios, then all memories | Model can lose which memory goes with which NPC; weaker continuity |
| `{bios_and_summaries}` | Per NPC: bio + that NPC's memory | Clearer pairing → more coherent replies |

You do **not** need both. If you use `{bios_and_summaries}`, drop `{bios}` and `{conversation_summaries}` from that prompt so you are not duplicating the same text.

### Example

Suppose Lydia and Serana are in the conversation.

**Split variables** (`{bios}` then `{conversation_summaries}`) expand roughly like:

```
[This is the beginning of Lydia's bio]
Lydia is a loyal housecarl of Whiterun...
[This is the end of Lydia's bio]

[This is the beginning of Serana's bio]
Serana is a vampire from Castle Volkihar...
[This is the end of Serana's bio]

Lydia: Met {player_name} in Dragonsreach. Agreed to travel together.
Serana: Escaped Dimhollow Crypt with {player_name}. Distrusts the Dawnguard.
```

The bios and memories are in separate stacks — easy for the model to attach Serana's memory to Lydia, or ignore pairing entirely.

**`{bios_and_summaries}`** expands roughly like:

```
[This is the beginning of Lydia's information...]
[This is the beginning of Lydia's bio...]
Lydia is a loyal housecarl of Whiterun...
[This is the end of Lydia's bio...]

[This is the beginning of Lydia's memory...]
Met {player_name} in Dragonsreach. Agreed to travel together.
[This is the end of Lydia's memory...]
[This is the end of Lydia's information...]

[This is the beginning of Serana's information...]
[This is the beginning of Serana's bio...]
Serana is a vampire from Castle Volkihar...
[This is the end of Serana's bio...]

[This is the beginning of Serana's memory...]
Escaped Dimhollow Crypt with {player_name}. Distrusts the Dawnguard.
[This is the end of Serana's memory...]
[This is the end of Serana's information...]
```

Each NPC's bio and memory stay attached — clearer who knows what, better group-chat coherence.

### Quick start

1. Open the **Prompts** tab.
2. Edit a multi-NPC prompt (for example **Skyrim Multi-NPC Prompt** or **Skyrim Multi-NPC Director Prompt**).
3. Replace the split variables with one combined variable.

**Before:**

```
Here are their backgrounds:
{bios}
{conversation_summaries}
```

**After:**

```
Character backgrounds:

{bios_and_summaries}
```

4. Save / leave the field (hot swap will apply on the next request if enabled). Start or continue a multi-NPC conversation and check the prompt log: each NPC should show bio and memory together.

**Notes**

- Bio section filtering still applies inside the bio portion of `{bios_and_summaries}`.
- The director-style multi-NPC prompt already uses `{bios_and_summaries}` by default — that is the recommended pattern for group chats.
