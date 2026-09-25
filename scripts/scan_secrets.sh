#!/usr/bin/env bash
#
# Fail the build if a secret-shaped value is present in a tracked file.
#
# This exists because an earlier revision of this project committed a live
# database credential. Deleting such a file is not remediation -- the value
# remains in Git history and must be rotated -- so the control is a scan that
# runs before every commit and on every pull request.
#
# Escape hatch: a line may opt out with the literal marker
#
#     secret-scan: allow
#
# The redaction tests must contain realistic credential *shapes* in order to
# prove they are redacted, so a whole-file or whole-directory exclusion would be
# too blunt. The pragma is per-line, visible in review, and greppable.
#
# Usage:  bash scripts/scan_secrets.sh [--include-untracked]
# Exit:   0 = clean, 1 = findings, 2 = usage error
#
# ``--include-untracked`` also scans files Git is not tracking yet (respecting
# ``.gitignore``). The default mode is deliberately narrower because CI scans a
# checkout; pre-commit passes the flag so a new file is covered before it is
# ever committed, which is exactly when a credential is most likely to appear.

set -euo pipefail

cd "$(dirname "$0")/.."

INCLUDE_UNTRACKED=0
for arg in "$@"; do
  case "$arg" in
    --include-untracked) INCLUDE_UNTRACKED=1 ;;
    *)
      echo "usage: bash scripts/scan_secrets.sh [--include-untracked]" >&2
      exit 2
      ;;
  esac
done

# Documentation legitimately quotes secret-shaped examples in prose.
EXCLUDE_REGEX='^(docs/|.*\.md$|\.env\.example$)'

PRAGMA='secret-scan: allow'

list_files() {
  if [[ "$INCLUDE_UNTRACKED" -eq 1 ]]; then
    # NUL-delimited and de-duplicated: a path can be both tracked and modified,
    # and a filename may contain spaces.
    { git ls-files -z; git ls-files -z --others --exclude-standard; } | sort -zu
  else
    git ls-files -z
  fi
}

# Each entry is "human-label|extended-regex".
PATTERNS=(
  "OpenAI key|sk-[A-Za-z0-9_-]{20,}"
  "Anthropic key|sk-ant-[A-Za-z0-9_-]{20,}"
  "GitHub token|gh[pousr]_[A-Za-z0-9]{20,}"
  "AWS access key id|AKIA[0-9A-Z]{16}"
  "Google API key|AIza[0-9A-Za-z_-]{30,}"
  "Slack token|xox[baprs]-[0-9A-Za-z-]{10,}"
  "Private key block|-----BEGIN [A-Z ]*PRIVATE KEY-----"
  "Postgres URL with password|postgres(ql)?(\+[a-z]+)?://[^:/@[:space:]]+:[^@[:space:]]+@"
  "Supabase key|sb[p]_[A-Za-z0-9_-]{20,}"
  "Generic credential assignment|(?i)(api[_-]?key|secret[_-]?key|access[_-]?token)[\"'[:space:]]*[:=][\"'[:space:]]*[A-Za-z0-9/+_-]{24,}"
)

found=0

report() {
  printf '\033[31mSECRET\033[0m %s:%s  (%s)\n' "$1" "$2" "$3"
  found=1
}

while IFS= read -r -d '' file; do
  [[ "$file" =~ $EXCLUDE_REGEX ]] && continue

  for entry in "${PATTERNS[@]}"; do
    label="${entry%%|*}"
    regex="${entry#*|}"

    while IFS= read -r match; do
      [[ -z "$match" ]] && continue
      lineno="${match%%:*}"
      content="${match#*:}"
      # Per-line, reviewable opt-out. The value itself is never echoed.
      [[ "$content" == *"$PRAGMA"* ]] && continue
      report "$file" "$lineno" "$label"
    done < <(grep -nIE -- "$regex" "$file" 2>/dev/null || true)
  done
done < <(list_files)

# Paths that must never be tracked, regardless of content.
FORBIDDEN_NAMES=(
  '^credentials$'
  '^\.env$'
  '\.pem$'
  '\.key$'
  '^id_rsa'
  '\.tfstate$'
  '\.tfstate\..*$'
  '\.tfvars$'
)

while IFS= read -r -d '' file; do
  for pattern in "${FORBIDDEN_NAMES[@]}"; do
    if [[ "$file" =~ $pattern ]]; then
      printf '\033[31mFORBIDDEN FILE\033[0m %s must not be committed\n' "$file"
      found=1
    fi
  done
done < <(list_files)

if [[ "$found" -ne 0 ]]; then
  echo
  echo "Secret scan failed. If a real credential was committed, rotating it at the"
  echo "provider is the only real fix -- removing the file does not remove history."
  echo "If the value is a deliberate test fixture, append the marker:"
  echo "    # $PRAGMA"
  exit 1
fi

echo "Secret scan clean."
