# Claude Code Git-Aware Status Line

## Problem
Default Claude Code status line doesn't show git repository information. Wanted a status line similar to popular setups showing:
- GitHub org/repo name
- Current branch
- Staged, unstaged, and untracked file counts

## Solution
Created a custom bash script at `~/.claude/statusline-git.sh` that queries git for repository status and configured Claude Code to use it.

### Status Line Script
Location: `~/.claude/statusline-git.sh`

```bash
#!/bin/bash
if ! git rev-parse --git-dir > /dev/null 2>&1; then
    printf '%s' "$(basename "$(pwd)")"
    exit 0
fi

# Get org/repo from remote URL
remote_url=$(git remote get-url origin 2>/dev/null)
if [ -n "$remote_url" ]; then
    # Extract org/repo from git@github.com:org/repo.git or https://github.com/org/repo.git
    repo_path=$(echo "$remote_url" | sed -E 's#(git@|https://)([^:/]+)[:/](.+)(\.git)?#\3#' | sed 's/\.git$//')
else
    repo_path=$(basename "$(git rev-parse --show-toplevel 2>/dev/null || pwd)")
fi

# Get branch
branch=$(git branch --show-current 2>/dev/null)
[ -z "$branch" ] && branch=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")

# Counts
staged=$(git --no-optional-locks diff --cached --numstat 2>/dev/null | wc -l | tr -d ' ')
unstaged=$(git --no-optional-locks diff --numstat 2>/dev/null | wc -l | tr -d ' ')
added=$(git --no-optional-locks ls-files --others --exclude-standard 2>/dev/null | wc -l | tr -d ' ')

# Colors
cyan='\033[36m'
green='\033[32m'
reset='\033[0m'

# Output: org/repo | branch | S: N | U: N | A: N
printf "${cyan}%s${reset} | ${green}%s${reset} | S: %s | U: %s | A: %s" "$repo_path" "$branch" "$staged" "$unstaged" "$added"
```

### Claude Code Settings
Location: `~/.claude/settings.json`

```json
{
  "statusLine": {
    "type": "command",
    "command": "/home/cooky/.claude/statusline-git.sh"
  }
}
```

### Output Format
```
imhtp-dev/LombardiaAgent | main | S: 0 | U: 8 | A: 13
```

- **Cyan**: org/repo name (from git remote)
- **Green**: branch name
- **S: N**: Staged files (ready to commit)
- **U: N**: Unstaged files (modified but not added)
- **A: N**: Added/untracked files (new files git doesn't know)

## Key Code Reference
- `~/.claude/statusline-git.sh` - The status line script
- `~/.claude/settings.json` - Claude Code configuration

## Gotchas
1. **CRLF Line Endings**: On WSL, files created may have Windows line endings (CRLF) which cause bash syntax errors. Fix with:
   ```bash
   sed -i 's/\r$//' ~/.claude/statusline-git.sh
   ```

2. **Use `--no-optional-locks`**: Git commands in status line should use this flag to prevent lock file issues during concurrent operations.

3. **Heredoc Indentation**: When using heredoc (`<< 'EOF'`) in terminal, the closing `EOF` must have NO leading whitespace.

4. **Context Window Not Accessible**: Claude Code's context window usage is internal and cannot be queried from external scripts for the status line.

## Date Learned
2025-12-24

## Related
- Claude Code documentation on status line configuration
- Git status porcelain format for scripting
