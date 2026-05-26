use std::process::Command;

#[test]
fn not_logged_in_fails_cleanly() {
    let tmp_dir = tempfile::tempdir().expect("tempdir");
    std::fs::write(tmp_dir.path().join("package.json"), "{}").unwrap();

    let fstak_bin = env!("CARGO_BIN_EXE_fstak");
    let output = Command::new(fstak_bin)
        .args(["run"])
        .current_dir(tmp_dir.path())
        .env("HOME", tmp_dir.path()) // no credentials.json here
        .output()
        .expect("run fstak");

    assert!(!output.status.success(), "fstak run should have failed");

    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(
        stderr.contains("fstak login"),
        "stderr should tell the user to run `fstak login`; got:\n{stderr}"
    );
}

#[test]
fn missing_run_arg_fails_cleanly() {
    // `fstak run` takes no arguments. Running it without a project context should fail cleanly.
    let tmp_dir = tempfile::tempdir().expect("tempdir");

    let fstak_bin = env!("CARGO_BIN_EXE_fstak");
    let output = Command::new(fstak_bin)
        .args(["run"])
        .current_dir(tmp_dir.path())
        .env("HOME", tmp_dir.path())
        .output()
        .expect("run fstak");

    // It will fail because there is no state and no credentials.
    // The important thing is it fails with a clear message, not a panic or bad usage.
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(
        !output.status.success(),
        "fstak run in an empty dir should fail"
    );
    assert!(
        stderr.contains("login") || stderr.contains("project"),
        "stderr should mention login or project; got:\n{stderr}"
    );
}
