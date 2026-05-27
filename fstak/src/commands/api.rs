use anyhow::{Context, Result, bail};
use flate2::Compression;
use flate2::write::GzEncoder;
use serde::Deserialize;
use serde_json::json;
use std::env;
use std::path::Path;
use std::time::Duration;

use crate::ui;

const DEFAULT_API_URL: &str = "https://api.fstak.runspx.com";
const MAX_FEEDBACK_BYTES: usize = 1024 * 1024;

#[derive(Deserialize)]
pub struct DeviceAuthStart {
    pub user_code: String,
    pub verification_uri: String,
    pub poll_token: String,
    pub interval: u64,
    pub expires_in: u64,
}

#[derive(Deserialize)]
pub struct DeviceAuthPoll {
    pub status: String,
    pub fstak_token: Option<String>,
    pub username: Option<String>,
}

#[derive(Deserialize)]
pub struct CodeAuthResponse {
    pub status: String,
    pub fstak_token: Option<String>,
    pub username: Option<String>,
}

pub fn api_url() -> String {
    env::var("FSTAK_API_URL").unwrap_or_else(|_| DEFAULT_API_URL.to_string())
}

pub fn auth_device_start(api_url: &str) -> Result<DeviceAuthStart> {
    let url = format!("{}/auth/device", api_url.trim_end_matches('/'));
    match ureq::post(&url).send_json(json!({})) {
        Ok(resp) => resp.into_json().context("parsing device auth response"),
        Err(ureq::Error::Status(code, resp)) => {
            let body = resp.into_string().unwrap_or_else(|_| "<no body>".into());
            bail!("POST {url} returned {code}: {body}");
        }
        Err(ureq::Error::Transport(t)) => bail!("POST {url} failed: {t}"),
    }
}

pub fn auth_device_poll(api_url: &str, poll_token: &str) -> Result<DeviceAuthPoll> {
    let url = format!("{}/auth/token", api_url.trim_end_matches('/'));
    match ureq::post(&url).send_json(json!({ "poll_token": poll_token })) {
        Ok(resp) => resp.into_json().context("parsing device token response"),
        Err(ureq::Error::Status(code, resp)) => {
            let body = resp.into_string().unwrap_or_else(|_| "<no body>".into());
            bail!("POST {url} returned {code}: {body}");
        }
        Err(ureq::Error::Transport(t)) => bail!("POST {url} failed: {t}"),
    }
}

pub fn auth_code(api_url: &str, code: &str) -> Result<CodeAuthResponse> {
    let url = format!("{}/auth/code", api_url.trim_end_matches('/'));
    match ureq::post(&url).send_json(json!({ "code": code })) {
        Ok(resp) => resp.into_json().context("parsing code auth response"),
        Err(ureq::Error::Status(code, resp)) => {
            let body = resp.into_string().unwrap_or_else(|_| "<no body>".into());
            if code == 401 || code == 403 {
                bail!("authentication failed. Run `fstak login` and try again.");
            }
            bail!("POST {url} returned {code}: {body}");
        }
        Err(ureq::Error::Transport(t)) => bail!("POST {url} failed: {t}"),
    }
}

pub fn sleep_for(seconds: u64) {
    std::thread::sleep(Duration::from_secs(seconds));
}

/// Create a tar.gz archive of a project directory, excluding build artifacts and local state.
pub fn create_archive(dir: &Path) -> Result<Vec<u8>> {
    let buf = Vec::new();
    let encoder = GzEncoder::new(buf, Compression::fast());
    let mut archive = tar::Builder::new(encoder);

    add_dir_recursive(&mut archive, dir, dir)?;

    let encoder = archive.into_inner().context("finalizing tar archive")?;
    encoder.finish().context("finalizing gzip stream")
}

