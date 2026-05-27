use anyhow::{Result, bail};
use std::time::{Duration, Instant};

use crate::commands::api;
use crate::credentials::Credentials;

pub fn login_with_code(code: &str, _verbose: bool) -> Result<()> {
    if code.trim().is_empty() {
        bail!("registration code cannot be empty");
    }

    let api_url = api::api_url();
    let resp = api::auth_code(&api_url, code.trim())?;
    if resp.status != "ready" {
        bail!(
            "login failed: auth server returned status '{}'",
            resp.status
        );
    }

    let username = resp.username.unwrap_or_else(|| "developer".to_string());
    let token = resp
        .fstak_token
        .ok_or_else(|| anyhow::anyhow!("login failed: auth server did not return fstak_token"))?;

    let creds = Credentials { username, token };
    creds.save()?;
    eprintln!("logged in as {}", creds.username);
    Ok(())
}

pub fn login(_verbose: bool) -> Result<()> {
    if let Ok(token) = std::env::var("FSTAK_TOKEN") {
        return login_with_code(&token, false);
    }

    let api_url = api::api_url();
    let start = api::auth_device_start(&api_url)?;
    eprintln!(
        "Open {} and enter code: {}",
        start.verification_uri, start.user_code
    );

    let deadline = Instant::now() + Duration::from_secs(start.expires_in.max(1));
    let poll_interval = start.interval.max(1);
    loop {
        if Instant::now() >= deadline {
            bail!("login timed out. Run `fstak login` again.");
        }

        let polled = api::auth_device_poll(&api_url, &start.poll_token)?;
        match polled.status.as_str() {
            "pending" => api::sleep_for(poll_interval),
            "ready" => {
                let username = polled.username.unwrap_or_else(|| "developer".to_string());
                let token = polled.fstak_token.ok_or_else(|| {
                    anyhow::anyhow!("login failed: auth server returned ready without token")
                })?;
                let creds = Credentials { username, token };
                creds.save()?;
                eprintln!("logged in as {}", creds.username);
                return Ok(());
            }
            "expired" => bail!("device code expired. Run `fstak login` again."),
            other => bail!("login failed: unexpected auth status '{other}'"),
        }
    }
}
