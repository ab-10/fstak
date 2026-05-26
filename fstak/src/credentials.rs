use anyhow::{Context, Result, bail};
use serde::{Deserialize, Serialize};
use std::path::PathBuf;

#[derive(Debug, Serialize, Deserialize)]
pub struct Credentials {
    pub username: String,
    pub token: String,
}

impl Credentials {
    fn path() -> Result<PathBuf> {
        let home = dirs::home_dir().context("could not determine home directory")?;
        Ok(home.join(".fstak").join("credentials.json"))
    }

    pub fn load() -> Result<Option<Self>> {
        let path = Self::path()?;
        if !path.exists() {
            return Ok(None);
        }
        let contents = std::fs::read_to_string(&path)
            .with_context(|| format!("reading {}", path.display()))?;
        let creds: Self = serde_json::from_str(&contents)
            .with_context(|| format!("parsing {}", path.display()))?;
        Ok(Some(creds))
    }

    pub fn save(&self) -> Result<()> {
        let path = Self::path()?;
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)
                .with_context(|| format!("creating {}", parent.display()))?;
        }
        let contents = serde_json::to_string_pretty(self)?;
        std::fs::write(&path, contents).with_context(|| format!("writing {}", path.display()))?;
        Ok(())
    }

    pub fn require() -> Result<Self> {
        match Self::load()? {
            Some(creds) => Ok(creds),
            None => bail!(
                "not logged in (couldn't find token at `~/.fstak/credentials.json`)\n\
                 Run `fstak login` to authenticate with GitHub."
            ),
        }
    }
}
