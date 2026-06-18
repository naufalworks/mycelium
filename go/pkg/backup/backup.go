// Package backup handles mycelium data backup and restore.
// Creates portable tar.gz archives of the entire brain state.
package backup

import (
	"archive/tar"
	"compress/gzip"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"time"
)

// DefaultBackupDir is the default backup location.
var DefaultBackupDir string

func init() {
	home, _ := os.UserHomeDir()
	DefaultBackupDir = filepath.Join(home, ".hermes", "myceliumd", "backups")
}

// BackupPaths lists all data files/dirs to include in a backup.
var BackupPaths = []string{
	"log.jsonl",
	"index.db",
	"index.db-wal",
	"index.db-shm",
	"l1",
	"l2",
	"evolution",
	"garden",
	"dicts",
	"snapshots",
	"branch",
}

// Create creates a full backup of the mycelium brain at the given root.
// Returns the path to the backup archive.
func Create(root string, outputDir string) (string, error) {
	if outputDir == "" {
		outputDir = DefaultBackupDir
	}
	os.MkdirAll(outputDir, 0755)

	timestamp := time.Now().UTC().Format("20060102-150405")
	filename := fmt.Sprintf("mycelium-backup-%s.tar.gz", timestamp)
	outputPath := filepath.Join(outputDir, filename)

	f, err := os.Create(outputPath)
	if err != nil {
		return "", fmt.Errorf("backup: create %s: %w", outputPath, err)
	}
	defer f.Close()

	gw := gzip.NewWriter(f)
	defer gw.Close()

	tw := tar.NewWriter(gw)
	defer tw.Close()

	added := 0
	for _, relPath := range BackupPaths {
		fullPath := filepath.Join(root, relPath)
		if err := addToArchive(tw, fullPath, relPath); err != nil {
			// Skip missing files silently
			continue
		}
		added++
	}

	if added == 0 {
		return "", fmt.Errorf("backup: no data found at %s", root)
	}

	return outputPath, nil
}

// Restore restores a mycelium backup from a tar.gz file into the given root directory.
func Restore(archivePath string, root string) error {
	f, err := os.Open(archivePath)
	if err != nil {
		return fmt.Errorf("restore: open %s: %w", archivePath, err)
	}
	defer f.Close()

	gr, err := gzip.NewReader(f)
	if err != nil {
		return fmt.Errorf("restore: gzip: %w", err)
	}
	defer gr.Close()

	tr := tar.NewReader(gr)
	restored := 0

	for {
		header, err := tr.Next()
		if err == io.EOF {
			break
		}
		if err != nil {
			return fmt.Errorf("restore: read tar: %w", err)
		}

		// Safety: prevent path traversal
		target := filepath.Join(root, filepath.Clean(header.Name))
		if !strings.HasPrefix(target, root) {
			continue
		}

		switch header.Typeflag {
		case tar.TypeDir:
			os.MkdirAll(target, os.FileMode(header.Mode))
		case tar.TypeReg:
			os.MkdirAll(filepath.Dir(target), 0755)
			out, err := os.Create(target)
			if err != nil {
				return fmt.Errorf("restore: create %s: %w", target, err)
			}
			if _, err := io.Copy(out, tr); err != nil {
				out.Close()
				return fmt.Errorf("restore: write %s: %w", target, err)
			}
			out.Close()
			os.Chmod(target, os.FileMode(header.Mode))
			restored++
		}
	}

	if restored == 0 {
		return fmt.Errorf("restore: no files found in archive %s", archivePath)
	}
	return nil
}

// addToArchive recursively adds a file or directory to the tar archive.
func addToArchive(tw *tar.Writer, fullPath, relPath string) error {
	info, err := os.Stat(fullPath)
	if err != nil {
		return err
	}

	if !info.IsDir() {
		return addFileToArchive(tw, fullPath, relPath, info)
	}

	// Directory: walk recursively
	entries, err := os.ReadDir(fullPath)
	if err != nil {
		return err
	}

	// Add directory entry
	header, err := tar.FileInfoHeader(info, "")
	if err != nil {
		return err
	}
	header.Name = relPath + "/"
	tw.WriteHeader(header)

	for _, entry := range entries {
		childFull := filepath.Join(fullPath, entry.Name())
		childRel := filepath.Join(relPath, entry.Name())
		if err := addToArchive(tw, childFull, childRel); err != nil {
			continue
		}
	}
	return nil
}

func addFileToArchive(tw *tar.Writer, fullPath, relPath string, info os.FileInfo) error {
	header, err := tar.FileInfoHeader(info, "")
	if err != nil {
		return err
	}
	header.Name = relPath

	f, err := os.Open(fullPath)
	if err != nil {
		return err
	}
	defer f.Close()

	if err := tw.WriteHeader(header); err != nil {
		return err
	}
	_, err = io.Copy(tw, f)
	return err
}

// ListBackups lists all backup archives in the given directory.
func ListBackups(dir string) ([]string, error) {
	if dir == "" {
		dir = DefaultBackupDir
	}
	entries, err := os.ReadDir(dir)
	if err != nil {
		if os.IsNotExist(err) {
			return nil, nil
		}
		return nil, err
	}

	var backups []string
	for _, e := range entries {
		if strings.HasPrefix(e.Name(), "mycelium-backup-") && strings.HasSuffix(e.Name(), ".tar.gz") {
			backups = append(backups, filepath.Join(dir, e.Name()))
		}
	}
	return backups, nil
}
