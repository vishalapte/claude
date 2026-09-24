#!/usr/bin/env bash
# ~/.claude/statusline-command.sh
# One-line status, condensed from the darwin PS1 in ~/.bashrc:
#   [\A] \u@\h \n (\w)$(__git_ps1)   →   [HH:MM] (email) (~/path:branch)
# Path is ~-abbreviated like bash \w; git branch mirrors __git_ps1.
#
# The active Claude account email is shown after the time. Claude Code does
# NOT pass the account in the statusLine stdin JSON, so it's read live from
# ~/.claude.json (oauthAccount.emailAddress) — it always reflects the
# account currently logged in.

input=$(cat)
cwd=$(echo "$input" | jq -r '.workspace.current_dir // .cwd')

# ~-abbreviate the path the way bash \w does.
case "$cwd" in
  "$HOME")   disp="~" ;;
  "$HOME"/*) disp="~${cwd#"$HOME"}" ;;
  *)         disp="$cwd" ;;
esac

# Current git branch (like __git_ps1); appended after the path as ":branch".
branch=$(git -C "$cwd" branch --show-current 2>/dev/null)
loc="$disp"
[ -n "$branch" ] && loc="${disp}:${branch}"

# Active Claude account email from ~/.claude.json (not in the statusLine JSON).
acct=$(jq -r '.oauthAccount.emailAddress // .oauthAccount.displayName // empty' \
  ~/.claude.json 2>/dev/null)
acct_label=""
[ -n "$acct" ] && acct_label=" (${acct})"

yellow='\e[0;33m'
dim='\e[2m'
reset='\e[m'

printf "${yellow}[%s]${reset}${dim}%s${reset} ${dim}(%s)${reset}" \
  "$(date +%H:%M)" "$acct_label" "$loc"
