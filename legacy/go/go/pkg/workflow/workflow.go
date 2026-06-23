// Package workflow defines and executes structured, trackable multi-step
// AI agent workflows ("loops"). Each workflow is a prompt with:
//   - Steps (ordered actions)
//   - Verification criteria per step
//   - Stopping conditions (failure, block, timeout, completion)
//   - Audit trail (every step result stored as artifact)
//
// Integration with existing mycelium infra:
//   - Workflow definitions stored as prompts (go/pkg/prompts)
//   - Step results stored as artifacts (go/pkg/artifacts)
//   - Execution status via async tasks (go/pkg/tasks)
//   - Progress tracked in memory_facts
package workflow

import (
	"encoding/json"
	"fmt"
	"strings"
	"sync"
	"time"

	"github.com/naufalworks/mycelium/go/pkg/artifacts"
	"github.com/naufalworks/mycelium/go/pkg/prompts"
)

// Step defines a single action within a workflow.
type Step struct {
	Order        int    `json:"order"`
	Name         string `json:"name"`
	Prompt       string `json:"prompt"`
	Verification string `json:"verification,omitempty"`
	StopOnFail   bool   `json:"stop_on_fail"`
}

// Workflow is a collection of steps with a stopping condition.
type Workflow struct {
	Name        string `json:"name"`
	Description string `json:"description"`
	Steps       []Step `json:"steps"`
	StopOn      string `json:"stop_on"` // "failure", "block", "timeout"
	MaxRetries  int    `json:"max_retries"`
	CreatedAt   string `json:"created_at"`
	Version     int    `json:"version"`
}

// RunState tracks the live state of a workflow execution.
type RunState struct {
	ID          string       `json:"id"`          // unique run ID
	Workflow    string       `json:"workflow"`    // workflow name
	Status      string       `json:"status"`      // running, paused, done, failed
	StepResults []StepResult `json:"step_results"`
	CurrentStep int          `json:"current_step"`
	CreatedAt   string       `json:"created_at"`
	UpdatedAt   string       `json:"updated_at"`
	Error       string       `json:"error,omitempty"`
	mu          sync.Mutex
}

// StepResult captures the outcome of a single step.
type StepResult struct {
	Step       int    `json:"step"`
	Name       string `json:"name"`
	Status     string `json:"status"` // pending, running, passed, failed, skipped
	ArtifactID string `json:"artifact_id,omitempty"`
	Output     string `json:"output,omitempty"`
	Error      string `json:"error,omitempty"`
	DurationMs int    `json:"duration_ms"`
	StartedAt  string `json:"started_at,omitempty"`
	UpdatedAt  string `json:"updated_at,omitempty"`
}

// ProgressCallback is called after each step completes with the current status.
type ProgressCallback func(runID, workflowName, stepName string, stepIdx, totalSteps int, status string, durationMs int)

// Engine manages workflow definitions and executions.
type Engine struct {
	promptReg      *prompts.Registry
	artifactStore  *artifacts.Store
	myceliumRoot   string
	mu             sync.Mutex
	runs           map[string]*RunState
	ProgressFn     ProgressCallback // optional: called after each step
	DefaultTimeout time.Duration   // per-step timeout (0 = no timeout)
}

// New creates a workflow engine.
func New(myceliumRoot string) *Engine {
	return &Engine{
		promptReg:    prompts.NewRegistry(myceliumRoot),
		artifactStore: artifacts.New(myceliumRoot),
		myceliumRoot: myceliumRoot,
		runs:         make(map[string]*RunState),
	}
}

// List returns all defined workflows.
func (e *Engine) List() ([]*Workflow, error) {
	prompts, err := e.promptReg.List()
	if err != nil {
		return nil, err
	}

	var workflows []*Workflow
	for _, p := range prompts {
		var w Workflow
		if json.Unmarshal([]byte(p.Template), &w) == nil && len(w.Steps) > 0 {
			workflows = append(workflows, &w)
		}
	}
	return workflows, nil
}

