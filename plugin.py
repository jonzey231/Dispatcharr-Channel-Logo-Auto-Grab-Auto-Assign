
import os
import re
import json
import time
import threading
import logging
from urllib import request, error
from urllib.parse import quote as urlquote

LOG = logging.getLogger("plugins.channel_logo_auto_grab_auto_assign")

# GitHub endpoints (no PAT required; we support optional token via env but not required)
GITHUB_TREE_URL = "https://api.github.com/repos/jesmannstl/tvlogos/git/trees/main?recursive=1"
GITHUB_RAW_BASE = "https://raw.githubusercontent.com/jesmannstl/tvlogos/main/"

DEFAULT_LOGOS_DIR = os.environ.get("DISPATCHARR_LOGO_DIR", "/data/logos")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")

# cache index to avoid rate limits
INDEX_BASENAME = ".tvlogos_index.json"
INDEX_TTL_SECS = 6 * 60 * 60  # 6 hours

PLACEHOLDER_PATTERNS = [
    r"(^|/)(logo)(\.(png|jpg|jpeg|webp|svg))?$",
    r"(^|/)(default)(\.(png|jpg|jpeg|webp|svg))?$",
    r"(^|/)download\.jpg$",
]

def is_placeholder(value: str) -> bool:
    if not value:
        return True
    val = str(value).strip()
    for pat in PLACEHOLDER_PATTERNS:
        if re.search(pat, val, re.IGNORECASE):
            return True
    return False

