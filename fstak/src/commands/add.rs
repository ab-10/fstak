use anyhow::Result;

use crate::cli::AddArgs;
use crate::commands::api;
use crate::config::LocalState;
use crate::credentials::Credentials;

pub fn add(args: AddArgs, _verbose: bool) -> Result<()> {
    let cwd = std::env::current_dir()?;
    let state = LocalState::load(&cwd)
        .map_err(|_| anyhow::anyhow!("No .fstak/state.json found. Run `fstak new` or `fstak run` first."))?;
    let slug = state
        .project_slug
        .ok_or_else(|| anyhow::anyhow!("No project slug saved. Run `fstak run` first."))?;
    let creds = Credentials::require()?;
    let api_url = api::api_url();
    api::dep_set(&api_url, &creds.token, &slug, &args.pkg, &args.pkg)?;
    println!("added {}", args.pkg);
    Ok(())
}