fn add_dir_recursive<W: std::io::Write>(
    archive: &mut tar::Builder<W>,
    root: &Path,
    current: &Path,
) -> Result<()> {
    let entries = std::fs::read_dir(current)
        .with_context(|| format!("reading directory {}", current.display()))?;

    for entry in entries {
        let entry = entry?;
        let path = entry.path();
        let file_type = entry.file_type()?;

        // Skip symlinks
        if file_type.is_symlink() {
            continue;
        }

        let name = entry.file_name();
        let name_str = name.to_string_lossy();
        let rel_path = path.strip_prefix(root).unwrap();

        if file_type.is_dir() {
            // Exclude common build / dependency / state directories at any depth
            if name_str == "node_modules"
                || name_str == "dist"
                || name_str == ".git"
                || name_str == ".fstak"
                || name_str == ".bun"
                || name_str == ".next"
                || name_str == "build"
                || name_str == "coverage"
            {
                continue;
            }
            archive
                .append_dir(rel_path, &path)
                .with_context(|| format!("adding directory {}", rel_path.display()))?;
            add_dir_recursive(archive, root, &path)?;
        } else if file_type.is_file() {
            // Skip lockfiles that can be large and are regenerated
            if name_str == "bun.lockb" || name_str == "package-lock.json" {
                // still include them? For reproducibility we include bun.lockb / package-lock if present.
                // But to keep uploads small we skip common heavy lockfiles for now.
                // Decision: include them. Remove the special case.
            }
            archive
                .append_path_with_name(&path, rel_path)
                .with_context(|| format!("adding file {}", rel_path.display()))?;
        }
    }

    Ok(())
}

/// Build a multipart/form-data body for a deployment.
pub fn build_run_multipart_body(
    archive: &[u8],
    project_name: &str,
    project_slug: Option<&str>,
) -> (String, Vec<u8>) {
    let boundary = "----fstak-upload-boundary";
    let mut body = Vec::new();

    append_text_field(&mut body, boundary, "project_name", project_name);
    if let Some(slug) = project_slug {
        append_text_field(&mut body, boundary, "project_slug", slug);
    }

    // code archive field
    body.extend_from_slice(format!("--{boundary}\r\n").as_bytes());
    body.extend_from_slice(
        b"Content-Disposition: form-data; name=\"code\"; filename=\"code.tar.gz\"\r\n",
    );
    body.extend_from_slice(b"Content-Type: application/gzip\r\n");
    body.extend_from_slice(b"\r\n");
    body.extend_from_slice(archive);

    // Closing boundary
    body.extend_from_slice(format!("\r\n--{boundary}--\r\n").as_bytes());

    let content_type = format!("multipart/form-data; boundary={boundary}");
    (content_type, body)
}

fn append_text_field(body: &mut Vec<u8>, boundary: &str, name: &str, value: &str) {
    body.extend_from_slice(format!("--{boundary}\r\n").as_bytes());
    body.extend_from_slice(
        format!("Content-Disposition: form-data; name=\"{name}\"\r\n").as_bytes(),
    );
    body.extend_from_slice(b"\r\n");
    body.extend_from_slice(value.as_bytes());
    body.extend_from_slice(b"\r\n");
}

#[derive(Deserialize)]
pub struct RunResponse {
    pub url: String,
    pub project_name: String,
    pub project_slug: String,
}

pub fn post_run(
    api_url: &str,
    token: &str,
    archive: &[u8],
    project_name: &str,
    project_slug: Option<&str>,
    verbose: bool,
) -> Result<RunResponse> {
    let url = format!("{}/run", api_url.trim_end_matches('/'));
    if verbose {
        ui::verbose(&format!("POST {url}"));
        ui::verbose(&format!("Archive size: {} bytes", archive.len()));
        ui::verbose(&format!("Project: {project_name}"));
        if let Some(slug) = project_slug {
            ui::verbose(&format!("Project slug: {slug}"));
        }
    }

    let (content_type, body) = build_run_multipart_body(archive, project_name, project_slug);

    match ureq::post(&url)
        .set("Authorization", &format!("Bearer {token}"))
        .set("Content-Type", &content_type)
        .send_bytes(&body)
    {
        Ok(resp) => {
            let run_resp: RunResponse = resp.into_json().context("parsing run response")?;
            Ok(run_resp)
        }
        Err(ureq::Error::Status(code, resp)) => {
            if code == 401 || code == 403 {
                bail!("session invalid or expired. Run `fstak login` to re-authenticate.");
            }
            let body = resp.into_string().unwrap_or_else(|_| "<no body>".into());
            if let Some(detail) = parse_error_body(&body) {
                ui::warn(&detail);
                std::process::exit(1);
            }
            bail!("POST {url} returned {code}: {body}");
        }
        Err(ureq::Error::Transport(t)) => bail!("POST {url} failed: {t}"),
    }
}