def _headers():
    h = {"User-Agent": "dispatcharr-plugin/channel-logo-auto-grab-auto-assign/1.6.1"}
    # token optional; if present we'll use it, but plugin works fine without one
    if GITHUB_TOKEN:
        h["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    return h

def _http_get(url, as_json=False):
    req = request.Request(url, headers=_headers())
    with request.urlopen(req, timeout=45) as resp:
        data = resp.read()
        if as_json:
            return json.loads(data.decode("utf-8"))
        return data

def normalize_key(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"\s+", " ", s)
    s = s.replace("&", "and")
    s = s.replace(":", "").replace("|", "").replace("/", " ").replace("\\", " ")
    s = re.sub(r"[^a-z0-9. +_-]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def build_index(logos_dir):
    # cached?
    cache_path = os.path.join(logos_dir, INDEX_BASENAME)
    try:
        if os.path.exists(cache_path):
            age = time.time() - os.path.getmtime(cache_path)
            if age < INDEX_TTL_SECS:
                with open(cache_path, "r", encoding="utf-8") as f:
                    idx = json.load(f)
                LOG.info("[INFO] index: using cached (%d entries)", len(idx))
                return idx
    except Exception:
        pass

    try:
        tree = _http_get(GITHUB_TREE_URL, as_json=True)
    except error.HTTPError as e:
        reason = getattr(e, "reason", e)
        LOG.warning("[WARN] index: github api error: %s", reason)
        # fallback to any cache even if stale
        if os.path.exists(cache_path):
            try:
                with open(cache_path, "r", encoding="utf-8") as f:
                    idx = json.load(f)
                LOG.info("[INFO] index: using stale cache (%d entries)", len(idx))
                return idx
            except Exception:
                return {}
        return {}
    except Exception as e:
        LOG.warning("[WARN] index: github error: %s", e)
        if os.path.exists(cache_path):
            try:
                with open(cache_path, "r", encoding="utf-8") as f:
                    idx = json.load(f)
                LOG.info("[INFO] index: using stale cache (%d entries)", len(idx))
                return idx
            except Exception:
                return {}
        return {}

    blobs = tree.get("tree", [])
    index = {}
    exts = (".png", ".webp", ".jpg", ".jpeg", ".svg")
    for node in blobs:
        if node.get("type") != "blob":
            continue
        path = node.get("path") or ""
        if not path.lower().endswith(exts):
            continue
        base = path.rsplit("/", 1)[-1]
        key = re.sub(r"\.(png|webp|jpg|jpeg|svg)$", "", base, flags=re.IGNORECASE)
        norm = normalize_key(key)
        index.setdefault(norm, path)

    # write cache
    try:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(index, f)
    except Exception:
        pass

    LOG.info("[INFO] index: built %d entries via trees api", len(index))
    return index

def pick_logo_path(index, candidates):
    for c in candidates:
        norm = normalize_key(c)
        path = index.get(norm)
        if path:
            return path
    return None

def ensure_dir(p):
    os.makedirs(p, exist_ok=True)

def download_logo(path, logos_dir):
    ensure_dir(logos_dir)
    url = GITHUB_RAW_BASE + urlquote(path)
    data = _http_get(url, as_json=False)
    ext = "." + path.rsplit(".", 1)[-1].lower()
    basename = path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    local_name = basename + ext
    local_path = os.path.join(logos_dir, local_name)
    with open(local_path, "wb") as f:
        f.write(data)
    return local_path, local_name

def _get_channel_and_logo_models():
    try:
        from django.apps import apps as djapps
    except Exception:
        LOG.error("[ERROR] django not available in plugin env")
        return None, None

    Channel = None
    Logo = None

    for m in djapps.get_models():
        try:
            fields = {f.name: f for f in m._meta.get_fields()}
            # heuristic: Channel has 'logo' and either 'name' or 'tvg_id'
            if "logo" in fields and ("name" in fields or "tvg_id" in fields or "tvgId" in fields):
                Channel = m
                try:
                    Logo = fields["logo"].remote_field.model
                except Exception:
                    pass
                break
        except Exception:
            continue

    if not Logo:
        for m in djapps.get_models():
            if m.__name__.lower() == "logo":
                Logo = m
                break

    return Channel, Logo

def _infer_logo_fields(LogoModel):
    file_candidates = ["file", "image", "path", "url", "logo", "src", "filename"]
    name_candidates = ["name", "title", "key", "slug", "label"]
    file_field = None
    name_field = None
    all_fields = {f.name for f in LogoModel._meta.get_fields() if hasattr(f, "attname")}
    for c in file_candidates:
        if c in all_fields:
            file_field = c
            break
    for c in name_candidates:
        if c in all_fields:
            name_field = c
            break
    return file_field, name_field

def _extract_channel_fields(ch):
    tvg_id = getattr(ch, "tvg_id", None) or getattr(ch, "tvgId", None)
    name = getattr(ch, "name", None) or getattr(ch, "tvg_name", None)
    # Try to detect whether a valid logo is already set
    logo_val = None
    try:
        logo_obj = getattr(ch, "logo", None)
        # If it's a foreign-key row, inspect its file-ish attributes
        if logo_obj and not isinstance(logo_obj, str):
            for attr in ("file", "image", "path", "url", "logo", "src", "filename"):
                if hasattr(logo_obj, attr):
                    logo_val = getattr(logo_obj, attr, None)
                    if logo_val:
                        break
        elif isinstance(logo_obj, str):
            logo_val = logo_obj
    except Exception:
        pass

    if not logo_val:
        for attr in ("tvg_logo", "logo_url", "icon", "image", "logo"):
            if hasattr(ch, attr):
                val = getattr(ch, attr, None)
                if val:
                    logo_val = val
                    break
    return tvg_id, name, logo_val

def _assign_logo(ChannelModel, LogoModel, channel, logo_path, logo_name, file_field, name_field):
    key = logo_name.rsplit(".", 1)[0]
    q_kwargs = {}
    if name_field:
        q_kwargs[name_field] = key

    logo_obj = None
    try:
        if q_kwargs:
            try:
                logo_obj = LogoModel.objects.filter(**q_kwargs).first()
            except Exception:
                logo_obj = None
        if not logo_obj:
            logo_obj = LogoModel()
            if name_field:
                setattr(logo_obj, name_field, key)
            if file_field:
                setattr(logo_obj, file_field, logo_path)
            try:
                logo_obj.save()
                LOG.info("Created new logo entry: %s", key)
            except Exception as e:
                LOG.error("[ERROR] could not save logo entry: %s", e)
                return False
        # ensure file path present
        if file_field and not getattr(logo_obj, file_field, None):
            try:
                setattr(logo_obj, file_field, logo_path)
                logo_obj.save()
            except Exception:
                pass

        try:
            setattr(channel, "logo", logo_obj)
            channel.save()
            LOG.info("[INFO] assign-fk: %r -> Logo(%r) via logo", getattr(channel, "name", None), key)
            return True
        except Exception as e:
            LOG.error("[ERROR] fk assign failed: %s", e)
            return False
    except Exception as e:
        LOG.error("[ERROR] logo assign exception: %s", e)
        return False

def _lockfile_path():
    # avoid double-runs from shim + host calling autorun simultaneously
    return "/tmp/channel_logo_auto_grab_auto_assign.lock"

def _acquire_lock():
    p = _lockfile_path()
    try:
        # if a fresh lock exists (<60s), skip
        if os.path.exists(p):
            age = time.time() - os.path.getmtime(p)
            if age < 60:
                LOG.warning("[WARN] startup: another run appears active (lock present); skipping.")
                return False
        with open(p, "w") as f:
            f.write(str(time.time()))
        return True
    except Exception:
        return True

class Plugin:
    name = "Channel Logo Auto\u2011Grab & Auto\u2011Assign"
    version = "1.6.1"
    description = "Downloads channel logos from tvlogos and assigns them to channels missing a proper logo. Writes only to the logos directory."

    def __init__(self):
        self._thread = None

    def autorun(self, context=None):
        def _worker():
            try:
                time.sleep(2)
                self._do_pass(context=context, autorun=True)
            except Exception as e:
                LOG.error("[ERROR] autorun worker crashed: %s", e)
        self._thread = threading.Thread(target=_worker, daemon=True)
        self._thread.start()
        LOG.info("[INFO] startup: autorun (2s)")
        return {"ok": True, "thread": "started"}

    def run(self, action=None, params=None, context=None):
        return self._do_pass(context=context, autorun=False)

    def _do_pass(self, context=None, autorun=False):
        if not _acquire_lock():
            return {"ok": True, "skipped": 0, "updated": 0, "downloaded": 0, "miss": 0, "logos_dir": DEFAULT_LOGOS_DIR, "reason": "locked"}

        logos_dir = os.environ.get("DISPATCHARR_LOGO_DIR", DEFAULT_LOGOS_DIR)
        os.makedirs(logos_dir, exist_ok=True)

        Channel, Logo = _get_channel_and_logo_models()
        if not Channel or not Logo:
            LOG.warning("[WARN] models not discovered; nothing to do")
            return {"ok": False, "reason": "models-not-found", "downloaded": 0, "updated": 0, "skipped": 0, "miss": 0, "logos_dir": logos_dir}

        file_field, name_field = _infer_logo_fields(Logo)
        if not file_field:
            LOG.warning("[WARN] could not infer file field on Logo model; continuing anyway")

        index = build_index(logos_dir)
        downloaded = 0
        updated = 0
        skipped = 0
        miss = 0

        try:
            qs = Channel.objects.all()
        except Exception as e:
            LOG.error("[ERROR] could not query Channel objects: %s", e)
            return {"ok": False, "reason": "query-error", "downloaded": 0, "updated": 0, "skipped": 0, "miss": 0, "logos_dir": logos_dir}

        # Log detected field mapping once
        LOG.info("[INFO] fields: %s", json.dumps({
            "tvg_id": "tvg_id",
            "tvg_name": "name",
            "tvg_logo": None,
            "logo_url": None,
            "logo": "logo",
            "icon": None,
            "image": None,
        }))

        for ch in qs:
            tvg_id, name, logo_val = _extract_channel_fields(ch)

            # Only process empty or placeholder logos
            if logo_val and not is_placeholder(str(logo_val)):
                skipped += 1
                continue

            candidates = []
            if tvg_id:
                candidates.append(str(tvg_id))
            if name:
                candidates.append(str(name))

            path = pick_logo_path(index, candidates)
            if not path:
                miss += 1
                LOG.warning("[WARN] miss: %r", name or tvg_id or "(unknown)")
                continue

            # download locally
            try:
                local_path, local_name = download_logo(path, logos_dir)
                downloaded += 1
            except error.HTTPError as e:
                if e.code == 403:
                    LOG.warning("[WARN] rate limited by GitHub when downloading %s", path)
                else:
                    LOG.warning("[WARN] http error downloading %s: %s", path, e)
                miss += 1
                continue
            except Exception as e:
                LOG.warning("[WARN] error downloading %s: %s", path, e)
                miss += 1
                continue

            # assign FK
            ok = _assign_logo(Channel, Logo, ch, local_path, local_name, file_field, name_field)
            if ok:
                updated += 1

        summary = {"ok": True, "updated": updated, "downloaded": downloaded, "skipped": skipped, "miss": miss, "logos_dir": logos_dir}
        LOG.info("done: %s", json.dumps(summary))
        return summary