// Start begins executing a workflow. Returns a run ID for tracking.
func (e *Engine) Start(name string) (string, error) {
	w, err := e.Get(name)
	if err != nil || w == nil {
		return "", fmt.Errorf("workflow %q not found", name)
	}

	now := time.Now().UTC().Format(time.RFC3339)
	runID := fmt.Sprintf("wf_%s_%x", name, time.Now().UnixNano())

	run := &RunState{
		ID:          runID,
		Workflow:    name,
		Status:      "running",
		CurrentStep: 0,
		CreatedAt:   now,
		UpdatedAt:   now,
		StepResults: make([]StepResult, len(w.Steps)),
	}

	// Initialize step results
	for i, step := range w.Steps {
		run.StepResults[i] = StepResult{
			Step:   step.Order,
			Name:   step.Name,
			Status: "pending",
		}
	}

	e.mu.Lock()
	e.runs[runID] = run
	e.mu.Unlock()

	// Execute steps in background
	go e.execute(run, w)

	return runID, nil
}

// execute runs a workflow's steps sequentially.
func (e *Engine) execute(run *RunState, w *Workflow) {
	timeout := e.DefaultTimeout
	if timeout == 0 {
		timeout = 5 * time.Minute
	}

	for i, step := range w.Steps {
		run.mu.Lock()
		run.CurrentStep = i
		run.StepResults[i].Status = "running"
		run.StepResults[i].StartedAt = time.Now().UTC().Format(time.RFC3339)
		run.UpdatedAt = time.Now().UTC().Format(time.RFC3339)
		run.mu.Unlock()

		// Execute the step (in production, this would call an LLM or subprocess)
		start := time.Now()
		result, err := e.executeStep(step, timeout)
		elapsed := time.Since(start)

		run.mu.Lock()
		run.StepResults[i].DurationMs = int(elapsed.Milliseconds())
		run.StepResults[i].UpdatedAt = time.Now().UTC().Format(time.RFC3339)

		if err != nil {
			run.StepResults[i].Status = "failed"
			run.StepResults[i].Error = err.Error()
		} else {
			run.StepResults[i].Status = "passed"
			run.StepResults[i].Output = result
		}

		// Store as artifact
		artifactID := fmt.Sprintf("wf_step_%x", time.Now().UnixNano())
		data, _ := json.Marshal(map[string]string{
			"workflow": run.Workflow,
			"step":     step.Name,
			"output":   result,
			"error":    func() string { if err != nil { return err.Error() }; return "" }(),
		})
		now := time.Now().UTC().Format(time.RFC3339)
		e.artifactStore.Store(&artifacts.Artifact{
			ID:        artifactID,
			Type:      "workflow-step",
			Name:      fmt.Sprintf("%s/%s", run.Workflow, step.Name),
			Data:      data,
			CreatedAt: now,
		})
		run.StepResults[i].ArtifactID = artifactID
		run.UpdatedAt = now
		run.mu.Unlock()

		// Progress callback
		if e.ProgressFn != nil {
			status := "passed"
			if err != nil {
				status = "failed"
			}
			e.ProgressFn(run.ID, run.Workflow, step.Name, i+1, len(w.Steps), status, int(elapsed.Milliseconds()))
		}

		// Stop on failure if configured
		if err != nil && step.StopOnFail {
			run.mu.Lock()
			run.Status = "failed"
			run.Error = fmt.Sprintf("step %q failed: %v", step.Name, err)
			run.UpdatedAt = time.Now().UTC().Format(time.RFC3339)
			run.mu.Unlock()
			return
		}
	}

	// All steps completed
	run.mu.Lock()
	run.Status = "done"
	run.CurrentStep = len(w.Steps)
	run.UpdatedAt = time.Now().UTC().Format(time.RFC3339)
	run.mu.Unlock()
}

// executeStep runs a single workflow step. For now it returns a placeholder.
func (e *Engine) executeStep(step Step, timeout time.Duration) (string, error) {
	// In production this would execute the step's prompt via LLM or subprocess.
	// For now, return a simple acknowledgment.
	return fmt.Sprintf("step %q completed (simulated)", step.Name), nil
}

// Get retrieves a workflow definition by name.
func (e *Engine) Get(name string) (*Workflow, error) {
	p, err := e.promptReg.Get(name)
	if err != nil {
		return nil, err
	}
	if p == nil {
		return nil, nil
	}

	var w Workflow
	if err := json.Unmarshal([]byte(p.Template), &w); err != nil {
		return nil, err
	}
	return &w, nil
}

// Status returns the current state of a workflow run.
func (e *Engine) Status(runID string) (*RunState, error) {
	e.mu.Lock()
	run, ok := e.runs[runID]
	e.mu.Unlock()
	if !ok {
		return nil, fmt.Errorf("run %q not found", runID)
	}
	return run, nil
}

