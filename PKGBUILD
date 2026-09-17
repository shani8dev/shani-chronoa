# shani-chronoa PKGBUILD
# Maintainer: Shrinivas Vishnu Kumbhar <shrinivas.v.kumbhar@gmail.com>

pkgname=shani-chronoa
pkgver=0.1.0
pkgrel=1
pkgdesc="Local AI assistant with privacy-first design, integrated into Shanios"
arch=('any')
url="https://github.com/shani8dev/shani-pkgbuilds/tree/main/shani-chronoa"
license=('GPL-3.0-only')
depends=(
    'python'
    'python-gobject'
    'gtk4'
    'python-httpx'
    'whisper.cpp'
    'piper-tts'
    'pipewire'
    'wireplumber'
    'libnotify'
    'upower'
)
optdepends=(
    'python-openwakeword: hands-free "hey chronoa" wake-word activation (AUR, not in official repos)'
    'python-numpy: required by python-openwakeword for wake-word activation'
    'python-mcp: shani-chronoa-mcp, exposes Chronoa skills as an MCP server for Claude Desktop/Claude Code/Cursor (AUR, not in official repos)'
)
install="$pkgname.install"

package() {
    install -d "$pkgdir/usr/bin"
    install -d "$pkgdir/usr/lib/shani-chronoa"
    install -d "$pkgdir/usr/share/applications"
    install -d "$pkgdir/usr/share/glib-2.0/schemas"
    install -d "$pkgdir/usr/share/pixmaps"
    install -d "$pkgdir/usr/share/icons/hicolor/scalable/apps"

    # Binaries
    install -Dm755 "$startdir/usr/bin/shani-chronoa" "$pkgdir/usr/bin/shani-chronoa"
    install -Dm755 "$startdir/usr/bin/shani-chronoa-mcp" "$pkgdir/usr/bin/shani-chronoa-mcp"

    # Python package
    cp -r "$startdir/usr/lib/shani-chronoa" "$pkgdir/usr/lib/"

    # Desktop entry
    install -Dm644 "$startdir/usr/share/applications/shani-chronoa.desktop" "$pkgdir/usr/share/applications/"

    # GSettings schema
    install -Dm644 "$startdir/usr/share/glib-2.0/schemas/org.shani.chronoa.gschema.xml" "$pkgdir/usr/share/glib-2.0/schemas/"

    # Icon
    install -Dm644 "$startdir/usr/share/pixmaps/shani-chronoa.svg" "$pkgdir/usr/share/pixmaps/"
    install -Dm644 "$startdir/usr/share/pixmaps/shani-chronoa.svg" "$pkgdir/usr/share/icons/hicolor/scalable/apps/shani-chronoa.svg"

    # Never ship bytecode, even if a dev ran the app from the source tree
    find "$pkgdir" -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
    find "$pkgdir" -type f -name '*.pyc' -delete 2>/dev/null || true
}
