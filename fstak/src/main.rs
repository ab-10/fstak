mod cli;
mod commands;
mod config;
mod credentials;
mod ui;

use anyhow::Result;
use clap::Parser;
use cli::{Cli, Command};

fn main() -> Result<()> {
    let cli = Cli::parse();
    let verbose = cli.verbose;

    match cli.command {
        Command::Run => commands::run::run(verbose),
        Command::New(args) => commands::new::new_project(args, verbose),
        Command::Login(args) => match args.code {
            Some(code) => commands::login::login_with_code(&code, verbose),
            None => commands::login::login(verbose),
        },
        Command::Logout => commands::logout::logout(verbose),
        Command::Whoami => commands::whoami::whoami(verbose),
        Command::Kill(args) => commands::kill::kill(args, verbose),
        Command::Ps => commands::ps::ps(verbose),
        Command::Logs(args) => commands::logs::logs(args, verbose),
        Command::Env(args) => commands::env::env(args, verbose),
        Command::Add(args) => commands::add::add(args, verbose),
        Command::Feedback(args) => commands::feedback::feedback(args, verbose),
    }
}