#[derive(Deserialize)]
pub struct EnvListItem {
    pub key: String,
    pub updated_at: Option<String>,
}

#[derive(Deserialize)]
pub struct EnvListResponse {
    pub project_slug: String,
    pub project_name: String,
    pub variables: Vec<EnvListItem>,
}

#[derive(Deserialize)]
pub struct DepListItem {
    pub name: String,
    pub requirement: String,
    pub updated_at: Option<String>,
}

#[derive(Deserialize)]
pub struct DepListResponse {
    pub project_slug: String,
    pub project_name: String,
    pub dependencies: Vec<DepListItem>,
}

#[derive(Deserialize)]
#[allow(dead_code)]
pub struct PubResponse {
    pub slug: String,
    pub url: String,
}

pub fn env_list(api_url: &str, token: &str, project_slug: &str) -> Result<EnvListResponse> {
    let url = format!(
        "{}/projects/{}/env",
        api_url.trim_end_matches('/'),
        percent_encode_query_value(project_slug)
    );
    match ureq::get(&url)
        .set("Authorization", &format!("Bearer {token}"))
        .call()
    {
        Ok(resp) => resp.into_json().context("parsing env list response"),
        Err(ureq::Error::Status(401, _)) | Err(ureq::Error::Status(403, _)) => {
            bail!("session invalid or expired. Run `fstak login` to re-authenticate.")
        }
        Err(ureq::Error::Status(code, resp)) => {
            let body = resp.into_string().unwrap_or_else(|_| "<no body>".into());
            if let Some(detail) = parse_error_body(&body) {
                bail!("{detail}");
            }
            bail!("GET {url} returned {code}: {body}");
        }
        Err(ureq::Error::Transport(t)) => bail!("GET {url} failed: {t}"),
    }
}

pub fn env_set(
    api_url: &str,
    token: &str,
    project_slug: &str,
    key: &str,
    value: &str,
) -> Result<()> {
    let url = format!(
        "{}/projects/{}/env/{}",
        api_url.trim_end_matches('/'),
        percent_encode_query_value(project_slug),
        percent_encode_query_value(key)
    );
    let payload = serde_json::json!({ "value": value });
    match ureq::put(&url)
        .set("Authorization", &format!("Bearer {token}"))
        .set("Content-Type", "application/json")
        .send_string(&payload.to_string())
    {
        Ok(_) => Ok(()),
        Err(ureq::Error::Status(401, _)) | Err(ureq::Error::Status(403, _)) => {
            bail!("session invalid or expired. Run `fstak login` to re-authenticate.")
        }
        Err(ureq::Error::Status(code, resp)) => {
            let body = resp.into_string().unwrap_or_else(|_| "<no body>".into());
            if let Some(detail) = parse_error_body(&body) {
                bail!("{detail}");
            }
            bail!("PUT {url} returned {code}: {body}");
        }
        Err(ureq::Error::Transport(t)) => bail!("PUT {url} failed: {t}"),
    }
}

