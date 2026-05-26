use anyhow::{Result, bail};

use crate::cli::LogsArgs;
use crate::commands::api;
use crate::config::LocalState;
use crate::credentials::Credentials;

pub fn logs(args: LogsArgs, _verbose: bool) -> Result<()> {
    let cwd = std::env::current_dir()?;
    let state = LocalState::load(&cwd)
        .map_err(|_| anyhow::anyhow!("No .fstak/state.json found. Run `fstak new` or `fstak run` first."))?;
    let slug = state
        .project_slug
        .ok_or_else(|| anyhow::anyhow!("No project slug saved. Run `fstak run` first."))?;
    let creds = Credentials::require()?;
    let api_url = api::api_url();
    let mut url = format!(
        "{}/projects/{}/logs?limit={}",
        api_url.trim_end_matches('/'),
        api::percent_encode_query_value(&slug),
        args.limit
    );
    if let Some(severity) = args.severity {
        url.push_str(&format!("&severity={}", api::percent_encode_query_value(&severity)));
    }
    if let Some(from) = args.from {
        url.push_str(&format!("&from={}", api::percent_encode_query_value(&from)));
    }
    if let Some(to) = args.to {
        url.push_str(&format!("&to={}", api::percent_encode_query_value(&to)));
    }
    match ureq::get(&url)
        .set("Authorization", &format!("Bearer {}", creds.token))
        .call()
    {
        Ok(resp) => {
            println!("{}", resp.into_string()?);
            Ok(())
        }
        Err(ureq::Error::Status(401, _)) | Err(ureq::Error::Status(403, _)) => {
            bail!("session invalid or expired. Run `fstak login` to re-authenticate.")
        }
        Err(ureq::Error::Status(code, resp)) => {
            bail!("GET {url} returned {code}: {}", resp.into_string().unwrap_or_default())
        }
        Err(ureq::Error::Transport(t)) => bail!("GET {url} failed: {t}"),
    }
}
