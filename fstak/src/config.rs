// Legacy local state and migration helpers. Some functions are unused after
// the auth cutover but kept for potential future migration paths.
#![allow(dead_code)]
use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};

const OLD_CONFIG_FILE: &str = "fstak.config.json";
const STATE_DIR: &str = ".fstak";
const STATE_FILE: &str = "state.json";

// --- Local, gitignored state (.fstak/state.json) ---

#[derive(Debug, Serialize, Deserialize)]
pub struct LocalState {
    pub project_name: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub project_slug: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub project_url: Option<String>,
}

impl LocalState {
    pub fn load(dir: &Path) -> Result<Self> {
        let path = Self::path(dir);
        let contents = std::fs::read_to_string(&path)
            .with_context(|| format!("reading {}", path.display()))?;
        let state: Self = serde_json::from_str(&contents)
            .with_context(|| format!("parsing {}", path.display()))?;
        Ok(state)
    }

    pub fn save(&self, dir: &Path) -> Result<()> {
        let state_dir = dir.join(STATE_DIR);
        std::fs::create_dir_all(&state_dir)
            .with_context(|| format!("creating {}", state_dir.display()))?;
        let path = Self::path(dir);
        let contents = serde_json::to_string_pretty(self)?;
        std::fs::write(&path, contents).with_context(|| format!("writing {}", path.display()))?;
        Ok(())
    }

    pub fn exists(dir: &Path) -> bool {
        Self::path(dir).exists()
    }

    fn path(dir: &Path) -> PathBuf {
        dir.join(STATE_DIR).join(STATE_FILE)
    }

    /// Create a fresh LocalState. The slug/url are populated by the server on first deploy.
    pub fn init(project_name: &str) -> Self {
        LocalState {
            project_name: project_name.to_string(),
            project_slug: None,
            project_url: None,
        }
    }
}

// --- Migration from old formats ---

/// Migrate old config layouts into the current single-file `.fstak/state.json`.
///
/// Handles two legacy formats:
/// 1. Combined format: `fstak.config.json` with project_slug (very old)
/// 2. Two-file format: `fstak.config.json` + `.fstak/state.json` (previous)
///
/// After migration, `fstak.config.json` is deleted. Idempotent.
pub fn migrate_if_needed(dir: &Path) -> Result<()> {
    let old_config_path = dir.join(OLD_CONFIG_FILE);
    if !old_config_path.exists() {
        return Ok(());
    }

    let contents = std::fs::read_to_string(&old_config_path)
        .with_context(|| format!("reading {}", old_config_path.display()))?;
    let raw: serde_json::Value = serde_json::from_str(&contents)
        .with_context(|| format!("parsing {}", old_config_path.display()))?;

    let project_name = raw["project_name"]
        .as_str()
        .unwrap_or("unknown")
        .to_string();

    if LocalState::exists(dir) {
        // Two-file format: state.json exists but may lack project_name.
        let mut state = LocalState::load(dir)?;
        if !state_has_project_name(dir)? {
            state.project_name = project_name;
            if state.project_slug.is_none() {
                state.project_slug = raw
                    .get("project_slug")
                    .and_then(|v| v.as_str())
                    .map(|s| s.to_string());
            }
            state.save(dir)?;
        }
    } else if raw.get("project_slug").is_some() {
        // Very old combined format: everything in fstak.config.json.
        let state = LocalState {
            project_name,
            project_slug: raw
                .get("project_slug")
                .and_then(|v| v.as_str())
                .map(|s| s.to_string()),
            project_url: raw
                .get("project_url")
                .and_then(|v| v.as_str())
                .map(|s| s.to_string()),
        };
        state.save(dir)?;
        ensure_gitignore_has_fstak(dir)?;
    } else {
        // fstak.config.json exists but no state.json and no project_slug —
        // just a bare config. Create state from it.
        let state = LocalState::init(&project_name);
        state.save(dir)?;
        ensure_gitignore_has_fstak(dir)?;
    }

    // Remove the old config file.
    std::fs::remove_file(&old_config_path)
        .with_context(|| format!("removing {}", old_config_path.display()))?;

    Ok(())
}

/// Check whether the existing state.json already has a `project_name` field.
fn state_has_project_name(dir: &Path) -> Result<bool> {
    let path = dir.join(STATE_DIR).join(STATE_FILE);
    let contents = std::fs::read_to_string(&path)?;
    let raw: serde_json::Value = serde_json::from_str(&contents)?;
    Ok(raw.get("project_name").is_some())
}

/// Append `.fstak/` to .gitignore if not already present.
pub fn ensure_gitignore_has_fstak(dir: &Path) -> Result<()> {
    let gitignore_path = dir.join(".gitignore");
    if gitignore_path.exists() {
        let contents = std::fs::read_to_string(&gitignore_path)?;
        if contents
            .lines()
            .any(|line| line.trim() == ".fstak/" || line.trim() == ".fstak")
        {
            return Ok(());
        }
        let mut new_contents = contents;
        if !new_contents.ends_with('\n') {
            new_contents.push('\n');
        }
        new_contents.push_str(".fstak/\n");
        std::fs::write(&gitignore_path, new_contents)?;
    } else {
        std::fs::write(&gitignore_path, ".fstak/\n")?;
    }
    Ok(())
}

/// When no `.fstak/state.json` exists but the directory looks like a project,
/// derive project_name from the directory name and create state.
pub fn recover_state(dir: &Path) -> Result<LocalState> {
    let has_package_json = dir.join("package.json").exists();
    let has_git = dir.join(".git").exists();

    if !has_package_json && !has_git {
        anyhow::bail!(
            "No .fstak/state.json found and directory doesn't look like a project. Run `fstak new` first."
        );
    }

    let project_name = dir
        .file_name()
        .and_then(|n| n.to_str())
        .unwrap_or("unknown")
        .to_string();

    let state = LocalState::init(&project_name);
    state.save(dir)?;
    ensure_gitignore_has_fstak(dir)?;

    Ok(state)
}
