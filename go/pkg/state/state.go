// Package state provides agentic state serialization for mycelium.
// Saves full session context on exit, restores on resume.
// Survives crashes, /new, and session timeouts.
package state

import (
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"strings"
	"time"

	"github.com/naufalworks/mycelium/go/pkg/brain"
)

// AgentState holds the complete serializable state of an agent session.
type AgentState struct {
	Timestamp    string   `json:"ts"`
	Version      int      `json:"version"`
	Session      string   `json:"session"`
	WorkingDir   string   `json:"working_dir,omitempty"`
	GitBranch    string   `json:"git_branch,omitempty"`
	GitHash      string   `json:"git_hash,omitempty"`
	LastCommand  string   `json:"last_command,omitempty"`
	LastOutput   string   `json:"last_output,omitempty"`
	LastError    string   `json:"last_error,omitempty"`
	OpenFiles    []string `json:"open_files,omitempty"`
	TaskDesc     string   `json:"task_desc,omitempty"`
	Attempts     []string `json:"attempts,omitempty"`
	Hypotheses   []string `json:"hypotheses,omitempty"`
	Remaining    []string `json:"remaining,omitempty"`
	BrainEntries int      `json:"brain_entries"`
}

// Save serializes the current agent state to mycelium and returns the entry.
func Save(b *brain.Brain, state *AgentState) (*brain.Entry, error) {
	if state.Timestamp == "" {
		state.Timestamp = time.Now().UTC().Format("2006-01-02T15:04:05Z")
	}
	state.Version = 1
	state.BrainEntries = b.Count()

	// Auto-detect git state
	detectGit(state)
	detectCWD(state)

	// Serialize state as JSON for the assistant field
	stateJSON, _ := json.MarshalIndent(state, "", "  ")
	stateJSON, _ = json.Marshal(state) // compact

	entry := &brain.Entry{
		Type:      "state_snapshot",
		Tier:      "S",
		Session:   state.Session,
		User:      fmt.Sprintf("STATE SAVE: %s — %s", state.Session, state.TaskDesc),
		Assistant: string(stateJSON),
		Entities:  brain.ExtractEntities(state.TaskDesc + " " + state.Session),
	}

	appended, err := b.Append(entry)
	if err != nil {
		return nil, fmt.Errorf("state save: %w", err)
	}
	return appended, nil
}

// Resume loads the most recent state_snapshot entry and restores the state.
func Resume(b *brain.Brain, session string) (*AgentState, error) {
	entries := b.RecentEntries(200)
	for _, e := range entries {
		if e.Type != "state_snapshot" {
			continue
		}
		if session != "" && e.Session != session {
			continue
		}
		var state AgentState
		if err := json.Unmarshal([]byte(e.Assistant), &state); err != nil {
			continue
		}
		return &state, nil
	}
	return nil, nil
}

// CaptureSnapshot builds a state snapshot from the current environment.
func CaptureSnapshot(session, task string) *AgentState {
	state := &AgentState{
		Session:    sessionOrGenerate(session),
		TaskDesc:   task,
		Timestamp:  time.Now().UTC().Format("2006-01-02T15:04:05Z"),
		Version:    1,
		WorkingDir: getCWD(),
	}
	detectGit(state)
	return state
}

// ── Environment detection ───────────────────────────────────────────────────

func detectGit(state *AgentState) {
	dir := state.WorkingDir
	if dir == "" {
		dir = getCWD()
	}

	branch, _ := gitExec(dir, "rev-parse", "--abbrev-ref", "HEAD")
	if branch != "" {
		state.GitBranch = strings.TrimSpace(branch)
	}

	hash, _ := gitExec(dir, "rev-parse", "--short", "HEAD")
	if hash != "" {
		state.GitHash = strings.TrimSpace(hash)
	}
}

func detectCWD(state *AgentState) {
	if state.WorkingDir == "" {
		state.WorkingDir = getCWD()
	}
}

func getCWD() string {
	cwd, err := os.Getwd()
	if err != nil {
		return "."
	}
	return cwd
}

func gitExec(dir string, args ...string) (string, error) {
	cmd := exec.Command("git", args...)
	cmd.Dir = dir
	out, err := cmd.Output()
	if err != nil {
		return "", err
	}
	return string(out), nil
}

func sessionOrGenerate(session string) string {
	if session != "" {
		return session
	}
	return fmt.Sprintf("session-%d", time.Now().Unix())
}

// FormatSessionContext returns a human-readable summary of the agent state.
func FormatSessionContext(state *AgentState) string {
	if state == nil {
		return "No prior session state found."
	}
	var b strings.Builder
	b.WriteString(fmt.Sprintf("## Resume Session: %s\n\n", state.Session))
	b.WriteString(fmt.Sprintf("**Last active:** %s\n", state.Timestamp))

	if state.GitBranch != "" {
		b.WriteString(fmt.Sprintf("**Git branch:** %s (%s)\n", state.GitBranch, state.GitHash))
	}
	if state.WorkingDir != "" {
		b.WriteString(fmt.Sprintf("**Working dir:** `%s`\n", state.WorkingDir))
	}
	if state.LastCommand != "" {
		b.WriteString(fmt.Sprintf("**Last command:** `%s`\n", state.LastCommand))
	}
	if state.LastError != "" {
		b.WriteString(fmt.Sprintf("**Last error:** `%s`\n", state.LastError))
	}
	if state.TaskDesc != "" {
		b.WriteString(fmt.Sprintf("**Task:** %s\n", state.TaskDesc))
	}
	if len(state.Attempts) > 0 {
		b.WriteString("\n**Attempted:**\n")
		for _, a := range state.Attempts {
			b.WriteString(fmt.Sprintf("- %s\n", a))
		}
	}
	if len(state.Hypotheses) > 0 {
		b.WriteString("\n**Hypotheses:**\n")
		for _, h := range state.Hypotheses {
			b.WriteString(fmt.Sprintf("- %s\n", h))
		}
	}
	if len(state.Remaining) > 0 {
		b.WriteString("\n**Remaining:**\n")
		for _, r := range state.Remaining {
			b.WriteString(fmt.Sprintf("- [ ] %s\n", r))
		}
	}
	return b.String()
}
