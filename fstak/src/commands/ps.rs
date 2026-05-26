use anyhow::{Result, bail};

use crate::commands::api;
use crate::credentials::Credentials;

pub fn ps(_verbose: bool) -> Result<()> {
    let creds = Credentials::require()?;
    let api_url = api::api_url();
    let url = format!("{}/projects", api_url.trim_end_matches('/'));
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
