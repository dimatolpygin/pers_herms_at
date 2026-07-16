---
name: avoid-ai-writing
description: Audit and rewrite content to remove AI writing patterns. Use when asked to remove AI-isms, clean up AI writing, or make text sound less like AI. Three modes - rewrite (default), detect (flag only), edit (in-place file fix).
version: 3.11.0
license: MIT
metadata:
  author: Conor Bronsdon
  tags: [writing, editing, voice, quality]
---

# Avoid AI Writing — Audit and Rewrite

You are editing content to remove AI writing patterns that make text sound machine-generated.

## Modes
- **rewrite** (default): Flag AI-isms and rewrite the text to fix them
- **detect**: Flag AI-isms only, no rewriting
- **edit**: Edit a file in place with minimal targeted fixes

## What to remove or fix

### Formatting
- **Em dashes (— and --)**: Replace with commas, periods, parentheses, or rewrite as two sentences. Target: zero. Hard max: one per 1,000 words.
- **Bold overuse**: Strip bold from most phrases. One bolded phrase per major section at most.
- **Emoji in headers**: Remove. Exception: social posts may use one or two emoji sparingly.
- **Excessive bullet lists**: Convert bullet-heavy sections into prose paragraphs.
- **Curly quotation marks in plain-text**: Replace with straight quotes.

### Sentence structure
- **"It's not X — it's Y" / negation-then-correction**: Rewrite as direct positive statement.
- **Hollow intensifiers**: Cut `genuine`/`genuinely`, `truly`, `quite frankly`, `to be honest`, `it's worth noting that`. Just state the fact.
- **Vague endorsement**: Cut `worth reading`, `worth a look`, `worth exploring`. Say why instead.
- **Hedging**: Cut `perhaps`, `could potentially`, `it's important to note that`. Be direct.
- **Compulsive rule of three**: Vary groupings. Max one "X, Y, and Z" pattern per piece.

### Words to replace (Tier 1 — always)
Replace: `delve` → `explore`, `landscape` → `field/industry`, `tapestry` → describe, `realm` → `area`, `paradigm` → `model`, `embark` → `start`, `beacon` → rewrite, `testament to` → `shows`, `robust` → `strong`, `comprehensive` → `thorough`, `cutting-edge` → `latest`, `leverage` → `use`, `pivotal` → `important`, `underscores` → `highlights`, `meticulous` → `careful`, `seamless` → `smooth`, `game-changer` → describe what changed, `utilize` → `use`.

### Russian-specific additions
Replace: `в современном мире`, `в эпоху`, `ключевой` (without specifics), `оптимальный`, `инновационный`, `уникальный`, `эксклюзивный`, `премиальный` (without specifics), `идеальный`, `потрясающий`, `важно отметить`, `следует подчеркнуть`.

## Output format (rewrite mode)
1. **Issues found**: Bulleted list of every AI-ism with quoted text
2. **Rewritten version**: Full clean text
3. **What changed**: Summary of major edits
4. **Second-pass audit**: Re-check for remaining tells, fix or confirm clean

## Tone calibration
1. Vary sentence length — mix short with long. Fragments are fine.
2. Be concrete — replace vague claims with numbers, names, dates, examples.
3. Have a voice — use first person, state preferences, show reactions where appropriate.
4. Cut the neutrality — humans have opinions. If the piece takes a position, take it.
5. Earn your emphasis — don't tell the reader something is interesting. Make it interesting.