// Prevents additional console window on Windows in release, DO NOT REMOVE!!
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::sync::Mutex;
#[cfg(not(debug_assertions))]
use tauri::api::process::{Command, CommandEvent};
use tauri::api::process::CommandChild;
use tauri::Manager;

struct BackendState {
  child: Option<CommandChild>,
}

fn main() {
  tauri::Builder::default()
    .manage(Mutex::new(BackendState { child: None }))
    .setup(|app| {
      #[cfg(not(debug_assertions))]
      {
        let (mut rx, child) = Command::new_sidecar("backend")
          .expect("failed to create sidecar command")
          .spawn()
          .expect("failed to spawn sidecar");

        let state = app.state::<Mutex<BackendState>>();
        state.lock().unwrap().child = Some(child);

        tauri::async_runtime::spawn(async move {
          while let Some(event) = rx.recv().await {
            match event {
              CommandEvent::Stdout(line) => {
                println!("[Backend] {}", line);
              }
              CommandEvent::Stderr(line) => {
                eprintln!("[Backend Error] {}", line);
              }
              _ => {}
            }
          }
        });
      }

      #[cfg(debug_assertions)]
      let _ = app;

      Ok(())
    })
    .on_window_event(|event| {
      if let tauri::WindowEvent::CloseRequested { .. } = event.event() {
        let child = {
          let state = event.window().state::<Mutex<BackendState>>();
          let mut guard = state.lock().unwrap();
          guard.child.take()
        };

        if let Some(child) = child {
          let _ = child.kill();
        }
      }
    })
    .run(tauri::generate_context!())
    .expect("error while running tauri application");
}
