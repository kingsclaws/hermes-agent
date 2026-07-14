#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
webui_root=${LEX_WEBUI_SOURCE:-/root/workspace/hermes-webui-lex}
target_image=${LEX_PLUGIN_IMAGE:-localhost:6678/lex-hermes:plugin}
core_image=${LEX_PLUGIN_CORE_IMAGE:-localhost:6678/lex-hermes:plugin-core}
core_dockerfile=${LEX_PLUGIN_CORE_DOCKERFILE:-$repo_root/Dockerfile.lex-plugin-core}

if [[ ! -f "$webui_root/server.py" ]]; then
    echo "error: WebUI source not found at $webui_root" >&2
    exit 1
fi

revision=$(git -C "$repo_root" rev-parse HEAD)
if [[ -n $(git -C "$repo_root" status --porcelain --untracked-files=normal) ]]; then
    revision="${revision}-dirty"
fi
source_digest=$(
    cd "$repo_root"
    while IFS= read -r -d '' path; do
        if [[ -L "$path" ]]; then
            printf 'symlink  %s  %s\n' "$path" "$(readlink "$path")"
        elif [[ -f "$path" ]]; then
            sha256sum "$path"
        fi
    done < <(git ls-files -co --exclude-standard -z | sort -z) \
        | sha256sum \
        | awk '{print $1}'
)
webui_revision=$(git -C "$webui_root" rev-parse HEAD)
if [[ -n $(git -C "$webui_root" status --porcelain --untracked-files=normal) ]]; then
    webui_revision="${webui_revision}-dirty"
fi

echo "[build] plugin revision: $revision"
echo "[build] plugin source digest: $source_digest"
echo "[build] WebUI revision: $webui_revision"

docker build \
    --build-arg "HERMES_GIT_SHA=${revision%-dirty}" \
    --tag "$core_image" \
    --file "$core_dockerfile" \
    "$repo_root"

staging_dir=$(mktemp -d)
trap 'rm -rf "$staging_dir"' EXIT
cp "$repo_root/Dockerfile.lex-plugin" "$repo_root/start-lex-plugin.sh" "$staging_dir/"
mkdir "$staging_dir/webui"
(
    cd "$webui_root"
    git ls-files -co --exclude-standard -z | tar --null -T - -cf -
) | tar -xf - -C "$staging_dir/webui"

docker build \
    --build-arg "BASE_IMAGE=$core_image" \
    --build-arg "LEX_SOURCE_REVISION=$revision" \
    --build-arg "LEX_SOURCE_DIGEST=$source_digest" \
    --build-arg "WEBUI_SOURCE_REVISION=$webui_revision" \
    --tag "$target_image" \
    --file "$staging_dir/Dockerfile.lex-plugin" \
    "$staging_dir"
