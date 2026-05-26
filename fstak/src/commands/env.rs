use anyhow::{Result, bail};
use std::io::Read;

use crate::cli::{EnvArgs, EnvCommand};
use crate::commands::api;
use crate::config::LocalState;
use crate::credentials::Credentials;

fn project_slug() -> Result<String> {
    let cwd = std::env::current_dir()?;
    let state = LocalState::load(&cwd)
        .map_err(|_| anyhow::anyhow!("No .fstak/state.json found. Run `fstak new` or `fstak run` first."))?;
    state
        .project_slug
        .ok_or_else(|| anyhow::anyhow!("No project slug saved. Run `fstak run` first."))
}

pub fn env(args: EnvArgs, _verbose: bool) -> Result<()> {
    let creds = Credentials::require()?;
    let api_url = api::api_url();
    let slug = project_slug()?;
    match args.command {
        EnvCommand::List => {
            let resp = api::env_list(&api_url, &creds.token, &slug)?;
            for var in resp.variables {
                println!("{}", var.key);
            }
        }
        EnvCommand::Unset(a) => {
            api::env_unset(&api_url, &creds.token, &slug, &a.key)?;
            println!("unset {}", a.key);
        }
        EnvCommand::Set(a) => {
            let (key, value) = parse_set(&a.key_or_pair, a.from_stdin, a.from_env)?;
            api::env_set(&api_url, &creds.token, &slug, &key, &value)?;
            println!("set {}", key);
        }
        EnvCommand::Load(a) => {
            let content = std::fs::read_to_string(a.file)?;
            for line in content.lines() {
                let trimmed = line.trim();
                if trimmed.is_empty() || trimmed.starts_with('#') {
                    continue;
                }
                if let Some((k, v)) = trimmed.split_once('=') {
                    api::env_set(&api_url, &creds.token, &slug, k.trim(), v.trim())?;
                }
            }
            println!("loaded env");
        }
    }
    Ok(())
}

fn parse_set(key_or_pair: &str, from_stdin: bool, from_env: bool) -> Result<(String, String)> {
    if key_or_pair.contains('=') {
        let (k, v) = key_or_pair.split_once('=').expect("split_once checked");
        return Ok((k.to_string(), v.to_string()));
    }
    if from_stdin {
        let mut buf = String::new();
        std::io::stdin().read_to_string(&mut buf)?;
        return Ok((key_or_pair.to_string(), buf.trim_end().to_string()));
    }
    if from_env {
        let value = std::env::var(key_or_pair)
            .map_err(|_| anyhow::anyhow!("environment variable {} is not set", key_or_pair))?;
        return Ok((key_or_pair.to_string(), value));
    }
    bail!("missing value. use KEY=value, --from-stdin, or --from-env")
}
