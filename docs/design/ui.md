# BolDoc UI — design plan

## Brief
You speak; BolDoc writes a proper document while you talk and sends it to you. Audience: Indian callers in any
language. Primary job: start a call, watch your words become a finished
document, leave with the file.

## Concept: rough copy → fair copy
Every Indian student writes a *rough copy* first and then a clean *fair copy*. The live call screen is two sheets:

- **Rough copy** — the conversation, on ruled notebook paper with the red margin line. It's messy on purpose.
- **Fair copy** — the document BolDoc writes: a typeset page that updates as versions land.

Talker = rough copy, thinker = fair copy. The design explains the architecture without a diagram.

## Tokens
| Token | Light | Dark ("night desk") | Use |
|---|---|---|---|
| `--paper` | `#F6F8FB` | `#0F1530` | app background — cool notebook white, never cream |
| `--sheet` | `#FFFFFF` | `#161E3D` | fair-copy page, inputs |
| `--graphite` | `#1C2233` | `#E7ECF7` | text (blue graphite, not tinted black) |
| `--muted` | `#5B6478` | `#9AA6C2` | secondary text |
| `--rule` | `#C9D6EE` | `#263361` | notebook rules, hairlines |
| `--ink` | `#2340B8` | `#8EA6FF` | royal-blue fountain-pen ink: every interactive element |
| `--margin` | `#D62F45` | `#FF5D73` | the margin line, live/recording, ending a call — nothing else |
| `--stamp` | `#6A3FB5` | `#B794FF` | violet rubber-stamp ink: delivery confirmations only |

## Type
- **Anek** (Ek Type, Mumbai; variable weight + width; Latin, Devanagari, Tamil…) for interface and rough copy.
  Width is the expressive axis: wordmark and display set wide (wdth 125), body at 100, dense UI at 87.5.
- **Tiro** (Devanagari Hindi, Tamil) for the fair copy: a book serif whose Latin and Indic scripts were designed
  together, so a Hindi letter and an English one look like the same document. Body 17 px / 1.65.
- Scale 1.25: 12.8 · 16 · 20 · 25 · 31.25 · 39 · 48.8 px. Rough-copy text sits on a 28 px ruled baseline grid.
- Numbers (timer, versions, phone) use tabular figures from Anek — no monospace.

## Layout
```
desktop call                                      mobile call
┌ BolDoc   ● Listening 02:14        End call ┐    ┌ ● Listening 02:14     ┐
├──────────────┬─────────────────────────────┤    │ ┌─ fair copy page ──┐ │
│ rough copy   │   ┌── fair copy (A4) ──┐    │    │ │                   │ │
│ ┃ ruled,     │   │ Leave Application  │    │    │ │                   │ │
│ ┃ red margin │   │ …                  │    │    │ └───────────────────┘ │
│ ┃ = voice    │   │ ▍ writing v2       │    │    ├ rough copy (sheet) ▴ ─┤
│ ┃ meter      │   └────────────────────┘    │    │ ┃ last caption line   │
│              │   v1  v2  v3   [Sent]       │    ├ mute  keypad  ( end ) ┤
├──────────────┴─────────────────────────────┤    └───────────────────────┘
│          mute     keypad     ( end )        │
└────────────────────────────────────────────┘
```
Home is a desk: the display line, three ways to start, then *your documents* as a ruled register (rows on
notebook rules), not a grid of cards. Text is left-aligned throughout; only the call dock is centred.

## Principles
1. **One memorable thing**: the red margin line is a live voice meter — your voice moves the notebook margin.
   Everything else stays still. The only other motion is the nib cursor while BolDoc is writing.
2. **Two sheets, two voices**: rough copy is sans on rules; fair copy is serif on a page. Never mixed.
3. **Facts, not hope**: versions and "Sent" stamps appear only when the server confirms them.
4. **Ink is action**: blue = can be pressed; red = live or ending; violet = delivered. No other accents.
5. **Indian scripts are first-class**: every style has a Devanagari/Tamil face from the same family.

## Review against generic defaults (changes made)
- Pane labels were ALL CAPS in the sketch → sentence case ("Rough copy", "Fair copy").
- Version history was a `v1 · v2 · v3` string → discrete version tabs on the page edge.
- Timer was going to be monospace → Anek tabular figures.
- Cards for the documents list → a ruled register, because the notebook is the material.
- Entrance animations on sections → removed; motion is reserved for voice and writing.
