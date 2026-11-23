#!/bin/sh
# The installer supplies the immutable commit of the annotated freeze tag. This
# guard runs outside that checkout so operational changes cannot edit the strategy.
set -eu
release=$1
tag=$2
expected_commit=$3
tagged_commit=$(/usr/bin/git -C "$release" rev-parse --verify "refs/tags/$tag^{commit}")
installed_commit=$(/usr/bin/git -C "$release" rev-parse --verify HEAD)
if [ "$tagged_commit" != "$expected_commit" ] || [ "$installed_commit" != "$expected_commit" ]; then
    echo "A126 paper update stopped: installed checkout or tag moved from the frozen commit." >&2
    exit 1
fi
if ! /usr/bin/git -C "$release" diff --quiet --no-ext-diff --ignore-submodules=none "$expected_commit" --; then
    echo "A126 paper update stopped: a tracked release file differs from the frozen commit." >&2
    exit 1
fi
exec "$release/.venv/bin/radar" forward-update
