#!/bin/bash
#
# Build pure-Python wheels for the two dependencies that don't publish one.
#
# Everything else in Lute's dependency tree already has a `py3-none-any` wheel
# on PyPI, so briefcase can install it for iOS directly.  MarkupSafe and PyYAML
# only publish C-extension wheels -- both have a pure-Python fallback, it's just
# never shipped as a wheel.  So we build it here, into ios/wheels/, which
# pyproject.toml points pip at via `--find-links`.
#
# Run this once before `briefcase create iOS`.

set -euo pipefail

IOSDIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="$IOSDIR/.venv/bin/python"
PIP="$IOSDIR/.venv/bin/pip"
WHEELS="$IOSDIR/wheels"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

MARKUPSAFE_VERSION=3.0.3
PYYAML_VERSION=6.0.3

if [ ! -x "$PYTHON" ]; then
    echo "Missing $PYTHON -- create it first:" >&2
    echo "  python3.11 -m venv ios/.venv && ios/.venv/bin/pip install briefcase" >&2
    exit 1
fi

cd "$IOSDIR"
"$PIP" install --quiet setuptools wheel

mkdir -p "$WHEELS"
rm -f "$WHEELS"/*.whl

# MarkupSafe's setup.py builds the C extension, and falls back to the pure
# build if the compiler fails.  Point CC at a command that always fails to
# take that fallback deliberately.  Its build dependency (setuptools) is pure,
# so nothing else needs a working compiler.
echo "Building pure MarkupSafe $MARKUPSAFE_VERSION..."
CC=/usr/bin/false "$PIP" wheel --no-binary=MarkupSafe --no-deps --quiet \
    -w "$WHEELS" "MarkupSafe==$MARKUPSAFE_VERSION"

# PyYAML can't use the same trick: its build dependency is Cython, which does
# need a compiler.  It has a supported "no C extension" switch instead, but it
# only skips *building* the extension -- setup.py still declares ext_modules,
# so bdist_wheel stamps a platform tag on a wheel whose contents are pure.
# Build it, then retag.
echo "Building pure PyYAML $PYYAML_VERSION..."
"$PIP" download --no-binary=:all: --no-deps --quiet --dest "$WORK" "PyYAML==$PYYAML_VERSION"
tar xzf "$WORK/pyyaml-$PYYAML_VERSION.tar.gz" -C "$WORK"
(
    cd "$WORK/pyyaml-$PYYAML_VERSION"
    "$PYTHON" setup.py --quiet --without-libyaml bdist_wheel -d "$WORK/dist" > /dev/null
)
"$PYTHON" -m wheel tags --remove \
    --python-tag py3 --abi-tag none --platform-tag any \
    "$WORK"/dist/PyYAML-*.whl > /dev/null
cp "$WORK"/dist/PyYAML-*.whl "$WHEELS/"

# A stray _yaml*.so would mean the C extension got built after all, and the
# wheel would fail to import on iOS.
if unzip -l "$WHEELS"/PyYAML-*.whl | grep -q '\.so'; then
    echo "ERROR: PyYAML wheel contains a compiled extension." >&2
    exit 1
fi

echo
echo "Built into $WHEELS:"
ls -1 "$WHEELS"
