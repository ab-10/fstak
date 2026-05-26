use anyhow::{Result, bail};
use std::io::Read;

use crate::cli::FeedbackArgs;
use crate::commands::api;
use crate::credentials::Credentials;

#[derive(serde::Serialize)]
struct FeedbackContext {
    cli_version: String,
    os: String,
    arch: String,
}

pub fn feedback(args: FeedbackArgs, _verbose: bool) -> Result<()> {
    let message = if args.message == "-" {
        let mut buf = String::new();
        std::io::stdin().read_to_string(&mut buf)?;
        buf
    } else {
        args.message
    };
    if message.trim().is_empty() {
        bail!("feedback message cannot be empty");
    }
    let token = Credentials::load()?.map(|c| c.token);
    let ctx = FeedbackContext {
        cli_version: env!("CARGO_PKG_VERSION").to_string(),
        os: std::env::consts::OS.to_string(),
        arch: std::env::consts::ARCH.to_string(),
    };
    api::post_feedback(&api::api_url(), token.as_deref(), &message, &ctx)?;
    println!("feedback sent");
    Ok(())
}
