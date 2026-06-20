package proxy

import (
	"encoding/json"
	"fmt"
	"log"
	"strings"

	"github.com/naufalworks/mycelium/go/pkg/artifacts"
)

// handleArtifactTool intercepts artifact tool calls from Claude:
//   artifact_run <prompt> <input>  — run a prompt, store result as artifact
//   artifact_get <id>              — retrieve a stored artifact
//   artifact_query <sql>           — query artifacts via SQL
//   artifact_ls [type]             — list artifacts by type
func (p *Proxy) handleArtifactTool(name string, params json.RawMessage) (string, error) {
	p.initArtifactStore()

	switch name {
	case "artifact_run":
		return p.artifactRun(params)
	case "artifact_get":
		return p.artifactGet(params)
	case "artifact_query":
		return p.artifactQuery(params)
	case "artifact_ls":
		return p.artifactList(params)
	default:
		return "", fmt.Errorf("unknown artifact tool: %s", name)
	}
}

func (p *Proxy) initArtifactStore() {
	if p.artifactStore == nil {
		root := strings.TrimSuffix(p.Brain.LogPath, "log.jsonl")
		p.artifactStore = artifacts.New(root)
	}
}

// artifactRun executes a prompt and stores the result as an artifact.
func (p *Proxy) artifactRun(params json.RawMessage) (string, error) {
	var input struct {
		Prompt string          `json:"prompt"`
		Data   json.RawMessage `json:"data"`
	}
	if err := json.Unmarshal(params, &input); err != nil {
		return "", err
	}

	// Execute the prompt through the registry
	p.initPromptRegistry()
	prompt, err := p.promptReg.Get(input.Prompt)
	if err != nil || prompt == nil {
		return "", fmt.Errorf("prompt %q not found", input.Prompt)
	}

	rendered, err := prompt.Execute(input.Data)
	if err != nil {
		return "", fmt.Errorf("prompt execute: %w", err)
	}

	// Store as artifact
	a := &artifacts.Artifact{
		Type:          input.Prompt,
		Name:          input.Prompt,
		Data:          []byte(`{"prompt":"` + rendered + `"}`),
		Prompt:        input.Prompt,
		PromptVersion: fmt.Sprintf("v%d", prompt.Version),
		InputSummary:  string(input.Data)[:min(len(string(input.Data)), 100)],
	}

	if err := p.artifactStore.Store(a); err != nil {
		return "", err
	}

	log.Printf("[artifacts] Stored: %s (%s)", a.ID, a.Type)

	result, _ := json.Marshal(map[string]interface{}{
		"artifact_id": a.ID,
		"type":        a.Type,
		"message":     "Stored. Use artifact_get " + a.ID + " to retrieve.",
	})
	return string(result), nil
}

// artifactGet retrieves a stored artifact by ID.
func (p *Proxy) artifactGet(params json.RawMessage) (string, error) {
	var input struct {
		ID string `json:"id"`
	}
	if err := json.Unmarshal(params, &input); err != nil {
		return "", err
	}

	p.initArtifactStore()
	a, err := p.artifactStore.Get(input.ID)
	if err != nil {
		return "", err
	}
	if a == nil {
		return "", fmt.Errorf("artifact %q not found", input.ID)
	}

	result, _ := json.Marshal(map[string]interface{}{
		"id":      a.ID,
		"type":    a.Type,
		"data":    json.RawMessage(a.Data),
		"created": a.CreatedAt,
		"cost":    a.TokenCost,
	})
	return string(result), nil
}

// artifactQuery runs a SQL query over artifacts.
func (p *Proxy) artifactQuery(params json.RawMessage) (string, error) {
	var input struct {
		SQL string `json:"sql"`
	}
	if err := json.Unmarshal(params, &input); err != nil {
		return "", err
	}

	p.initArtifactStore()
	cols, rows, err := p.artifactStore.Query(input.SQL)
	if err != nil {
		return "", err
	}

	result, _ := json.Marshal(map[string]interface{}{
		"columns": cols,
		"rows":    rows,
		"count":   len(rows),
	})
	return string(result), nil
}

// artifactList lists artifacts by type.
func (p *Proxy) artifactList(params json.RawMessage) (string, error) {
	var input struct {
		Type   string `json:"type,omitempty"`
		Limit  int    `json:"limit,omitempty"`
		Offset int    `json:"offset,omitempty"`
	}
	json.Unmarshal(params, &input)
	if input.Limit <= 0 {
		input.Limit = 20
	}

	p.initArtifactStore()
	results, err := p.artifactStore.List(input.Type, input.Limit, input.Offset)
	if err != nil {
		return "", err
	}

	var items []map[string]interface{}
	for _, a := range results {
		items = append(items, map[string]interface{}{
			"id":      a.ID,
			"type":    a.Type,
			"name":    a.Name,
			"created": a.CreatedAt,
			"cost":    a.TokenCost,
		})
	}

	result, _ := json.Marshal(map[string]interface{}{
		"artifacts": items,
		"count":     len(items),
	})
	return string(result), nil
}

func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}
