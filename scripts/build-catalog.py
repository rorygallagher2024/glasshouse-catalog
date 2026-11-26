#!/usr/bin/env python3
"""Validate registry.json and build the static GitHub Pages catalog."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import zipfile
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = ROOT / 'registry.json'
SITE_DIR = ROOT / 'site'

ID_RE = re.compile(r'^[a-z0-9][a-z0-9-]{0,31}$')
SHA256_RE = re.compile(r'^[0-9a-f]{64}$')
HA_NAME_RE = re.compile(r'^[a-z][a-z0-9_]{0,31}$')
FORBIDDEN_USES = {'stats', 'art', 'news'}
ALLOWED_USES = {
    'level', 'stats', 'audio', 'choice', 'videos', 'home-weather',
    'video-busy', 'ha', 'ha-list', 'ha-all', 'color', 'art', 'news',
}
MAX_SAVER_FILES = 200
MAX_SAVER_BYTES = 5 * 1024 * 1024
MAX_LAUNCHER_FILES = 5000
MAX_LAUNCHER_BYTES = 50 * 1024 * 1024
FIXED_ZIP_DT = (2026, 1, 1, 0, 0, 0)


def fail(msg: str) -> None:
    print(f'error: {msg}', file=sys.stderr)
    sys.exit(1)


def is_https(value: object) -> bool:
    if not isinstance(value, str) or not value.startswith('https://'):
        return False
    p = urlparse(value)
    return bool(p.scheme == 'https' and p.netloc and '\\' not in value)


def manifest_text(val: object) -> dict[str, str] | None:
    if isinstance(val, str) and val.strip():
        return {'en': val.strip()}
    if isinstance(val, dict) and isinstance(val.get('en'), str) and val['en'].strip():
        out = {}
        for k, v in val.items():
            if isinstance(k, str) and isinstance(v, str) and v.strip():
                out[k] = v.strip()
        return out
    return None


def collect_files(pkg_dir: Path, max_files: int, max_bytes: int) -> list[tuple[Path, str]]:
    files: list[tuple[Path, str]] = []
    total = 0
    for cur, dirs, names in os.walk(pkg_dir, followlinks=False):
        dirs.sort()
        names.sort()
        cur_p = Path(cur)
        for d in list(dirs):
            if (cur_p / d).is_symlink():
                fail(f'{pkg_dir}: symlink directory {d} not allowed')
        for n in names:
            p = cur_p / n
            if p.is_symlink():
                fail(f'{pkg_dir}: symlink file {n} not allowed')
            rel = p.relative_to(pkg_dir).as_posix()
            if rel == 'preview.png':
                continue
            size = p.stat().st_size
            total += size
            files.append((p, rel))
    if len(files) > max_files or total > max_bytes:
        fail(f'{pkg_dir}: exceeds limits ({len(files)} files, {total} bytes)')
    return files


def build_zip(files: list[tuple[Path, str]], out_zip: Path) -> tuple[str, int]:
    out_zip.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_zip, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
        for src, rel in files:
            data = src.read_bytes()
            zi = zipfile.ZipInfo(rel, date_time=FIXED_ZIP_DT)
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = 0o644 << 16
            zf.writestr(zi, data)
    raw = out_zip.read_bytes()
    return hashlib.sha256(raw).hexdigest(), len(raw)


def validate_local_saver(pkg_dir: Path, entry_id: str) -> tuple[dict, list[tuple[Path, str]]]:
    manifest_file = pkg_dir / 'saver.json'
    if not manifest_file.is_file():
        fail(f'{pkg_dir}: missing saver.json')
    m = json.loads(manifest_file.read_text(encoding='utf-8'))
    if not isinstance(m, dict) or m.get('manifest') != 1:
        fail(f'{manifest_file}: manifest must be 1')
    name = manifest_text(m.get('name'))
    desc = manifest_text(m.get('description'))
    version = m.get('version')
    author = m.get('author')
    license_id = m.get('license')
    if not name or not desc or not isinstance(version, str) or not version.strip():
        fail(f'{manifest_file}: missing name, description, or version')
    if not isinstance(author, str) or not author.strip():
        fail(f'{manifest_file}: author is required for community savers')
    if not isinstance(license_id, str) or not license_id.strip():
        fail(f'{manifest_file}: license is required for community savers')

    runtime = m.get('runtime', 'qml')
    if runtime not in ('qml', 'web'):
        fail(f'{manifest_file}: runtime must be qml or web')
    main = m.get('main', 'web/index.html' if runtime == 'web' else 'main.qml')
    if not isinstance(main, str) or not (pkg_dir / main).is_file():
        fail(f'{manifest_file}: missing main file {main}')

    uses = m.get('uses', [])
    if not isinstance(uses, list):
        fail(f'{manifest_file}: uses must be a list')
    for u in uses:
        if u not in ALLOWED_USES:
            fail(f'{manifest_file}: unknown use {u!r}')
        if u in FORBIDDEN_USES:
            fail(f'{manifest_file}: community savers cannot use {u!r}')
    if ('ha' in uses or 'ha-all' in uses):
        ents = m.get('entities')
        if not isinstance(ents, dict) or not ents:
            fail(f'{manifest_file}: entities required with ha')
        for k in ents:
            if not HA_NAME_RE.match(str(k)):
                fail(f'{manifest_file}: bad entity key {k!r}')

    files = collect_files(pkg_dir, MAX_SAVER_FILES, MAX_SAVER_BYTES)
    meta = {
        'id': entry_id,
        'kind': 'screensaver',
        'name': name,
        'description': desc,
        'author': author.strip(),
        'license': license_id.strip(),
        'version': version.strip(),
        'uses': uses,
    }
    if isinstance(m.get('requires'), dict):
        meta['requires'] = m['requires']
    return meta, files


def validate_local_nosignal(pkg_dir: Path, entry_id: str) -> tuple[dict, list[tuple[Path, str]]]:
    manifest_file = pkg_dir / 'nosignal.json'
    if not manifest_file.is_file():
        fail(f'{pkg_dir}: missing nosignal.json')
    m = json.loads(manifest_file.read_text(encoding='utf-8'))
    if not isinstance(m, dict) or m.get('manifest') != 1:
        fail(f'{manifest_file}: manifest must be 1')
    name = manifest_text(m.get('name'))
    desc = manifest_text(m.get('description'))
    version = m.get('version')
    author = m.get('author')
    license_id = m.get('license')
    if not name or not desc or not isinstance(version, str) or not version.strip():
        fail(f'{manifest_file}: missing name, description, or version')
    if not isinstance(author, str) or not author.strip():
        fail(f'{manifest_file}: author is required for community No Signal screens')
    if not isinstance(license_id, str) or not license_id.strip():
        fail(f'{manifest_file}: license is required for community No Signal screens')
    main_file = m.get('main', 'main.qml')
    if not isinstance(main_file, str) or not main_file.endswith('.qml') or '/' in main_file or '\\' in main_file or not (pkg_dir / main_file).is_file():
        fail(f'{manifest_file}: missing or invalid main .qml file {main_file!r}')
    files = collect_files(pkg_dir, MAX_SAVER_FILES, MAX_SAVER_BYTES)
    meta = {
        'id': entry_id,
        'kind': 'nosignal',
        'name': name,
        'description': desc,
        'author': author.strip(),
        'license': license_id.strip(),
        'version': version.strip(),
    }
    if isinstance(m.get('requires'), dict):
        meta['requires'] = m['requires']
    return meta, files


def validate_local_launcher(pkg_dir: Path, entry_id: str) -> tuple[dict, list[tuple[Path, str]]]:
    manifest_file = pkg_dir / 'launcher.json'
    m = {}
    if manifest_file.is_file():
        m = json.loads(manifest_file.read_text(encoding='utf-8'))
    if not (pkg_dir / 'index.html').is_file() and not isinstance(m.get('main'), str):
        fail(f'{pkg_dir}: missing index.html')
    name = manifest_text(m.get('name')) or {'en': entry_id}
    desc = manifest_text(m.get('description')) or {'en': ''}
    version = m.get('version')
    author = m.get('author')
    license_id = m.get('license')
    if not isinstance(version, str) or not version.strip():
        fail(f'{pkg_dir}: launcher.json requires version')
    if not isinstance(author, str) or not author.strip():
        fail(f'{pkg_dir}: launcher.json requires author')
    if not isinstance(license_id, str) or not license_id.strip():
        fail(f'{pkg_dir}: launcher.json requires license')
    files = collect_files(pkg_dir, MAX_LAUNCHER_FILES, MAX_LAUNCHER_BYTES)
    meta = {
        'id': entry_id,
        'kind': 'launcher',
        'name': name,
        'description': desc,
        'author': author.strip(),
        'license': license_id.strip(),
        'version': version.strip(),
    }
    return meta, files


def main() -> None:
    if not REGISTRY_PATH.is_file():
        fail('registry.json not found')
    reg = json.loads(REGISTRY_PATH.read_text(encoding='utf-8'))
    base_url = str(reg.get('baseUrl', '')).rstrip('/')
    repo_url = str(reg.get('repoUrl', '')).rstrip('/')
    if not is_https(base_url) or not is_https(repo_url):
        fail('baseUrl and repoUrl must be https:// URLs')

    items_raw = reg.get('items')
    if not isinstance(items_raw, list):
        fail('registry.json items must be an array')

    if SITE_DIR.exists():
        shutil.rmtree(SITE_DIR)
    (SITE_DIR / 'dist').mkdir(parents=True, exist_ok=True)
    (SITE_DIR / 'previews').mkdir(parents=True, exist_ok=True)

    seen: set[tuple[str, str]] = set()
    catalog_items: list[dict] = []

    for raw in items_raw:
        if not isinstance(raw, dict):
            fail('each item must be an object')
        kind = raw.get('kind')
        item_id = raw.get('id')
        if kind not in ('screensaver', 'launcher', 'nosignal'):
            fail(f'invalid kind {kind!r}')
        if not isinstance(item_id, str) or not ID_RE.match(item_id):
            fail(f'invalid id {item_id!r}')
        if (kind, item_id) in seen:
            fail(f'duplicate ({kind}, {item_id})')
        seen.add((kind, item_id))

        if 'path' in raw:
            rel_path = str(raw['path'])
            pkg_dir = (ROOT / rel_path).resolve()
            if not str(pkg_dir).startswith(str(ROOT.resolve()) + os.sep) or not pkg_dir.is_dir():
                fail(f'invalid local path {rel_path}')
            if kind == 'screensaver':
                entry, files = validate_local_saver(pkg_dir, item_id)
            elif kind == 'nosignal':
                entry, files = validate_local_nosignal(pkg_dir, item_id)
            else:
                entry, files = validate_local_launcher(pkg_dir, item_id)
            zip_name = f"{kind}-{item_id}-{entry['version']}.zip"
            sha, size = build_zip(files, SITE_DIR / 'dist' / zip_name)
            entry['sourceUrl'] = raw.get('sourceUrl') if is_https(raw.get('sourceUrl')) else f'{repo_url}/tree/main/{rel_path}'
            entry['downloadUrl'] = f'{base_url}/dist/{zip_name}'
            entry['sha256'] = sha
            entry['size'] = size
            preview = pkg_dir / 'preview.png'
            if preview.is_file():
                p_name = f'{kind}-{item_id}.png'
                shutil.copyfile(preview, SITE_DIR / 'previews' / p_name)
                entry['previewUrl'] = f'{base_url}/previews/{p_name}'
            elif is_https(raw.get('previewUrl')):
                entry['previewUrl'] = raw['previewUrl']
        else:
            name = manifest_text(raw.get('name'))
            desc = manifest_text(raw.get('description'))
            version = str(raw.get('version', '')).strip()
            author = str(raw.get('author', '')).strip()
            license_id = str(raw.get('license', '')).strip()
            dl = str(raw.get('downloadUrl', '')).strip()
            sha = str(raw.get('sha256', '')).strip().lower()
            repo = str(raw.get('repo', '')).strip()
            src_url = str(raw.get('sourceUrl') or (f'https://github.com/{repo}' if repo else '')).strip()
            if not name or not desc or not version or not author or not license_id:
                fail(f'{item_id}: name, description, version, author, and license are required')
            if not is_https(dl) or not SHA256_RE.match(sha) or not is_https(src_url):
                fail(f'{item_id}: valid https downloadUrl, 64-hex sha256, and https sourceUrl/repo are required')
            entry = {
                'id': item_id,
                'kind': kind,
                'name': name,
                'description': desc,
                'author': author,
                'license': license_id,
                'version': version,
                'sourceUrl': src_url,
                'downloadUrl': dl,
                'sha256': sha,
            }
            if isinstance(raw.get('size'), int) and raw['size'] > 0:
                entry['size'] = raw['size']
            if is_https(raw.get('previewUrl')):
                entry['previewUrl'] = raw['previewUrl']
            if isinstance(raw.get('requires'), dict):
                entry['requires'] = raw['requires']
        catalog_items.append(entry)

    revoked_items: list[dict] = []
    revoked_raw = reg.get('revoked', [])
    if not isinstance(revoked_raw, list):
        fail('registry.json revoked must be an array')
    for rev in revoked_raw:
        if not isinstance(rev, dict):
            fail('each revoked entry must be an object')
        rk = rev.get('kind')
        rid = rev.get('id')
        reason = rev.get('reason')
        if rk not in ('screensaver', 'launcher', 'nosignal') or not isinstance(rid, str) or not ID_RE.match(rid):
            fail(f'invalid revoked target ({rk!r}, {rid!r})')
        if not isinstance(reason, str) or not reason.strip():
            fail(f'revoked entry ({rk}, {rid}) requires a non-empty reason')
        revoked_items.append({'kind': rk, 'id': rid, 'reason': reason.strip()})

    catalog = {
        'catalog': 1,
        'items': catalog_items,
    }
    if revoked_items:
        catalog['revoked'] = revoked_items
    (SITE_DIR / 'catalog.json').write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8',
    )
    (SITE_DIR / 'index.html').write_text(
        '<!doctype html><meta charset="utf-8"><title>Glasshouse Community Catalog</title>'
        '<p>This hosts <a href="catalog.json">catalog.json</a> for '
        '<a href="https://github.com/rorygallagher2024/glasshouse">Glasshouse</a>.</p>\n',
        encoding='utf-8',
    )
    print(f'Built catalog.json with {len(catalog_items)} items')


if __name__ == '__main__':
    main()
