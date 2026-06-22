//! Page view components for the Mycelium web frontend.

mod dashboard;
mod memory;
mod artifacts;
mod workflows;
mod settings;
mod graph;

pub use dashboard::DashboardView;
pub use memory::MemoryView;
pub use artifacts::ArtifactsView;
pub use workflows::WorkflowsView;
pub use settings::SettingsView;
pub use graph::GraphView;
