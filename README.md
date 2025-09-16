# Channel Logo Auto‑Grab & Auto‑Assign

Automatically finds, downloads, and assigns channel logos from the tvlogos repository to Dispatcharr channels that are missing a proper logo.
Skips channels with a healthy non-placeholder logo. Writes only to the server's logos directory (default: `/data/logos`).

- No configuration required.
- No token required.
- Caches a compact index of the repo under the logos directory to reduce network calls.

Logs include:
- `startup: module-level kick (12s)`
- `startup: autorun (2s)`
- `index: built N entries via trees api` or `index: using cached (N entries)`
- `assign-fk: 'Channel' -> Logo('Key') via logo`
- Final `done: {...}` summary.

Instructions:
Download the zip file and import in Dispatcharr's Plugins tab.
