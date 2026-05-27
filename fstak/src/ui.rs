use colored::Colorize;

/// Print a warning message.
pub fn warn(message: &str) {
    eprintln!("{} {}", "!".bold().yellow(), message);
}

/// Render a clickable URL using OSC 8 hyperlinks for modern terminals.
pub fn hyperlink(url: &str, label: &str) -> String {
    format!("\x1b]8;;{url}\x1b\\{label}\x1b]8;;\x1b\\")
}

/// Print a verbose debug message (only shown with -v).
pub fn verbose(message: &str) {
    eprintln!("{} {}", "[verbose]".dimmed(), message.dimmed());
}
