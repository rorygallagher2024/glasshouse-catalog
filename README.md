# Glasshouse Community Catalog

Community screen savers and custom launchers for [Glasshouse](https://github.com/rorygallagher2024/glasshouse).

Third-party add-ons in this catalog are created and maintained by their authors, not by Glasshouse. Each listing shows its author, licence, version, and source repository, and every downloadable package is verified by SHA-256 at install time. (Custom No Signal screens will join in a future release.)

## Submitting your repository

Contributors keep their add-on in their own GitHub repository with tagged releases and register it here via a pull request adding an entry to `registry.json`.

### Screen saver requirements

- Keep all files at the root of the archive (or inside one top-level folder) with a valid `saver.json` (`"manifest": 1`, `"name"`, `"description"`, `"version"`, `"author"`, `"license"`).
- QML screen savers must use `QtQuick 2.4` and `Eos.Window 0.1` so they run cleanly on webOS 4 through 10 (max 200 files, 5 MB total, no symlinks).
- No server-side Node code is executed. Community screen savers cannot declare `"stats"`, `"art"`, or `"news"` in `uses` (those carry dashboard credentials).

### Custom launcher requirements

- Package your web app as a `.zip` (with `index.html` at root or inside one wrapper directory; max 5,000 files, 50 MB download / 100 MB unpacked) or an `.ipk` containing a web app.
- Pure HTML/JS/CSS only; avoid native services or binaries.

### Entry format in `registry.json`

Add an object to `"items"`:

```json
{
  "kind": "screensaver",
  "id": "my-saver",
  "repo": "yourname/glasshouse-my-saver",
  "tag": "v1.0.0",
  "downloadUrl": "https://github.com/yourname/glasshouse-my-saver/releases/download/v1.0.0/my-saver.zip",
  "sha256": "<64-hex-sha256-of-archive>",
  "author": "Your Name",
  "license": "MIT",
  "version": "1.0.0",
  "name": { "en": "My Saver" },
  "description": { "en": "Short description shown on the Screensaver tab." },
  "previewUrl": "https://raw.githubusercontent.com/yourname/glasshouse-my-saver/v1.0.0/preview.png"
}
```

Open a pull request against `main`—CI checks the schema, URLs, and checksum before merge, and rebuilds `catalog.json` automatically on GitHub Pages after merge.
