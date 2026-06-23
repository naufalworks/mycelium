package tasks

import (
	"testing"
)

func TestCreateAndGet(t *testing.T) {
	q := New("/Users/azfar.naufal/Documents/mycelium", "http://127.0.0.1:8443/v1")

	id, err := q.Create("test task prompt")
	if err != nil {
		t.Fatalf("Create failed: %v", err)
	}
	if id == "" {
		t.Fatal("Expected non-empty ID")
	}

	task, err := q.Get(id)
	if err != nil {
		t.Fatalf("Get failed: %v", err)
	}
	if task == nil {
		t.Fatal("Expected non-nil task")
	}
	if task.Status != StatusPending {
		t.Errorf("Expected status pending, got %q", task.Status)
	}
	if task.Prompt != "test task prompt" {
		t.Errorf("Expected prompt 'test task prompt', got %q", task.Prompt)
	}

	// Clean up
	db, _ := q.db()
	db.Exec("DELETE FROM tasks WHERE id=?", id)
	db.Close()
}

func TestCreateEmptyPrompt(t *testing.T) {
	q := New("/Users/azfar.naufal/Documents/mycelium", "http://127.0.0.1:8443/v1")
	_, err := q.Create("")
	if err == nil {
		t.Error("Expected error for empty prompt")
	}
}

func TestGetNonexistent(t *testing.T) {
	q := New("/Users/azfar.naufal/Documents/mycelium", "http://127.0.0.1:8443/v1")
	task, err := q.Get("nonexistent_id")
	if err != nil {
		t.Fatalf("Get failed: %v", err)
	}
	if task != nil {
		t.Error("Expected nil for nonexistent task")
	}
}

func TestList(t *testing.T) {
	q := New("/Users/azfar.naufal/Documents/mycelium", "http://127.0.0.1:8443/v1")

	// Create a couple tasks
	q.Create("list test 1")
	q.Create("list test 2")

	tasks, err := q.List(10, "")
	if err != nil {
		t.Fatalf("List failed: %v", err)
	}
	if len(tasks) < 2 {
		t.Errorf("Expected at least 2 tasks, got %d", len(tasks))
	}

	// Clean up
	db, _ := q.db()
	for _, t := range tasks {
		db.Exec("DELETE FROM tasks WHERE id=?", t.ID)
	}
	db.Close()
}

func TestListByStatus(t *testing.T) {
	q := New("/Users/azfar.naufal/Documents/mycelium", "http://127.0.0.1:8443/v1")

	id, _ := q.Create("status filter test")
	db, _ := q.db()
	db.Exec("UPDATE tasks SET status='done' WHERE id=?", id)

	tasks, err := q.List(10, "done")
	if err != nil {
		t.Fatalf("List by status failed: %v", err)
	}
	found := false
	for _, t := range tasks {
		if t.ID == id {
			found = true
			break
		}
	}
	if !found {
		t.Error("Expected to find the done task in filtered list")
	}

	db.Exec("DELETE FROM tasks WHERE id=?", id)
	db.Close()
}
