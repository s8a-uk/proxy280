#!/usr/bin/env bash
# GitHub リポジトリの設定を gh で適用する (make github-settings)。何度実行してもよい。
# GitHub の無料プランで public リポジトリにしか使えない設定 (ルールセット等) は、private の間は waiting と表示する。
set -euo pipefail

repo=$(gh repo view --json nameWithOwner --jq .nameWithOwner)
visibility=$(gh api "repos/$repo" --jq .visibility)
echo "$repo ($visibility)"

applied() { echo "  applied: $1"; }
waiting() { echo "  waiting: $1 (on GitHub's free plan only for a public repository)"; }
# try <説明> <コマンド...>: 適用できれば applied、このプランで拒否されたら waiting
try() {
	local what=$1
	shift
	if "$@" > /dev/null 2>&1; then applied "$what"; else waiting "$what"; fi
}

# リポジトリ: Wiki・Projects は使わない
gh api -X PATCH "repos/$repo" -F has_wiki=false -F has_projects=false > /dev/null
applied "no wiki, no projects"

# Actions: コミットの SHA で固定したアクションだけ、GitHub 公式とワークフローが使うものだけを許可する。
# トークンは既定で読み取り専用で、PR を承認できない。外部コントリビュータの PR は毎回承認してから動かす
gh api -X PUT "repos/$repo/actions/permissions" --input - > /dev/null <<'EOF'
{"enabled": true, "allowed_actions": "selected", "sha_pinning_required": true}
EOF
gh api -X PUT "repos/$repo/actions/permissions/selected-actions" --input - > /dev/null <<'EOF'
{"github_owned_allowed": true, "verified_allowed": false,
 "patterns_allowed": ["astral-sh/setup-uv@*", "docker/setup-buildx-action@*", "docker/build-push-action@*"]}
EOF
gh api -X PUT "repos/$repo/actions/permissions/workflow" --input - > /dev/null <<'EOF'
{"default_workflow_permissions": "read", "can_approve_pull_request_reviews": false}
EOF
applied "Actions: SHA pinning, allowed actions, read-only token"
try "Actions: approval for pull requests of outside contributors" \
	gh api -X PUT "repos/$repo/actions/permissions/fork-pr-contributor-approval" -f approval_policy=all_external_contributors

# セキュリティ
try "Dependabot alerts" gh api -X PUT "repos/$repo/vulnerability-alerts"
try "Dependabot security updates" gh api -X PUT "repos/$repo/automated-security-fixes"
try "secret scanning and push protection" gh api -X PATCH "repos/$repo" --input - <<'EOF'
{"security_and_analysis": {"secret_scanning": {"status": "enabled"},
                           "secret_scanning_push_protection": {"status": "enabled"}}}
EOF
try "private vulnerability reporting" gh api -X PUT "repos/$repo/private-vulnerability-reporting"

# ルールセット: main は、main に追従していてチェック (ci.yml のジョブ "all checks passed") が通った PR でだけ変更する。
# force push と削除は禁止。管理者 (オーナー) は迂回できる
ruleset() {
	local name=$1 body=$2 id
	id=$(gh api "repos/$repo/rulesets" --jq ".[] | select(.name == \"$name\") | .id" 2> /dev/null || true)
	if [ -n "$id" ]; then
		gh api -X PUT "repos/$repo/rulesets/$id" --input - <<< "$body"
	else
		gh api -X POST "repos/$repo/rulesets" --input - <<< "$body"
	fi
}
try "ruleset main" ruleset main "$(cat <<'EOF'
{"name": "main", "target": "branch", "enforcement": "active",
 "bypass_actors": [{"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always"}],
 "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
 "rules": [{"type": "deletion"}, {"type": "non_fast_forward"},
           {"type": "pull_request", "parameters": {"required_approving_review_count": 0,
            "dismiss_stale_reviews_on_push": false, "require_code_owner_review": false,
            "require_last_push_approval": false, "required_review_thread_resolution": false}},
           {"type": "required_status_checks", "parameters": {"strict_required_status_checks_policy": true,
            "required_status_checks": [{"context": "all checks passed"}]}}]}
EOF
)"
