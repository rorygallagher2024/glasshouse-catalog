# Glasshouse Community Catalog

Community screen savers, custom launchers, and No Signal screens for [Glasshouse](https://github.com/rorygallagher2024/glasshouse).

Third-party add-ons in this catalog are created and maintained by their authors, not by Glasshouse. Each listing shows its author, licence, version, and source repository, and every downloadable package is verified by SHA-256 at install time.

## Submitting your repository

Contributors keep their add-on in their own GitHub repository with tagged releases and register it here via a pull request adding an entry to `registry.json`. You can test an unlisted repository on your TV first using **Install from a GitHub link or .zip URL** under **Community screen savers** or **Home -> Custom -> Community launchers**.

### Screen saver requirements

See [Writing a screen saver](https://rorygallagher2024.github.io/glasshouse/development/WRITING-SCREENSAVERS/) and [Screen saver design principles](https://rorygallagher2024.github.io/glasshouse/development/SCREENSAVER-DESIGN/) for the full guide.

- Keep all files at the root of the archive (or inside one top-level folder) with a valid `saver.json` (`"manifest": 1`, `"name"`, `"description"`, `"version"`, `"author"`, `"license"`).
- QML screen savers must use `QtQuick 2.4` and `Eos.Window 0.1` so they run cleanly on webOS 4 through 10 (max 200 files, 5 MB total, no symlinks).
- No server-side Node code is executed. Community screen savers cannot declare `"stats"`, `"art"`, or `"news"` in `uses` (those carry dashboard credentials).

### Custom launcher requirements

See [Writing a launcher](https://rorygallagher2024.github.io/glasshouse/development/WRITING-LAUNCHERS/) for the `window.Glasshouse` API and webOS compatibility notes.

- Package your web app as a `.zip` (with `index.html` at root or inside one wrapper directory; max 5,000 files, 50 MB download / 100 MB unpacked) or an `.ipk` containing a web app.
- Pure HTML/JS/CSS only; avoid native services or binaries.

### No Signal screen requirements

See [Writing a No Signal screen](https://rorygallagher2024.github.io/glasshouse/development/WRITING-NOSIGNAL/) for the QML API (`hidesLg`, `headingText`, relative assets) and packaging rules.

- Keep all files at the root of the archive (or inside one top-level folder) with a valid `nosignal.json` (`"manifest": 1`, `"name"`, `"description"`, `"version"`, `"author"`, `"license"`).
- Use `import QtQuick 2.4` (max 200 files, 5 MB total, no symlinks).

### Entry format in `registry.json`

Compute the SHA-256 checksum of your release archive (`shasum -a 256 <archive>` or `sha256sum <archive>`), then add an object to `"items"`:

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

Open a pull request against `main`. CI validates the schema, verifies the SHA-256 checksum, inspects the archive contents and capabilities, and posts a review summary on the pull request before merge. After merge, `catalog.json` rebuilds automatically on GitHub Pages.
