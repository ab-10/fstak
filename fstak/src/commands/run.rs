use anyhow::{Result, bail};
use std::env;

use crate::commands::api;
use crate::config::{LocalState, migrate_if_needed, recover_state};
use crate::credentials::Credentials;
use crate::ui;

pub fn run(verbose: bool) -> Result<()> {
    let cwd = env::current_dir()?;
    let creds = Credentials::require()?;
    migrate_if_needed(&cwd)?;
    let mut state = match LocalState::load(&cwd) {
        Ok(s) => s,
        Err(_) => recover_state(&cwd)?,
    };

    let archive = api::create_archive(&cwd)?;
    let api_url = api::api_url();
    let resp = api::post_run(
        &api_url,
        &creds.token,
        &archive,
        &state.project_name,
        state.project_slug.as_deref(),
        verbose,
    )?;

    if resp.project_name != state.project_name {
        bail!(
            "server returned project '{}' for local project '{}'",
            resp.project_name,
            state.project_name
        );
    }

    state.project_slug = Some(resp.project_slug);
    state.project_url = Some(resp.url.clone());
    state.save(&cwd)?;

    eprintln!();
    eprintln!("  {}", ui::hyperlink(&resp.url, &resp.url));
    eprintln!();
    Ok(())
}
