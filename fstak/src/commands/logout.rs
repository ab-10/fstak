use anyhow::Result;

use crate::commands::api;
use crate::credentials::Credentials;

pub fn logout(_verbose: bool) -> Result<()> {
    match Credentials::load()? {
        Some(creds) => {
            let api_url = api::api_url();
            if let Err(e) = api::auth_logout(&api_url, &creds.token) {
                eprintln!("warning: server-side session revocation failed: {e}");
            }
            if Credentials::remove()? {
                eprintln!("logged out {}", creds.username);
            }
        }
        None => {
            eprintln!("not logged in");
        }
    }
    Ok(())
}
