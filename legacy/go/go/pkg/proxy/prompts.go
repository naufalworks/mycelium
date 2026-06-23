package proxy

import (
	"encoding/json"
	"log"
	"strings"

	"github.com/naufalworks/mycelium/go/pkg/prompts"
)

// initPromptRegistry lazily initializes the prompt registry.
func (p *Proxy) initPromptRegistry() {
	if p.promptReg == nil {
		// Get Mycelium dir from Brain
		root := p.Brain.LogPath
		if strings.Contains(root, "log.jsonl") {
			root = strings.TrimSuffix(root, "log.jsonl")
		}
		p.promptReg = prompts.NewRegistry(root)
	}
}

// interceptPromptValidation is called after Claude responds.
// If the call matches a registered prompt, it validates the output schema.
// If validation fails, it auto-retries by sending correction feedback.
func (p *Proxy) interceptPromptValidation(userMsg string, responseBody []byte) ([]byte, bool) {
	p.initPromptRegistry()

	// Extract the prompt name from the user message
	// Pattern: users invoke prompts with /prompt <name> or by structured calls
	name := extractPromptName(userMsg)
	if name == "" {
		return responseBody, true
	}

	prompt, err := p.promptReg.Get(name)
	if err != nil || prompt == nil {
		return responseBody, true // no prompt registered, pass through
	}

	// Try to extract JSON output from the response
	jsonData := extractJSONFromResponse(responseBody, prompt.OutputShape)
	if jsonData == nil {
		return responseBody, false // can't parse response as JSON
	}

	// Validate against schema
	if err := prompts.Validate(jsonData, prompt.OutputShape); err != nil {
		log.Printf("[mycelium-proxy] Prompt %q validation failed: %v", name, err)
		return responseBody, false
	}

	return responseBody, true
}

// extractPromptName looks for prompt invocation patterns in the user message.
// Returns the prompt name or empty string.
func extractPromptName(msg string) string {
	msg = strings.TrimSpace(msg)

	// Pattern: /prompt <name> or "run prompt <name>"
	for _, prefix := range []string{"/prompt ", "run prompt ", ".prompt "} {
		if strings.HasPrefix(strings.ToLower(msg), prefix) {
			name := strings.TrimSpace(msg[len(prefix):])
			if idx := strings.IndexAny(name, " \n\t"); idx > 0 {
				name = name[:idx]
			}
			return strings.TrimSpace(name)
		}
	}
	return ""
}

// extractJSONFromResponse attempts to find JSON matching the expected schema
// shape in the LLM response. Returns the raw JSON bytes or nil.
func extractJSONFromResponse(body []byte, outputShape string) []byte {
	text := string(body)

	// Try to find JSON block
	if idx := strings.Index(text, "```json"); idx >= 0 {
		end := strings.Index(text[idx+7:], "```")
		if end >= 0 {
			return []byte(strings.TrimSpace(text[idx+7 : idx+7+end]))
		}
	}

	// Try raw JSON parse
	var tmp interface{}
	if json.Unmarshal(body, &tmp) == nil {
		return body
	}

	// Walk line by line for first { }
	lines := strings.Split(text, "\n")
	for _, line := range lines {
		trimmed := strings.TrimSpace(line)
		if strings.HasPrefix(trimmed, "{") {
			var tmp interface{}
			if json.Unmarshal([]byte(trimmed), &tmp) == nil {
				return []byte(trimmed)
			}
		}
	}

	return nil
}

func truncateStr(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n]
}
