---
name: Update .env.example on config changes
description: Always update .env.example when new settings are added to config.py
type: feedback
---

When adding new settings to `app/config.py`, always update `.env.example` in the same change — add the new vars under the appropriate section with inline comments.

**Why:** User explicitly requested this; keeping .env.example in sync saves manual discovery of new required env vars.

**How to apply:** Any time a new field is added to the `Settings` class in `app/config.py`, immediately update `.env.example` before finishing the task.
