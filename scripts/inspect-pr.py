#!/usr/bin/env python3
"""Inspect catalog PR additions/updates: verify hashes, unpack safely, and scan capabilities."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = ROOT / 'registry.json'

ID_RE = re.compile(r'^[a-z0-9][a-z0-9-]{0,31}$')
SHA256_RE = re.compile(r'^[0-9a-f]{64}$')
HA_NAME_RE = re.compile(r'^[a-z][a-z0-9_]{0,31}$')
TAG_FROM_URL_RE = re.compile(r'/releases/download/([^/]+)/')
REPO_RE = re.compile(r'^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$')

FORBIDDEN_USES = {'stats', 'art', 'news'}
USES = {
    'level': '__TVWEB_LEVEL__',
    'stats': '__TVWEB_URL__',
    'audio': '__TVWEB_AUDIO__',
    'choice': '__TVWEB_CHOICE__',
    'videos': '__TVWEB_VIDEOS__',
    'home-weather': '__TVWEB_HOME_WEATHER__',
    'video-busy': '__TVWEB_VIDEO_BUSY__',
    'ha': '',
    'ha-list': '__TVWEB_HA_LIST__',
    'ha-all': '__TVWEB_HA_ALL__',
    'color': '__TVWEB_COLOR__',
    'art': '__TVWEB_ART__',
    'news': '__TVWEB_NEWS__',
}

MAX_SAVER_FILES = 200
MAX_SAVER_BYTES = 5 * 1024 * 1024
MAX_LAUNCHER_FILES = 5000
MAX_LAUNCHER_DOWNLOAD = 50 * 1024 * 1024
MAX_LAUNCHER_UNPACKED = 100 * 1024 * 1024

SINCE = {
    'topPadding': (2, 6), 'bottomPadding': (2, 6),
    'leftPadding': (2, 6), 'rightPadding': (2, 6), 'padding': (2, 6),
    'advance': (2, 10), 'renderTypeQuality': (2, 15),
    'palette': (2, 13), 'containmentMask': (2, 11),
}
BANNED_ANCHORS = {
    'Column': ('left', 'right', 'horizontalCenter', 'fill', 'centerIn'),
    'Row': ('top', 'bottom', 'verticalCenter', 'fill', 'centerIn'),
}

SAFE_BINARY_EXTS = {
    '.png', '.jpg', '.jpeg', '.webp', '.gif', '.svg', '.ico',
    '.ttf', '.otf', '.woff', '.woff2',
    '.aac', '.mp3', '.ogg', '.wav',
}

SCAN_RULES = [
    ('Network access', re.compile(r'\bXMLHttpRequest\b|\bfetch\s*\(|\bWebSocket\b|https?://')),
    ('webOS / TV services', re.compile(r'\bPalmServiceBridge\b|webOS\.service|luna://|Qt\.openUrlExternally|\bPalmSystem\b')),
    ('Audio / media playback', re.compile(r'\bQtMultimedia\b|\bSoundEffect\b|\bMediaPlayer\b|<audio\b|<video\b|\bAudio\s*\{')),
    ('Dynamic code execution', re.compile(r'\beval\s*\(|\bnew\s+Function\b|Qt\.createQmlObject|\.innerHTML\b')),
]

LAUNCHER_API_RE = re.compile(r'\bGlasshouse\.')


class _RedirectGuard(urllib.request.HTTPRedirectHandler):
    def __init__(self, allow_http_localhost: bool) -> None:
        super().__init__()
        self.allow_http_localhost = allow_http_localhost

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not is_allowed_url(newurl, self.allow_http_localhost):
            raise urllib.error.URLError(f'blocked redirect to non-HTTPS URL: {newurl}')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def is_allowed_url(value: object, allow_http_localhost: bool = False) -> bool:
    if not isinstance(value, str) or '\\' in value or not value.strip():
        return False
    p = urlparse(value.strip())
    if p.scheme == 'https' and p.netloc:
        return True
    if allow_http_localhost and p.scheme == 'http' and p.hostname in ('127.0.0.1', 'localhost'):
        return True
    return False


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


def item_key(raw: dict) -> tuple[str, str]:
    return str(raw.get('kind', '')), str(raw.get('id', ''))


def extract_tag(raw: dict | None) -> str:
    if not raw:
        return ''
    tag = raw.get('tag')
    if isinstance(tag, str) and tag.strip():
        return tag.strip()
    dl = raw.get('downloadUrl')
    if isinstance(dl, str):
        m = TAG_FROM_URL_RE.search(dl)
        if m:
            return m.group(1)
    return ''


def compare_link(old_raw: dict | None, new_raw: dict) -> str:
    if not old_raw or 'path' in new_raw:
        return ''
    old_repo = str(old_raw.get('repo', '')).strip()
    new_repo = str(new_raw.get('repo', '')).strip()
    if not old_repo or old_repo != new_repo or not REPO_RE.match(new_repo):
        return ''
    old_tag = extract_tag(old_raw)
    new_tag = extract_tag(new_raw)
    if not old_tag or not new_tag or old_tag == new_tag:
        return ''
    return f'https://github.com/{new_repo}/compare/{old_tag}...{new_tag}'


def git_show_json(base_ref: str, rel_path: str) -> dict | None:
    res = subprocess.run(
        ['git', 'show', f'{base_ref}:{rel_path}'],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        return None
    return json.loads(res.stdout)


def git_changed_files(base_ref: str) -> set[str]:
    res = subprocess.run(
        ['git', 'diff', '--name-only', f'{base_ref}...HEAD'],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        return set()
    return {line.strip() for line in res.stdout.splitlines() if line.strip()}


def find_target_items(reg: dict, base_ref: str | None, inspect_all: bool) -> list[tuple[dict | None, dict]]:
    items = reg.get('items', [])
    if inspect_all or not base_ref:
        return [(None, item) for item in items if isinstance(item, dict)]

    base_reg = git_show_json(base_ref, 'registry.json') or {'items': []}
    old_by_key = {item_key(it): it for it in base_reg.get('items', []) if isinstance(it, dict)}
    changed_paths = git_changed_files(base_ref)

    selected: list[tuple[dict | None, dict]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        k = item_key(item)
        old_item = old_by_key.get(k)
        if old_item != item:
            selected.append((old_item, item))
            continue
        rel_path = item.get('path')
        if isinstance(rel_path, str):
            prefix = rel_path.strip('/').rstrip('/') + '/'
            if any(p == rel_path.strip('/') or p.startswith(prefix) for p in changed_paths):
                selected.append((old_item, item))
    return selected


def download_file(url_str: str, dest: Path, max_bytes: int, allow_http_localhost: bool) -> tuple[str, int]:
    if not is_allowed_url(url_str, allow_http_localhost):
        raise ValueError(f'downloadUrl must use https://: {url_str}')
    opener = urllib.request.build_opener(_RedirectGuard(allow_http_localhost))
    req = urllib.request.Request(url_str, headers={'User-Agent': 'glasshouse-catalog-inspector/1'})
    h = hashlib.sha256()
    total = 0
    with opener.open(req, timeout=30) as resp, dest.open('wb') as out:
        if not is_allowed_url(resp.geturl(), allow_http_localhost):
            raise ValueError(f'final response URL is not HTTPS: {resp.geturl()}')
        while True:
            chunk = resp.read(65536)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise ValueError(f'download exceeds max size ({max_bytes} bytes)')
            h.update(chunk)
            out.write(chunk)
    return h.hexdigest(), total


def safe_rel_parts(raw_name: str) -> tuple[str, ...]:
    norm = raw_name.replace('\\', '/').lstrip('./')
    if raw_name.startswith('/') or '\\' in raw_name:
        raise ValueError(f'unsafe archive path {raw_name!r}')
    p = PurePosixPath(norm)
    parts = [part for part in p.parts if part not in ('', '.')]
    if not parts or any(part == '..' for part in parts):
        raise ValueError(f'unsafe archive path {raw_name!r}')
    return tuple(parts)


def strip_single_wrapper(entries: list[tuple[tuple[str, ...], bytes]], marker_names: tuple[str, ...]) -> list[tuple[str, bytes]]:
    if not entries:
        return []
    top_dirs = {parts[0] for parts, _ in entries}
    has_root_marker = any(len(parts) == 1 and parts[0] in marker_names for parts, _ in entries)
    if len(top_dirs) == 1 and not has_root_marker and all(len(parts) >= 2 for parts, _ in entries):
        return [('/'.join(parts[1:]), data) for parts, data in entries]
    return [('/'.join(parts), data) for parts, data in entries]


def unpack_zip(archive_path: Path, dest_dir: Path, max_files: int, max_bytes: int, marker_names: tuple[str, ...]) -> list[tuple[str, int]]:
    collected: list[tuple[tuple[str, ...], bytes]] = []
    total = 0
    with zipfile.ZipFile(archive_path, 'r') as zf:
        for info in zf.infolist():
            mode = (info.external_attr >> 16) & 0o170000
            if stat.S_ISLNK(mode):
                raise ValueError(f'symlink entry not allowed: {info.filename}')
            if info.is_dir():
                continue
            parts = safe_rel_parts(info.filename)
            if parts[0] == '__MACOSX':
                continue
            total += info.file_size
            if len(collected) + 1 > max_files or total > max_bytes:
                raise ValueError(f'archive exceeds unpack limits ({max_files} files / {max_bytes} bytes)')
            data = zf.read(info)
            if len(data) != info.file_size:
                total += len(data) - info.file_size
                if total > max_bytes:
                    raise ValueError(f'archive exceeds unpack byte limit ({max_bytes} bytes)')
            collected.append((parts, data))

    rel_entries = strip_single_wrapper(collected, marker_names)
    return write_unpacked(rel_entries, dest_dir)


def extract_ar_data_tar(ipk_path: Path) -> tuple[str, bytes]:
    raw = ipk_path.read_bytes()
    if not raw.startswith(b'!<arch>\n'):
        raise ValueError('not a valid .ipk (ar) archive')
    pos = 8
    while pos + 60 <= len(raw):
        hdr = raw[pos:pos + 60]
        if hdr[58:60] != b'`\n':
            raise ValueError('corrupt ar header in .ipk')
        name = hdr[0:16].decode('ascii', errors='replace').rstrip(' /')
        size = int(hdr[48:58].decode('ascii').strip())
        pos += 60
        payload = raw[pos:pos + size]
        pos += size + (size % 2)
        if name.startswith('data.tar'):
            return name, payload
    raise ValueError('.ipk archive is missing data.tar')


def unpack_ipk(archive_path: Path, dest_dir: Path, max_files: int, max_bytes: int) -> list[tuple[str, int]]:
    _, tar_bytes = extract_ar_data_tar(archive_path)
    collected: list[tuple[tuple[str, ...], bytes]] = []
    total = 0
    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode='r:*') as tf:
        for member in tf.getmembers():
            if member.issym() or member.islnk() or member.ischr() or member.isblk() or member.isfifo():
                raise ValueError(f'unsafe tar entry type not allowed: {member.name}')
            if member.isdir():
                continue
            parts = safe_rel_parts(member.name)
            total += member.size
            if len(collected) + 1 > max_files or total > max_bytes:
                raise ValueError(f'.ipk exceeds unpack limits ({max_files} files / {max_bytes} bytes)')
            fobj = tf.extractfile(member)
            if fobj is None:
                continue
            data = fobj.read()
            collected.append((parts, data))

    # Strip usr/palm/applications/<appId>/ prefix when present.
    stripped: list[tuple[str, bytes]] = []
    app_prefixes = {
        parts[:4] for parts, _ in collected
        if len(parts) >= 5 and parts[:3] == ('usr', 'palm', 'applications')
    }
    if len(app_prefixes) == 1 and all(len(parts) >= 5 and parts[:3] == ('usr', 'palm', 'applications') for parts, _ in collected):
        for parts, data in collected:
            stripped.append(('/'.join(parts[4:]), data))
    else:
        for parts, data in collected:
            stripped.append(('/'.join(parts), data))
    return write_unpacked(stripped, dest_dir)


def write_unpacked(rel_entries: list[tuple[str, bytes]], dest_dir: Path) -> list[tuple[str, int]]:
    out_files: list[tuple[str, int]] = []
    root_res = dest_dir.resolve()
    for rel, data in sorted(rel_entries, key=lambda pair: pair[0]):
        target = (dest_dir / rel).resolve()
        if not str(target).startswith(str(root_res) + os.sep):
            raise ValueError(f'path traversal detected: {rel}')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        out_files.append((rel, len(data)))
    return out_files


def check_qml_source(src: str, rel_path: str) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    lines = src.splitlines()

    stripped = re.sub(r'"(?:[^"\\\n]|\\.)*"', '""', src)
    stripped = re.sub(r"'(?:[^'\\\n]|\\.)*'", "''", stripped)
    stripped = re.sub(r'/\*.*?\*/', '', stripped, flags=re.S)
    stripped = re.sub(r'//[^\n]*', '', stripped)
    opens, closes = stripped.count('{'), stripped.count('}')
    if opens != closes:
        errors.append(f'{rel_path}: unbalanced braces ({opens} open, {closes} close)')

    m = re.search(r'^\s*import\s+QtQuick\s+(\d+)\.(\d+)\s*$', src, re.M)
    if not m:
        errors.append(f'{rel_path}: missing plain "import QtQuick x.y" line')
        ver = (99, 99)
    else:
        ver = (int(m.group(1)), int(m.group(2)))
        if ver > (2, 4):
            warnings.append(f'{rel_path}:{src[:m.start()].count(chr(10)) + 1}: imports QtQuick {ver[0]}.{ver[1]} (QtQuick 2.4 recommended for webOS 4 compatibility)')

    depth = 0
    layouts: list[tuple[int, str]] = []
    for n, line in enumerate(lines, 1):
        code = re.sub(r'//.*$', '', line)
        for prop, since in SINCE.items():
            if re.search(r'(^|[\s{;])' + prop + r'\s*:', code) and ver < since:
                errors.append(f'{rel_path}:{n}: {prop} needs QtQuick {since[0]}.{since[1]}, file imports {ver[0]}.{ver[1]}')
        am = re.search(r'anchors\.(\w+)\s*:', code)
        if am and layouts:
            body_depth, kind = layouts[-1]
            before = code[:am.start()]
            here = depth + before.count('{') - before.count('}')
            if here == body_depth + 1 and am.group(1) in BANNED_ANCHORS[kind]:
                errors.append(f'{rel_path}:{n}: anchors.{am.group(1)} on direct child of {kind} is ignored')
        start = re.match(r'\s*(Row|Column)\s*\{', code)
        o, c = code.count('{'), code.count('}')
        if start and o:
            layouts.append((depth + 1, start.group(1)))
        depth += o - c
        while layouts and depth < layouts[-1][0]:
            layouts.pop()
    return errors, warnings


def validate_screensaver_dir(pkg_dir: Path) -> tuple[list[str], list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    notes: list[str] = []

    manifest_path = pkg_dir / 'saver.json'
    if not manifest_path.is_file():
        return ['missing saver.json'], warnings, notes
    try:
        m = json.loads(manifest_path.read_text(encoding='utf-8'))
    except Exception as exc:
        return [f'saver.json parse error: {exc}'], warnings, notes
    if not isinstance(m, dict) or m.get('manifest') != 1:
        errors.append('saver.json: manifest must be 1')
        return errors, warnings, notes

    if not manifest_text(m.get('name')) or not manifest_text(m.get('description')):
        errors.append('saver.json: name and description (en) are required')
    for req_key in ('version', 'author', 'license'):
        if not isinstance(m.get(req_key), str) or not m[req_key].strip():
            errors.append(f'saver.json: {req_key} is required')

    runtime = m.get('runtime', 'qml')
    if runtime not in ('qml', 'web'):
        errors.append(f'saver.json: invalid runtime {runtime!r}')
    main_rel = m.get('main', 'web/index.html' if runtime == 'web' else 'main.qml')
    if not isinstance(main_rel, str) or not (pkg_dir / main_rel).is_file():
        errors.append(f'saver.json: missing main file {main_rel!r}')
        main_src = ''
    else:
        main_src = (pkg_dir / main_rel).read_text(encoding='utf-8', errors='replace')

    uses = m.get('uses', [])
    if not isinstance(uses, list):
        errors.append('saver.json: uses must be a list')
        uses = []
    for u in uses:
        if u not in USES:
            errors.append(f'saver.json: unknown use {u!r}')
        elif u in FORBIDDEN_USES:
            errors.append(f'saver.json: forbidden use {u!r} for community screensaver')

    if ('choice' in uses) != bool(m.get('choices')):
        errors.append('saver.json: choices and "choice" in uses must be paired')
    if (('ha' in uses or 'ha-all' in uses)) != bool(m.get('entities')):
        errors.append('saver.json: entities and "ha" or "ha-all" in uses must be paired')
    if ('ha-list' in uses) != bool(m.get('entityList')):
        errors.append('saver.json: entityList and "ha-list" in uses must be paired')
    if isinstance(m.get('entities'), dict):
        for k in m['entities']:
            if not HA_NAME_RE.match(str(k)):
                errors.append(f'saver.json: bad entity key {k!r}')

    if uses:
        notes.append('Declared uses: ' + ', '.join(str(u) for u in uses))

    if main_src:
        for use_key, placeholder in USES.items():
            if placeholder and placeholder in main_src and use_key not in uses:
                errors.append(f'{main_rel}: uses {placeholder} without "{use_key}" in uses')

    if runtime == 'qml':
        for qml_file in sorted(pkg_dir.rglob('*.qml')):
            rel = qml_file.relative_to(pkg_dir).as_posix()
            q_err, q_warn = check_qml_source(qml_file.read_text(encoding='utf-8', errors='replace'), rel)
            errors.extend(q_err)
            warnings.extend(q_warn)

    return errors, warnings, notes


def validate_nosignal_dir(pkg_dir: Path) -> tuple[list[str], list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    notes: list[str] = []

    manifest_path = pkg_dir / 'nosignal.json'
    if not manifest_path.is_file():
        return ['missing nosignal.json'], warnings, notes
    try:
        m = json.loads(manifest_path.read_text(encoding='utf-8'))
    except Exception as exc:
        return [f'nosignal.json parse error: {exc}'], warnings, notes
    if not isinstance(m, dict) or m.get('manifest') != 1:
        errors.append('nosignal.json: manifest must be 1')
        return errors, warnings, notes

    if not manifest_text(m.get('name')) or not manifest_text(m.get('description')):
        errors.append('nosignal.json: name and description (en) are required')
    for req_key in ('version', 'author', 'license'):
        if not isinstance(m.get(req_key), str) or not m[req_key].strip():
            errors.append(f'nosignal.json: {req_key} is required')

    main_rel = m.get('main', 'main.qml')
    if not isinstance(main_rel, str) or not main_rel.endswith('.qml') or '/' in main_rel or '\\' in main_rel or not (pkg_dir / main_rel).is_file():
        errors.append(f'nosignal.json: missing or invalid main .qml file {main_rel!r}')

    for qml_file in sorted(pkg_dir.rglob('*.qml')):
        rel = qml_file.relative_to(pkg_dir).as_posix()
        q_err, q_warn = check_qml_source(qml_file.read_text(encoding='utf-8', errors='replace'), rel)
        errors.extend(q_err)
        warnings.extend(q_warn)

    return errors, warnings, notes


def validate_launcher_dir(pkg_dir: Path) -> tuple[list[str], list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    notes: list[str] = []

    main_rel = 'index.html'
    appinfo = pkg_dir / 'appinfo.json'
    if appinfo.is_file():
        try:
            ai = json.loads(appinfo.read_text(encoding='utf-8'))
            if isinstance(ai.get('main'), str) and ai['main'].strip():
                main_rel = ai['main'].strip()
        except Exception as exc:
            errors.append(f'appinfo.json parse error: {exc}')

    if not (pkg_dir / main_rel).is_file():
        errors.append(f'launcher missing start page {main_rel!r}')

    for rel_path in pkg_dir.rglob('*'):
        rel = rel_path.relative_to(pkg_dir).as_posix()
        parts = PurePosixPath(rel).parts
        if 'services' in parts:
            errors.append(f'native service path not allowed in launcher: {rel}')
            break

    return errors, warnings, notes


def scan_capabilities(pkg_dir: Path, kind: str, file_list: list[tuple[str, int]]) -> tuple[list[str], list[str], list[str]]:
    errors: list[str] = []
    warnings_by_cat: dict[str, list[str]] = {}
    notes: list[str] = []
    launcher_api_hits = 0

    for rel, _ in file_list:
        fpath = pkg_dir / rel
        raw = fpath.read_bytes()
        if raw.startswith(b'\x7fELF'):
            errors.append(f'native ELF binary not allowed: {rel}')
            continue

        ext = Path(rel).suffix.lower()
        try:
            text = raw.decode('utf-8')
        except UnicodeDecodeError:
            if ext not in SAFE_BINARY_EXTS:
                warnings_by_cat.setdefault('Unexpected binary files', []).append(f'{rel} (non-UTF-8)')
            continue

        if ext == '.sh' or text.startswith('#!'):
            warnings_by_cat.setdefault('Shell scripts', []).append(f'{rel}:1: script file or shebang present')

        for line_no, line in enumerate(text.splitlines(), 1):
            for cat, pattern in SCAN_RULES:
                if pattern.search(line):
                    snippet = line.strip()
                    if len(snippet) > 100:
                        snippet = snippet[:97] + '...'
                    warnings_by_cat.setdefault(cat, []).append(f'{rel}:{line_no}: `{snippet}`')
            if kind == 'launcher' and LAUNCHER_API_RE.search(line):
                launcher_api_hits += 1

    warnings: list[str] = []
    for cat, hits in warnings_by_cat.items():
        shown = hits[:5]
        extra = f' (+{len(hits) - 5} more)' if len(hits) > 5 else ''
        warnings.append(f'**{cat}** ({len(hits)} match{"es" if len(hits) != 1 else ""}{extra}): ' + '; '.join(shown))

    if kind == 'launcher' and launcher_api_hits:
        notes.append(f'Uses `window.Glasshouse` API ({launcher_api_hits} references)')
    return errors, warnings, notes


def validate_item_dir(pkg_dir: Path, kind: str) -> tuple[list[str], list[str], list[str]]:
    if kind == 'screensaver':
        return validate_screensaver_dir(pkg_dir)
    if kind == 'nosignal':
        return validate_nosignal_dir(pkg_dir)
    return validate_launcher_dir(pkg_dir)


def inspect_entry(old_raw: dict | None, raw: dict, allow_http_localhost: bool) -> dict:
    kind = str(raw.get('kind', ''))
    item_id = str(raw.get('id', ''))
    version = str(raw.get('version', '')).strip()
    old_version = str(old_raw.get('version', '')).strip() if old_raw else ''
    diff_url = compare_link(old_raw, raw)

    errors: list[str] = []
    warnings: list[str] = []
    notes: list[str] = []
    file_list: list[tuple[str, int]] = []
    sha_status = 'in-repo'
    source_label = ''

    if kind not in ('screensaver', 'launcher', 'nosignal') or not ID_RE.match(item_id):
        errors.append(f'invalid kind={kind!r} or id={item_id!r}')

    is_qml_pkg = kind in ('screensaver', 'nosignal')
    if 'path' in raw:
        rel_path = str(raw['path'])
        pkg_dir = (ROOT / rel_path).resolve()
        source_label = f'in-repo (`{rel_path}`)'
        if not str(pkg_dir).startswith(str(ROOT.resolve()) + os.sep) or not pkg_dir.is_dir():
            errors.append(f'invalid local path {rel_path}')
        else:
            max_files = MAX_SAVER_FILES if is_qml_pkg else MAX_LAUNCHER_FILES
            max_bytes = MAX_SAVER_BYTES if is_qml_pkg else MAX_LAUNCHER_UNPACKED
            for p in sorted(pkg_dir.rglob('*')):
                if p.is_symlink():
                    errors.append(f'symlink not allowed: {p.relative_to(pkg_dir).as_posix()}')
                elif p.is_file():
                    rel = p.relative_to(pkg_dir).as_posix()
                    if rel == 'preview.png':
                        continue
                    file_list.append((rel, p.stat().st_size))
            total_bytes = sum(sz for _, sz in file_list)
            if len(file_list) > max_files or total_bytes > max_bytes:
                errors.append(f'exceeds package limits ({len(file_list)} files, {total_bytes} bytes)')
            if not errors:
                v_err, v_warn, v_notes = validate_item_dir(pkg_dir, kind)
                s_err, s_warn, s_notes = scan_capabilities(pkg_dir, kind, file_list)
                errors.extend(v_err + s_err)
                warnings.extend(v_warn + s_warn)
                notes.extend(v_notes + s_notes)
                if is_qml_pkg:
                    mfile = 'saver.json' if kind == 'screensaver' else 'nosignal.json'
                    try:
                        m = json.loads((pkg_dir / mfile).read_text(encoding='utf-8'))
                        version = version or str(m.get('version', '')).strip()
                    except Exception:
                        pass
    else:
        dl = str(raw.get('downloadUrl', '')).strip()
        expected_sha = str(raw.get('sha256', '')).strip().lower()
        repo = str(raw.get('repo', '')).strip()
        src_url = str(raw.get('sourceUrl') or (f'https://github.com/{repo}' if repo else '')).strip()
        source_label = f'[{repo or src_url}]({src_url})' if src_url else '(missing)'
        if diff_url:
            source_label += f' ([compare]({diff_url}))'

        if not manifest_text(raw.get('name')) or not manifest_text(raw.get('description')):
            errors.append('name and description (en) are required')
        for req_key in ('version', 'author', 'license'):
            if not isinstance(raw.get(req_key), str) or not raw[req_key].strip():
                errors.append(f'{req_key} is required')
        if not SHA256_RE.match(expected_sha):
            errors.append('sha256 must be 64 hex characters')
        if not is_allowed_url(dl, allow_http_localhost):
            errors.append(f'downloadUrl must be https://: {dl}')
        if not is_allowed_url(src_url, allow_http_localhost):
            errors.append(f'sourceUrl/repo must be https://: {src_url}')

        if not errors:
            max_dl = MAX_SAVER_BYTES if is_qml_pkg else MAX_LAUNCHER_DOWNLOAD
            max_files = MAX_SAVER_FILES if is_qml_pkg else MAX_LAUNCHER_FILES
            max_unpacked = MAX_SAVER_BYTES if is_qml_pkg else MAX_LAUNCHER_UNPACKED
            tmp_root = Path(tempfile.mkdtemp(prefix='gh-cat-inspect-'))
            try:
                ext = '.ipk' if (kind == 'launcher' and dl.lower().split('?')[0].endswith('.ipk')) else '.zip'
                archive_path = tmp_root / f'pkg{ext}'
                actual_sha, dl_bytes = download_file(dl, archive_path, max_dl, allow_http_localhost)
                if actual_sha != expected_sha:
                    sha_status = 'MISMATCH'
                    errors.append(f'SHA-256 mismatch: expected `{expected_sha}`, got `{actual_sha}`')
                else:
                    sha_status = f'verified ({dl_bytes} B)'
                    unpack_dir = tmp_root / 'unpacked'
                    unpack_dir.mkdir()
                    markers = ('saver.json',) if kind == 'screensaver' else (('nosignal.json',) if kind == 'nosignal' else ('index.html', 'appinfo.json'))
                    if ext == '.ipk':
                        file_list = unpack_ipk(archive_path, unpack_dir, max_files, max_unpacked)
                    else:
                        file_list = unpack_zip(archive_path, unpack_dir, max_files, max_unpacked, markers)
                    v_err, v_warn, v_notes = validate_item_dir(unpack_dir, kind)
                    s_err, s_warn, s_notes = scan_capabilities(unpack_dir, kind, file_list)
                    errors.extend(v_err + s_err)
                    warnings.extend(v_warn + s_warn)
                    notes.extend(v_notes + s_notes)
            except Exception as exc:
                if sha_status == 'in-repo':
                    sha_status = 'error'
                errors.append(f'archive inspection failed: {exc}')
            finally:
                shutil.rmtree(tmp_root, ignore_errors=True)

    return {
        'kind': kind,
        'id': item_id,
        'action': 'update' if old_raw else 'add',
        'version': version or '?',
        'old_version': old_version,
        'source_label': source_label,
        'diff_url': diff_url,
        'sha_status': sha_status,
        'files': file_list,
        'errors': errors,
        'warnings': warnings,
        'notes': notes,
    }


def render_markdown(results: list[dict]) -> str:
    lines = ['<!-- glasshouse-catalog-audit -->', '## Catalog archive inspection', '']
    if not results:
        lines.append('No catalog packages were added or modified in this PR.')
        return '\n'.join(lines) + '\n'

    has_fail = any(r['errors'] for r in results)
    lines.append(f'**Status:** {"FAIL" if has_fail else "PASS"}')
    lines.append('')
    lines.append('| Item | Kind | Change | Source | Checksum | Files | Flags |')
    lines.append('| :--- | :--- | :--- | :--- | :--- | :--- | :--- |')

    for r in results:
        ver_col = f'{r["old_version"]} -> {r["version"]}' if r['old_version'] and r['old_version'] != r['version'] else r['version']
        total_kb = sum(sz for _, sz in r['files']) / 1024.0
        files_col = f'{len(r["files"])} ({total_kb:.1f} KB)' if r['files'] else '0'
        flags = []
        if r['errors']:
            flags.append(f'{len(r["errors"])} error(s)')
        if r['warnings']:
            flags.append(f'{len(r["warnings"])} warning(s)')
        flag_col = ', '.join(flags) if flags else 'clean'
        lines.append(
            f'| `{r["id"]}` | {r["kind"]} | {r["action"]} ({ver_col}) | {r["source_label"]} | {r["sha_status"]} | {files_col} | {flag_col} |'
        )

    lines.append('')
    for r in results:
        lines.append(f'<details><summary><code>{r["kind"]}/{r["id"]}</code> ({len(r["files"])} files)</summary>')
        lines.append('')
        if r['errors']:
            lines.append('**Errors:**')
            for err in r['errors']:
                lines.append(f'- {err}')
            lines.append('')
        if r['warnings']:
            lines.append('**Capability & compatibility warnings (review carefully):**')
            for w in r['warnings']:
                lines.append(f'- {w}')
            lines.append('')
        if r['notes']:
            lines.append('**Notes:**')
            for n in r['notes']:
                lines.append(f'- {n}')
            lines.append('')
        if r['files']:
            lines.append('**Archive contents:**')
            lines.append('```text')
            for rel, sz in r['files'][:200]:
                lines.append(f'{sz:8d}  {rel}')
            if len(r['files']) > 200:
                lines.append(f'... ({len(r["files"]) - 200} more files)')
            lines.append('```')
        lines.append('</details>')
        lines.append('')

    return '\n'.join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description='Inspect Glasshouse catalog PR entries')
    parser.add_argument('--base-ref', help='Git base ref to diff against (e.g. origin/main)')
    parser.add_argument('--all', action='store_true', help='Inspect every item in registry.json')
    parser.add_argument('--out-md', help='Write Markdown audit report to path')
    parser.add_argument('--allow-http-localhost', action='store_true', help='Allow http://localhost downloads (for tests only)')
    args = parser.parse_args()

    reg = json.loads(REGISTRY_PATH.read_text(encoding='utf-8'))
    targets = find_target_items(reg, args.base_ref, args.all)
    results = [inspect_entry(old_raw, new_raw, args.allow_http_localhost) for old_raw, new_raw in targets]

    report = render_markdown(results)
    if args.out_md:
        Path(args.out_md).write_text(report, encoding='utf-8')
    print(report)

    if any(r['errors'] for r in results):
        sys.exit(1)


if __name__ == '__main__':
    main()