// ReportStep updates the result of a workflow step (for external executors).
func (e *Engine) ReportStep(runID, stepName, output, errMsg string) error {
	e.mu.Lock()
	run, ok := e.runs[runID]
	e.mu.Unlock()
	if !ok {
		return fmt.Errorf("run %q not found", runID)
	}

	run.mu.Lock()
	defer run.mu.Unlock()

	// Store step result as artifact
	artifactID := fmt.Sprintf("wf_step_%x", time.Now().UnixNano())
	data, _ := json.Marshal(map[string]string{
		"workflow": run.Workflow,
		"step":     stepName,
		"output":   output,
		"error":    errMsg,
	})
	now := time.Now().UTC().Format(time.RFC3339)

	e.artifactStore.Store(&artifacts.Artifact{
		ID:        artifactID,
		Type:      "workflow-step",
		Name:      fmt.Sprintf("%s/%s", run.Workflow, stepName),
		Data:      data,
		CreatedAt: now,
	})

	// Update step result
	if run.CurrentStep < len(run.StepResults) {
		result := &run.StepResults[run.CurrentStep]
		result.Status = "passed"
		result.Output = output
		result.ArtifactID = artifactID
		if errMsg != "" {
			result.Status = "failed"
			result.Error = errMsg
		}
		result.UpdatedAt = now
	}

	run.UpdatedAt = now

	// Check if we should mark run as failed
	if errMsg != "" && run.CurrentStep < len(run.StepResults) {
		step := run.StepResults[run.CurrentStep]
		if step.Status == "failed" {
			run.Status = "failed"
			run.Error = fmt.Sprintf("step %q failed: %s", stepName, errMsg)
			return nil
		}
	}

	return nil
}

// ReportDone marks a workflow run as complete and advances to the next step.
func (e *Engine) ReportDone(runID string) error {
	e.mu.Lock()
	run, ok := e.runs[runID]
	e.mu.Unlock()
	if !ok {
		return fmt.Errorf("run %q not found", runID)
	}

	run.mu.Lock()
	defer run.mu.Unlock()
	run.CurrentStep++

	// Check if all steps are done
	if run.CurrentStep >= len(run.StepResults) {
		run.Status = "done"
		run.UpdatedAt = time.Now().UTC().Format(time.RFC3339)
	} else {
		run.StepResults[run.CurrentStep].Status = "running"
		run.UpdatedAt = time.Now().UTC().Format(time.RFC3339)
	}

	return nil
}

// Log returns the human-readable log of a workflow run.
func (e *Engine) Log(runID string) (string, error) {
	e.mu.Lock()
	run, ok := e.runs[runID]
	e.mu.Unlock()
	if !ok {
		return "", fmt.Errorf("run %q not found", runID)
	}

	run.mu.Lock()
	defer run.mu.Unlock()

	var b strings.Builder
	b.WriteString(fmt.Sprintf("Workflow: %s\n", run.Workflow))
	b.WriteString(fmt.Sprintf("Run ID:   %s\n", run.ID))
	b.WriteString(fmt.Sprintf("Status:   %s\n", run.Status))
	b.WriteString(fmt.Sprintf("Started:  %s\n", run.CreatedAt))
	b.WriteString(fmt.Sprintf("\nSteps (%d/%d):\n", run.CurrentStep, len(run.StepResults)))

	for _, sr := range run.StepResults {
		icon := map[string]string{
			"passed": "✅", "failed": "❌", "running": "▶",
			"pending": "☐", "skipped": "⏭",
		}[sr.Status]
		b.WriteString(fmt.Sprintf("\n  %s %s\n", icon, sr.Name))
		b.WriteString(fmt.Sprintf("     Status: %s\n", sr.Status))
		b.WriteString(fmt.Sprintf("     Duration: %dms\n", sr.DurationMs))
		if sr.ArtifactID != "" {
			b.WriteString(fmt.Sprintf("     Artifact: %s\n", sr.ArtifactID))
		}
		if sr.Error != "" {
			b.WriteString(fmt.Sprintf("     Error: %s\n", sr.Error))
		}
		if sr.Output != "" {
			b.WriteString(fmt.Sprintf("     Output: %s\n", sr.Output))
		}
	}

	return b.String(), nil
}
