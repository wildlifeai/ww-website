# Authoring guides

> Using Claude Code? The `guide-author` skill
> (`.agents/skills/guide-author/SKILL.md`) automates this conversion — point
> it at a Notion export, Word doc, or pasted text and it produces a compliant
> guide and image folder following the rules below.

Every `.md` file in this folder (except this README) is published at
`https://<site>/guides/<filename-without-extension>` on the next build.
Merging a PR is the entire publishing workflow — no code changes needed.

## File format

Name the file with a short kebab-case slug (it becomes the URL), and start
it with this frontmatter:

```markdown
---
title: Machine Learning Models
description: One-line summary shown on the guides index, and the page's meta description in search results and link previews.
category: Analysis
order: 10
updated: 2026-06-12
---

## First section

Guide content in standard Markdown (GitHub-flavoured: tables, task lists,
and fenced code blocks all work). Don't add a top-level `# heading` — the
page renders the frontmatter title for you.
```

- `category` groups guides on the index page (e.g. Setup, Field work, Analysis).
- `order` sorts within a category (lower first).
- Raw HTML is **not** rendered — keep content in Markdown.

## Images

1. Put web-compressed images (≤ ~200 KB each) in
   `frontend/public/guides/img/<your-guide-slug>/`.
2. Reference them with an absolute path:
   `![Pipeline overview](/guides/img/machine-learning-models/pipeline.png)`

One folder per guide, so removing a guide removes its images.

## Migrating from Notion

Notion → `···` menu → **Export** → *Markdown & CSV* gives you the `.md` and
an image folder. Rename the file to a clean slug, add the frontmatter, move
the images to `public/guides/img/<slug>/`, fix the image paths, and open a PR.
