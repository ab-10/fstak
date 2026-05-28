use anyhow::Result;

use crate::commands::api;
use crate::credentials::Credentials;

pub fn whoami(_verbose: bool) -> Result<()> {
    let creds = Credentials::require()?;
    let api_url = api::api_url();
    let info = api::auth_whoami(&api_url, &creds.token)?;
    println!("{}", info.spx_username);
    Ok(())
}
