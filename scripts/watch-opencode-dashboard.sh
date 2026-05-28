#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OPENCODE_DB="${OPENCODE_DB:-$HOME/.local/share/opencode/opencode.db}"
DASHBOARD="${FSTAK_STORY_DASHBOARD:-$REPO_ROOT/notes/stories/story-status-dashboard.html}"
INTERVAL_SECONDS="${OPENCODE_DASHBOARD_INTERVAL:-180}"
STATE_FILE="${OPENCODE_DASHBOARD_STATE:-$REPO_ROOT/.git/opencode-dashboard-watch-state}"
RUN_ONCE=0

for arg in "$@"; do
  case "$arg" in
    --once) RUN_ONCE=1 ;;
    *)
      echo "usage: $0 [--once]" >&2
      exit 2
      ;;
  esac
done

if ! command -v sqlite3 >/dev/null 2>&1; then
  echo "sqlite3 is required" >&2
  exit 1
fi

if ! command -v node >/dev/null 2>&1; then
  echo "node is required" >&2
  exit 1
fi

sync_once() {
  if [ ! -f "$OPENCODE_DB" ]; then
    echo "opencode database not found: $OPENCODE_DB" >&2
    return 1
  fi

  local todo_json
  todo_json="$(mktemp -t fstak-opencode-todos.XXXXXX.json)"
  trap 'rm -f "$todo_json"' RETURN

  local latest_seen
  latest_seen="$(sqlite3 "$OPENCODE_DB" "
    select coalesce(max(p.time_updated), 0)
    from part p
    join session s on s.id = p.session_id
    where json_extract(p.data, '$.type') = 'tool'
      and json_extract(p.data, '$.tool') = 'todowrite';
  ")"

  if [ ! -f "$STATE_FILE" ]; then
    mkdir -p "$(dirname "$STATE_FILE")"
    printf '%s\n' "$latest_seen" > "$STATE_FILE"
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) initialized baseline at $latest_seen; no dashboard changes"
    return 0
  fi

  local last_seen
  last_seen="$(cat "$STATE_FILE")"

  sqlite3 -json "$OPENCODE_DB" "
    with todo_items as (
      select
        p.time_updated as time_updated,
        s.id as session_id,
        s.title as session_title,
        json_extract(todo.value, '$.content') as content,
        json_extract(todo.value, '$.status') as status
      from part p
      join session s on s.id = p.session_id
      join json_each(json_extract(p.data, '$.state.input.todos')) todo
      where p.time_updated > $last_seen
        and json_extract(p.data, '$.type') = 'tool'
        and json_extract(p.data, '$.tool') = 'todowrite'
        and json_extract(todo.value, '$.content') is not null
    )
    select * from todo_items order by time_updated asc;
  " > "$todo_json"

  node - "$DASHBOARD" "$todo_json" <<'NODE'
const fs = require("fs");

const dashboardPath = process.argv[2];
const todoPath = process.argv[3];
const html = fs.readFileSync(dashboardPath, "utf8");
const rows = JSON.parse(fs.readFileSync(todoPath, "utf8") || "[]");

const latest = new Map();
for (const row of rows) {
  const content = String(row.content || "").trim();
  if (!content) continue;
  const prior = latest.get(content);
  if (!prior || Number(row.time_updated || 0) >= Number(prior.time_updated || 0)) {
    latest.set(content, row);
  }
}

const statusMap = {
  pending: "pending",
  in_progress: "active",
  completed: "completed",
  cancelled: "cancelled",
};

const rowsByStatus = [...latest.values()].map((row) => ({
  title: String(row.content).trim(),
  status: statusMap[row.status] || "active",
  sourceStatus: row.status || "unknown",
  session: row.session_title || row.session_id || "opencode",
  timeUpdated: Number(row.time_updated || 0),
}));

rowsByStatus.sort((a, b) => {
  const order = { active: 0, pending: 1, completed: 2, cancelled: 3 };
  return (order[a.status] - order[b.status]) || (b.timeUpdated - a.timeUpdated) || a.title.localeCompare(b.title);
});

const existingMatch = html.match(/const stories = ([\s\S]*?\n    \]);/);
const existingStories = existingMatch ? eval(existingMatch[1]) : [];
for (const story of existingStories) {
  if (story.status === "active" && story.sourceStatus !== "in_progress") {
    story.status = "pending";
    story.archive = false;
  }
  if (story.status === "archived") {
    story.status = "completed";
    story.archive = true;
  }
}
const byTitle = new Map(existingStories.map((story) => [story.title, story]));

for (const row of rowsByStatus) {
  byTitle.set(row.title, {
    ...(byTitle.get(row.title) || {}),
    title: row.title,
    status: row.status,
    archive: row.status !== "active",
    source: `OpenCode: ${row.session}`,
    sourceStatus: row.sourceStatus,
    timeUpdated: row.timeUpdated,
  });
}

const stories = [...byTitle.values()].sort((a, b) => {
  const order = { active: 0, pending: 1, completed: 2, cancelled: 3 };
  return (order[a.status] - order[b.status]) || (Number(b.timeUpdated || 0) - Number(a.timeUpdated || 0)) || String(a.title).localeCompare(String(b.title));
}).map((story, index) => ({
  ...story,
  n: index + 1,
}));

const literal = `const stories = ${JSON.stringify(stories, null, 6).replace(/^/gm, "    ").trim()};`;
const next = html.replace(/const stories = \[[\s\S]*?\n    \];/, literal);
if (next === html) {
  throw new Error("Could not find stories array in dashboard");
}
fs.writeFileSync(dashboardPath, next);

const active = stories.filter((story) => story.status === "active").length;
const pending = stories.filter((story) => story.status === "pending").length;
const completed = stories.filter((story) => story.status === "completed").length;
const cancelled = stories.filter((story) => story.status === "cancelled").length;
console.log(`${new Date().toISOString()} updated ${dashboardPath}: ${active} active, ${pending} pending, ${completed} completed, ${cancelled} cancelled`);
NODE

  printf '%s\n' "$latest_seen" > "$STATE_FILE"
}

if [ "$RUN_ONCE" -eq 1 ]; then
  sync_once
else
  while true; do
    sync_once
    sleep "$INTERVAL_SECONDS"
  done
fi
