use anyhow::{Result, bail};

use crate::cli::KillArgs;
use crate::commands::api;
use crate::credentials::Credentials;

pub fn kill(args: KillArgs, _verbose: bool) -> Result<()> {
    let creds = Credentials::require()?;
    let api_url = api::api_url();
    let slug = args
        .project_slug
        .trim()
        .trim_start_matches("https://")
        .split('.')
        .next()
        .unwrap_or(&args.project_slug)
        .to_string();
    let url = format!(
        "{}/projects/{}/kill",
        api_url.trim_end_matches('/'),
        api::percent_encode_query_value(&slug)
    );
    match ureq::post(&url)
        .set("Authorization", &format!("Bearer {}", creds.token))
        .call()
    {
        Ok(_) => {
            println!("killed {}", slug);
            Ok(())
        }
        Err(ureq::Error::Status(401, _)) | Err(ureq::Error::Status(403, _)) => {
            bail!("session invalid or expired. Run `fstak login` to re-authenticate.")
        }
        Err(ureq::Error::Status(code, resp)) => {
            bail!("POST {url} returned {code}: {}", resp.into_string().unwrap_or_default())
        }
        Err(ureq::Error::Transport(t)) => bail!("POST {url} failed: {t}"),
    }
}
