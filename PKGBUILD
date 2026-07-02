# Maintainer: Nguyen <s02179@bvisvietnam.com>
pkgname=lsf-git
pkgver=1.0.0
pkgrel=1
pkgdesc="Least Slack First -- terminal assignment scheduler"
arch=('any')
url="https://github.com/suhao49/lsf"
license=('MIT')
depends=('python>=3.9' 'python-textual')
makedepends=('python-build' 'python-installer' 'python-setuptools' 'python-wheel')
# tomli only needed on Python < 3.11; python-tomli is in the AUR/extra
# Arch ships Python 3.11+ so this is effectively never needed, but listed for correctness
optdepends=('python-tomli: TOML parsing on Python < 3.11')
provides=('lsf')
conflicts=('lsf')

# No source array -- place this PKGBUILD in the repo root and run: makepkg -si

build() {
    cd "$startdir"
    python -m build --wheel --no-isolation --outdir "$srcdir/dist"
}

package() {
    cd "$startdir"
    python -m installer --destdir="$pkgdir" "$srcdir"/dist/*.whl

    # Example config
    install -Dm644 config/config.toml.example \
        "$pkgdir/usr/share/lsf/config.toml.example"

    # License
    install -Dm644 LICENSE \
        "$pkgdir/usr/share/licenses/$pkgname/LICENSE"
}
