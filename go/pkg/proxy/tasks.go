package proxy

import (
	"encoding/json"
	"fmt"
	"strings"
	"time"

	"github.com/naufalworks/mycelium/go/pkg/cache"
	"github.com/naufalworks/mycelium/go/pkg/tasks"
)

// handleTaskCreate creates an async task and returns the task ID immediately.
// The task is processed in the background by the worker pool.
func (p *Proxy) handleTaskCreate(params json.RawMessage) (string, error) {
	p.initTaskQueue()

	var input struct {
		Prompt string `json:"prompt"`
	}
	if err := json.Unmarshal(params, &input); err != nil {
		return "", fmt.Errorf("invalid params: %w", err)
	}
	if input.Prompt == "" {
		return "", fmt.Errorf("prompt is required")
	}

	id, err := p.taskQueue.Create(input.Prompt)
	if err != nil {
		return "", fmt.Errorf("queue: %w", err)
	}

	result, _ := json.Marshal(map[string]interface{}{
		"task_id":   id,
		"status":    "pending",
		"message":   "Task queued. Use task_status to check progress.",
		"estimated": "The task will be processed shortly. Poll with task_status " + id,
	})
	return string(result), nil
}

// handleTaskStatus returns the current status of a task.
func (p *Proxy) handleTaskStatus(params json.RawMessage) (string, error) {
	p.initTaskQueue()

	var input struct {
		ID string `json:"id"`
	}
	if err := json.Unmarshal(params, &input); err != nil {
		return "", fmt.Errorf("invalid params: %w", err)
	}
	if input.ID == "" {
		return "", fmt.Errorf("task ID is required")
	}

	task, err := p.taskQueue.Get(input.ID)
	if err != nil {
		return "", fmt.Errorf("task not found: %w", err)
	}
	if task == nil {
		return "", fmt.Errorf("task %q not found", input.ID)
	}

	// Auto-start the worker pool if there are pending tasks and it's not already running
	if task.Status == "pending" {
		p.initTaskQueue()
	}

	result, _ := json.Marshal(map[string]interface{}{
		"task_id":          task.ID,
		"status":           task.Status,
		"result_artifact":  task.ResultArtifact,
		"error":            task.ErrorMsg,
		"created_at":       task.CreatedAt,
		"completed_at":     task.CompletedAt,
		"retry_count":      task.RetryCount,
	})
	return string(result), nil
}

// handleTaskList returns all tasks, optionally filtered by status.
func (p *Proxy) handleTaskList(params json.RawMessage) (string, error) {
	p.initTaskQueue()

	var input struct {
		Status string `json:"status,omitempty"`
		Limit  int    `json:"limit,omitempty"`
	}
	json.Unmarshal(params, &input)
	if input.Limit <= 0 {
		input.Limit = 10
	}

	taskList, err := p.taskQueue.List(input.Limit, input.Status)
	if err != nil {
		return "", fmt.Errorf("list: %w", err)
	}

	var items []map[string]interface{}
	for _, t := range taskList {
		items = append(items, map[string]interface{}{
			"id":              t.ID,
			"status":          t.Status,
			"prompt_preview":  truncateStr(t.Prompt, 80),
			"result_artifact": t.ResultArtifact,
			"created_at":      t.CreatedAt,
		})
	}

	result, _ := json.Marshal(map[string]interface{}{
		"tasks": items,
		"count": len(items),
	})
	return string(result), nil
}

// handleCacheLookup checks the speculative cache for a matching prediction.
func (p *Proxy) handleCacheLookup(params json.RawMessage) (string, error) {
	p.initSpeculativeCache()

	var input struct {
		Prompt string `json:"prompt"`
	}
	json.Unmarshal(params, &input)
	if input.Prompt == "" {
		return "", nil
	}

	result, artifactID, hit := p.specCache.Lookup(input.Prompt)
	if !hit {
		return "", nil
	}

	// Return cached result
	output, _ := json.Marshal(map[string]interface{}{
		"result":      result,
		"artifact_id": artifactID,
		"cached":      true,
	})
	return string(output), nil
}

// handleCachePrecompute triggers background pre-computation.
func (p *Proxy) handleCachePrecompute(params json.RawMessage) (string, error) {
	p.initSpeculativeCache()

	var input struct {
		Threshold float64 `json:"threshold,omitempty"`
	}
	json.Unmarshal(params, &input)
	if input.Threshold <= 0 {
		input.Threshold = 0.4
	}

	artifacts := p.specCache.Precompute(input.Threshold)
	result, _ := json.Marshal(map[string]interface{}{
		"precomputed": len(artifacts),
		"artifacts":   artifacts,
		"message":     "Pre-computation complete. Results cached and ready for instant recall.",
	})
	return string(result), nil
}

// initTaskQueue initializes the task queue if not already created.
func (p *Proxy) initTaskQueue() {
	if p.taskQueue != nil {
		return
	}
	root := strings.TrimSuffix(p.Brain.LogPath, "log.jsonl")
	llmURL := fmt.Sprintf("http://127.0.0.1:%s/v1/chat/completions", p.Port)
	p.taskQueue = tasks.New(root, llmURL)
	p.taskQueue.StartWorkers()
}

// initSpeculativeCache initializes the speculative cache.
func (p *Proxy) initSpeculativeCache() {
	if p.specCache != nil {
		return
	}
	root := strings.TrimSuffix(p.Brain.LogPath, "log.jsonl")
	llmURL := fmt.Sprintf("http://127.0.0.1:%s/v1/chat/completions", p.Port)
	p.specCache = cache.New(root, llmURL)
}

var _ time.Time
