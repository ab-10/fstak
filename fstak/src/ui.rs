use colored::Colorize;

/// Print a step header (e.g. "[1/7] Pulling fstak base image...")
pub fn step(current: usize, total: usize, message: &str) {
    eprintln!(
        "{} {}",
        format!("[{current}/{total}]").bold().cyan(),
        message
    );
}

/// Print a success message.
pub fn success(message: &str) {
    eprintln!("{} {}", "✓".bold().green(), message);
}

/// Print a warning message.
pub fn warn(message: &str) {
    eprintln!("{} {}", "!".bold().yellow(), message);
}

/// Print an informational message.
pub fn info(message: &str) {
    eprintln!("{} {}", "→".bold().blue(), message);
}

/// Render a clickable URL using OSC 8 hyperlinks for modern terminals.
pub fn hyperlink(url: &str, label: &str) -> String {
    format!("\x1b]8;;{url}\x1b\\{label}\x1b]8;;\x1b\\")
}

/// Stream a command's output line-by-line to stderr (real-time visibility).
pub fn stream_header(command: &str) {
    eprintln!("{} {}", "$".dimmed(), command.dimmed());
}

/// Print a verbose debug message (only shown with -v).
pub fn verbose(message: &str) {
    eprintln!("{} {}", "[verbose]".dimmed(), message.dimmed());
}
