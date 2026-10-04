#!/usr/bin/env bash
set -euo pipefail

branch="$(git branch --show-current)"

case "$branch" in
    main|next)
        ;;
    *)
        echo "Not syncing component mirrors from branch: $branch"
        exit 0
        ;;
esac

sync_component() {
    prefix="$1"
    remote="$2"

    echo
    echo "Checking $prefix -> $remote/$branch"

    sha="$(git subtree split --prefix="$prefix" HEAD)"

    remote_sha="$(
        git ls-remote --heads "$remote" "refs/heads/$branch" |
        awk '{print $1}'
    )"

    if [[ "$sha" == "$remote_sha" ]]; then
        echo "$remote/$branch already synchronized."
        return
    fi

    echo "Updating $remote/$branch"
    echo "  old: ${remote_sha:-<new branch>}"
    echo "  new: $sha"

    git push "$remote" \
        "$sha:refs/heads/$branch"
}

sync_component "statline/core" core
sync_component "statline/app" app
sync_component "statline/gateway" gateway

echo
echo "StatLine component synchronization complete."