pub fn env_unset(api_url: &str, token: &str, project_slug: &str, key: &str) -> Result<()> {
    let url = format!(
        "{}/projects/{}/env/{}",
        api_url.trim_end_matches('/'),
        percent_encode_query_value(project_slug),
        percent_encode_query_value(key)
    );
    match ureq::delete(&url)
        .set("Authorization", &format!("Bearer {token}"))
        .call()
    {
        Ok(_) => Ok(()),
        Err(ureq::Error::Status(401, _)) | Err(ureq::Error::Status(403, _)) => {
            bail!("session invalid or expired. Run `fstak login` to re-authenticate.")
        }
        Err(ureq::Error::Status(code, resp)) => {
            let body = resp.into_string().unwrap_or_else(|_| "<no body>".into());
            if let Some(detail) = parse_error_body(&body) {
                bail!("{detail}");
            }
            bail!("DELETE {url} returned {code}: {body}");
        }
        Err(ureq::Error::Transport(t)) => bail!("DELETE {url} failed: {t}"),
    }
}

pub fn dep_set(
    api_url: &str,
    token: &str,
    project_slug: &str,
    name: &str,
    requirement: &str,
) -> Result<()> {
    let url = format!(
        "{}/projects/{}/deps/{}",
        api_url.trim_end_matches('/'),
        percent_encode_query_value(project_slug),
        percent_encode_query_value(name)
    );
    let payload = serde_json::json!({ "requirement": requirement });
    match ureq::put(&url)
        .set("Authorization", &format!("Bearer {token}"))
        .set("Content-Type", "application/json")
        .send_string(&payload.to_string())
    {
        Ok(_) => Ok(()),
        Err(ureq::Error::Status(401, _)) | Err(ureq::Error::Status(403, _)) => {
            bail!("session invalid or expired. Run `fstak login` to re-authenticate.")
        }
        Err(ureq::Error::Status(code, resp)) => {
            let body = resp.into_string().unwrap_or_else(|_| "<no body>".into());
            if let Some(detail) = parse_error_body(&body) {
                bail!("{detail}");
            }
            bail!("PUT {url} returned {code}: {body}");
        }
        Err(ureq::Error::Transport(t)) => bail!("PUT {url} failed: {t}"),
    }
}

pub fn dep_unset(api_url: &str, token: &str, project_slug: &str, name: &str) -> Result<()> {
    let url = format!(
        "{}/projects/{}/deps/{}",
        api_url.trim_end_matches('/'),
        percent_encode_query_value(project_slug),
        percent_encode_query_value(name)
    );
    match ureq::delete(&url)
        .set("Authorization", &format!("Bearer {token}"))
        .call()
    {
        Ok(_) => Ok(()),
        Err(ureq::Error::Status(401, _)) | Err(ureq::Error::Status(403, _)) => {
            bail!("session invalid or expired. Run `fstak login` to re-authenticate.")
        }
        Err(ureq::Error::Status(code, resp)) => {
            let body = resp.into_string().unwrap_or_else(|_| "<no body>".into());
            if let Some(detail) = parse_error_body(&body) {
                bail!("{detail}");
            }
            bail!("DELETE {url} returned {code}: {body}");
        }
        Err(ureq::Error::Transport(t)) => bail!("DELETE {url} failed: {t}"),
    }
}

pub fn dep_list(
    api_url: &str,
    token: &str,
    project_slug: &str,
) -> Result<DepListResponse> {
    let url = format!(
        "{}/projects/{}/deps",
        api_url.trim_end_matches('/'),
        percent_encode_query_value(project_slug)
    );
    match ureq::get(&url)
        .set("Authorization", &format!("Bearer {token}"))
        .call()
    {
        Ok(resp) => resp.into_json().context("parsing dependency list response"),
        Err(ureq::Error::Status(401, _)) | Err(ureq::Error::Status(403, _)) => {
            bail!("session invalid or expired. Run `fstak login` to re-authenticate.")
        }
        Err(ureq::Error::Status(code, resp)) => {
            let body = resp.into_string().unwrap_or_else(|_| "<no body>".into());
            if let Some(detail) = parse_error_body(&body) {
                bail!("{detail}");
            }
            bail!("GET {url} returned {code}: {body}");
        }
        Err(ureq::Error::Transport(t)) => bail!("GET {url} failed: {t}"),
    }
}

