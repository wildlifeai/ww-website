---
name: guide-author
description: >
  Convert source material (Notion exports, Word docs, pasted text, screenshots of
  instructions) into a published website guide under frontend/src/content/guides/.
  Use whenever someone asks to add, migrate, convert, or update a user guide for
  the Wildlife Watcher website.
---

# Guide Author

Guides are Markdown files in `frontend/src/content/guides/`. Every `.md` there
(except `README.md`) is rendered at `/guides/<filename-without-extension>`. A
merged PR lands on `dev`, whose build is the dev preview; the guide reaches
wildlifewatcher.ai only with the next `dev` to `main` release. The authoring
conventions (frontmatter, Markdown, images, a Notion export) live in
[`frontend/src/content/guides/README.md`](../../../frontend/src/content/guides/README.md):
read it first. This skill is the agent workflow on top of it.

---

# 1. Output contract

Produce exactly two things:

1. `frontend/src/content/guides/<slug>.md`, with a kebab-case slug, which becomes the URL.
2. `frontend/public/guides/img/<slug>/`, the guide's images (only if it has any).

Start the file with the README's frontmatter, all five keys (`title`, `description`,
`category`, `order`, `updated`):

* `category`: reuse an existing category before inventing one. Check current
  guides: `grep -h --exclude=README.md "^category:" frontend/src/content/guides/*.md | sort -u`.
  Intended taxonomy: `Setup`, `Field work`, `Analysis`. Prefer these before
  introducing a new category, and confirm any new one with the user.
* `order`: controls sorting within the category (lower = first). Read the
  sibling guides in the same category and slot sensibly (gaps of 10).
* `updated`: today's date, `YYYY-MM-DD`.

# 2. Content rules

The README's Markdown rules apply: GitHub-flavoured, no top-level `# heading`,
and no raw HTML, so strip any HTML from the source rather than carrying it over.
On top of them:

* Voice: second person ("you"), present tense, plain language for
  conservation field workers, not developers. Spell out jargon on first use.
* Keep steps numbered and actionable; one action per step. Use `>` blockquotes
  for warnings and tips.
* Link to related pages with absolute site paths: `/resources`, `/faq`,
  `/guides/<other-slug>`.

# 3. Accuracy guardrails (non-negotiable)

* **Never invent product facts.** Hardware specs, battery life, AI behaviour,
  and feature availability must come from the source material or from the
  site's existing canon (`frontend/src/pages/FaqPage.tsx`,
  `ResourcesPage.tsx`, existing guides). If the source claims something that
  contradicts the canon (e.g. solar power, which is NOT shipped), flag it to
  the user instead of publishing it.
* Use the canonical wordings: SD card = "FAT32-formatted microSD card,
  32–64 GB (Class 10 or higher)"; battery = "about one month on 4× AA";
  LoRaWAN = "rolling out in early access" (heartbeats and detection alerts,
  never photos, shown on the Realtime page, whose Messages tab lists them as
  they arrive; detail in `lorawan-in-new-zealand.md`).
* If the source is incomplete or ambiguous, leave a `> **TODO (team):** …`
  blockquote rather than guessing. Reviewers can fill it in on the PR.

# 4. Images

The folder and the absolute image paths are in the README. On top of them:

1. Web-compress before committing, to 200 KB or less per image. With Python:
   `python -c "from PIL import Image; im = Image.open(r'<in>'); im.thumbnail((1400, 1400)); im.save(r'<out>', quality=80)"`.
2. Give every image meaningful alt text:
   `![Live preview showing a sharp platform](/guides/img/<slug>/preview.png)`
3. Notion exports name images with long hashes: rename them to short
   descriptive names.

# 5. Source-specific notes

* **Notion export** (export steps in the README): strip Notion artefacts, that
  is the duplicated H1 title, `aside`/callout HTML, the trailing UUID in the
  filename, and absolute notion.so links (convert to plain text or proper
  external links). Move images per §4.
* **Word / PDF / screenshots**: extract the text faithfully; reproduce tables
  as GFM tables. Do not summarise away steps.

# 6. Validate before finishing

From `frontend/`:

```bash
npx vite build
```

* Build must pass, and the guide must appear in the output
  (`grep -rl "<guide title>" dist/assets`).
* Self-check: frontmatter has all five keys; no `# h1` in the body; no raw
  HTML; every image path resolves to a file in `public/guides/img/<slug>/`;
  category matches an existing one (or the user approved a new one).

Then commit the guide + images together. PR title: `docs(guides): add <title>`.
