package prompts

import (
	"encoding/json"
	"testing"
)

func TestValidateBasic(t *testing.T) {
	tests := []struct {
		name   string
		data   string
		schema string
		pass   bool
	}{
		{
			name:   "valid string",
			data:   `"hello"`,
			schema: `{"type": "string"}`,
			pass:   true,
		},
		{
			name:   "wrong type",
			data:   `42`,
			schema: `{"type": "string"}`,
			pass:   false,
		},
		{
			name:   "object with required fields",
			data:   `{"name": "Alice", "age": 30}`,
			schema: `{"type": "object", "properties": {"name": {"type": "string"}, "age": {"type": "number"}}, "required": ["name", "age"]}`,
			pass:   true,
		},
		{
			name:   "missing required field",
			data:   `{"name": "Alice"}`,
			schema: `{"type": "object", "properties": {"name": {"type": "string"}, "age": {"type": "number"}}, "required": ["name", "age"]}`,
			pass:   false,
		},
		{
			name:   "nested object",
			data:   `{"user": {"name": "Alice", "age": 30}}`,
			schema: `{"type": "object", "properties": {"user": {"type": "object", "properties": {"name": {"type": "string"}, "age": {"type": "number"}}, "required": ["name", "age"]}}, "required": ["user"]}`,
			pass:   true,
		},
		{
			name:   "array validation",
			data:   `[1, 2, 3]`,
			schema: `{"type": "array", "items": {"type": "number"}}`,
			pass:   true,
		},
		{
			name:   "enum validation",
			data:   `"high"`,
			schema: `{"type": "string", "enum": ["low", "medium", "high"]}`,
			pass:   true,
		},
		{
			name:   "enum failure",
			data:   `"critical"`,
			schema: `{"type": "string", "enum": ["low", "medium", "high"]}`,
			pass:   false,
		},
		{
			name:   "null schema always valid",
			data:   `"anything"`,
			schema: ``,
			pass:   true,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			err := Validate([]byte(tt.data), tt.schema)
			if tt.pass && err != nil {
				t.Errorf("Expected pass, got: %v", err)
			}
			if !tt.pass && err == nil {
				t.Error("Expected error, got nil")
			}
		})
	}
}

func TestDefineAndGet(t *testing.T) {
	// This test needs a real index.db — skip if not available
	reg := NewRegistry("/Users/azfar.naufal/Documents/mycelium")
	if reg.dbPath == "" {
		t.Skip("No mycelium root")
	}

	p := Prompt{
		Name:        "test-extract-person",
		Template:    "Extract name and age from: {{text}}",
		InputShape:  `{"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}`,
		OutputShape: `{"type": "object", "properties": {"name": {"type": "string"}, "age": {"type": "number"}}, "required": ["name", "age"]}`,
		Description: "Test prompt",
	}

	if err := reg.Define(p); err != nil {
		t.Fatalf("Define failed: %v", err)
	}

	got, err := reg.Get("test-extract-person")
	if err != nil {
		t.Fatalf("Get failed: %v", err)
	}
	if got == nil {
		t.Fatal("Expected prompt, got nil")
	}
	if got.Name != "test-extract-person" {
		t.Errorf("Expected name 'test-extract-person', got %q", got.Name)
	}

	// Clean up
	reg.Delete("test-extract-person")
}

func TestExecute(t *testing.T) {
	p := Prompt{
		Name:        "test-exec",
		Template:    "Extract from: {{text}}",
		InputShape:  `{"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}`,
		OutputShape: `{"type": "object", "properties": {"result": {"type": "string"}}, "required": ["result"]}`,
	}

	input := json.RawMessage(`{"text": "hello world"}`)
	result, err := p.Execute(input)
	if err != nil {
		t.Fatalf("Execute failed: %v", err)
	}
	expected := "Extract from: hello world"
	if result != expected {
		t.Errorf("Expected %q, got %q", expected, result)
	}
}
