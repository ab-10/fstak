use anyhow::{Context, Result, bail};
use std::env;

use crate::cli::NewArgs;
use crate::config::{LocalState, ensure_gitignore_has_fstak};

pub fn new_project(args: NewArgs, _verbose: bool) -> Result<()> {
    validate_name(&args.name)?;
    let cwd = env::current_dir()?;
    let dir = cwd.join(&args.name);
    if dir.exists() {
        bail!("Directory '{}' already exists", args.name);
    }
    std::fs::create_dir_all(dir.join("src"))
        .with_context(|| format!("creating {}", dir.display()))?;

    std::fs::write(
        dir.join("package.json"),
        format!(
            "{{\n  \"name\": \"{}\",\n  \"private\": true,\n  \"version\": \"0.1.0\",\n  \"scripts\": {{\n    \"build\": \"bun build index.html --outdir dist\"\n  }},\n  \"dependencies\": {{\n    \"react\": \"^19.0.0\",\n    \"react-dom\": \"^19.0.0\"\n  }}\n}}\n",
            args.name
        ),
    )
    .context("writing package.json")?;
    std::fs::write(
        dir.join("index.html"),
        "<!doctype html><html><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>fstak app</title></head><body><div id=\"root\"></div><script type=\"module\" src=\"/src/main.tsx\"></script></body></html>\n",
    )
    .context("writing index.html")?;
    std::fs::write(
        dir.join("src/main.tsx"),
        "import React from \"react\";\nimport { createRoot } from \"react-dom/client\";\n\nfunction App() {\n  return <h1>Hello from fstak</h1>;\n}\n\ncreateRoot(document.getElementById(\"root\")!).render(<App />);\n",
    )
    .context("writing src/main.tsx")?;
    std::fs::write(dir.join(".gitignore"), "node_modules/\ndist/\n.fstak/\n")?;

    let state = LocalState::init(&args.name);
    state.save(&dir)?;
    ensure_gitignore_has_fstak(&dir)?;
    eprintln!("created {}", dir.display());
    eprintln!("next: cd {} && fstak run", args.name);
    Ok(())
}

fn validate_name(name: &str) -> Result<()> {
    if name.is_empty() {
        bail!("Project name cannot be empty");
    }
    if !name.chars().next().unwrap_or('a').is_ascii_lowercase() {
        bail!("Project name must start with a lowercase letter");
    }
    if !name
        .chars()
        .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '-')
    {
        bail!("Project name must contain only lowercase letters, digits, and hyphens");
    }
    if name.ends_with('-') {
        bail!("Project name cannot end with a hyphen");
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::validate_name;

    #[test]
    fn accepts_valid_name() {
        assert!(validate_name("alpha").is_ok());
        assert!(validate_name("app-2").is_ok());
    }

    #[test]
    fn rejects_trailing_hyphen() {
        let err = validate_name("alpha-").expect_err("expected trailing hyphen to fail");
        assert!(err.to_string().contains("cannot end with a hyphen"));
    }

    #[test]
    fn rejects_invalid_start_character() {
        let err = validate_name("1alpha").expect_err("expected invalid first character to fail");
        assert!(
            err.to_string()
                .contains("must start with a lowercase letter")
        );
    }

    #[test]
    fn rejects_invalid_characters() {
        let err = validate_name("app_name").expect_err("expected underscore to fail");
        assert!(
            err.to_string()
                .contains("must contain only lowercase letters, digits, and hyphens")
        );
    }

    #[test]
    fn scaffold_builds_html_entrypoint() {
        let package_json = format!(
            "{{\n  \"name\": \"{}\",\n  \"private\": true,\n  \"version\": \"0.1.0\",\n  \"scripts\": {{\n    \"build\": \"bun build index.html --outdir dist\"\n  }},\n  \"dependencies\": {{\n    \"react\": \"^19.0.0\",\n    \"react-dom\": \"^19.0.0\"\n  }}\n}}\n",
            "example"
        );

        assert!(package_json.contains("bun build index.html --outdir dist"));
        assert!(!package_json.contains("bun build src/main.tsx --outdir dist"));
    }
}