pub fn post_feedback<T: serde::Serialize>(
    api_url: &str,
    token: Option<&str>,
    message: &str,
    context: &T,
) -> Result<()> {
    let url = format!("{}/feedback", api_url.trim_end_matches('/'));
    let _payload = serde_json::to_string(&context).context("serializing feedback context")?;
    // We send a minimal envelope similar to spx
    let full = serde_json::json!({
        "message": message,
        "context": context,
    });
    let body = serde_json::to_string(&full).context("serializing feedback payload")?;
    if body.len() > MAX_FEEDBACK_BYTES {
        bail!("feedback submission exceeds 1MB limit ({} bytes)", body.len());
    }

    let mut req = ureq::post(&url).set("Content-Type", "application/json");
    if let Some(t) = token {
        req = req.set("Authorization", &format!("Bearer {t}"));
    }

    match req.send_string(&body) {
        Ok(_) => Ok(()),
        Err(ureq::Error::Status(code, resp)) => {
            let resp_body = resp.into_string().unwrap_or_else(|_| "<no body>".into());
            if let Some(detail) = parse_error_body(&resp_body) {
                bail!("{detail}");
            }
            bail!("POST {url} returned {code}: {resp_body}");
        }
        Err(ureq::Error::Transport(t)) => bail!("POST {url} failed: {t}"),
    }
}

pub fn percent_encode_query_value(value: &str) -> String {
    let mut out = String::new();
    for byte in value.bytes() {
        match byte {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => {
                out.push(byte as char);
            }
            _ => out.push_str(&format!("%{byte:02X}")),
        }
    }
    out
}

/// Extract the `detail` field from a JSON error response, if present.
pub fn parse_error_body(body: &str) -> Option<String> {
    let v: serde_json::Value = serde_json::from_str(body).ok()?;
    Some(v.get("detail")?.as_str()?.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_error_body_extracts_detail() {
        let body = r#"{"detail":"something went wrong"}"#;
        assert_eq!(parse_error_body(body).unwrap(), "something went wrong");
    }

    #[test]
    fn parse_error_body_invalid_json() {
        assert!(parse_error_body("not json").is_none());
    }

    #[test]
    fn create_archive_excludes_correctly() {
        let tmp = tempfile::tempdir().unwrap();
        let root = tmp.path();

        // Create files and directories
        std::fs::write(root.join("package.json"), "{}").unwrap();
        std::fs::create_dir(root.join("src")).unwrap();
        std::fs::write(root.join("src/App.tsx"), "export {}").unwrap();
        std::fs::create_dir(root.join("node_modules")).unwrap();
        std::fs::write(root.join("node_modules/react"), "").unwrap();
        std::fs::create_dir(root.join("dist")).unwrap();
        std::fs::write(root.join("dist/index.html"), "").unwrap();
        std::fs::create_dir(root.join(".git")).unwrap();
        std::fs::write(root.join(".git/config"), "").unwrap();
        std::fs::create_dir(root.join(".fstak")).unwrap();
        std::fs::write(root.join(".fstak/state.json"), "").unwrap();

        let archive_bytes = create_archive(root).unwrap();
        assert!(!archive_bytes.is_empty());

        let decoder = flate2::read::GzDecoder::new(&archive_bytes[..]);
        let mut archive = tar::Archive::new(decoder);
        let paths: Vec<String> = archive
            .entries()
            .unwrap()
            .filter_map(|e| e.ok())
            .map(|e| e.path().unwrap().to_string_lossy().to_string())
            .collect();

        assert!(paths.iter().any(|p| p == "package.json"));
        assert!(paths.iter().any(|p| p == "src/App.tsx"));
        assert!(!paths.iter().any(|p| p.starts_with("node_modules")));
        assert!(!paths.iter().any(|p| p.starts_with("dist")));
        assert!(!paths.iter().any(|p| p.starts_with(".git")));
        assert!(!paths.iter().any(|p| p.starts_with(".fstak")));
    }
}
