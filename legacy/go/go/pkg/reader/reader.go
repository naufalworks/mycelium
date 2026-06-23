// Package reader provides a content extraction engine.
// Fetches a URL, strips JS/CSS/trackers/nav, returns clean text.
// All Go, no browser engine, zero external HTTP calls.
package reader

import (
	"io"
	"net/http"
	"strings"
	"time"
	"unicode"

	"golang.org/x/net/html"
)

const (
	clientTimeout = 15 * time.Second
	maxBodySize   = 5 << 20 // 5MB
)

// Result holds the extracted content from a URL.
type Result struct {
	URL       string `json:"url"`
	Title     string `json:"title"`
	Content   string `json:"content"`   // clean markdown-like text
	TextOnly  string `json:"text_only"` // plain text, no formatting
	WordCount int    `json:"word_count"`
}

// Fetch retrieves a URL and extracts clean content.
func Fetch(url string) (*Result, error) {
	client := &http.Client{Timeout: clientTimeout}
	resp, err := client.Get(url)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	reader := io.LimitReader(resp.Body, maxBodySize)
	doc, err := html.Parse(reader)
	if err != nil {
		return nil, err
	}

	title := extractTitle(doc)
	content := extractContent(doc)
	textOnly := stripMarkdown(content)

	return &Result{
		URL:       url,
		Title:     title,
		Content:   content,
		TextOnly:  textOnly,
		WordCount: countWords(textOnly),
	}, nil
}

// extractTitle finds the page title.
func extractTitle(n *html.Node) string {
	if n.Type == html.ElementNode && n.Data == "title" && n.FirstChild != nil {
		return strings.TrimSpace(n.FirstChild.Data)
	}
	for c := n.FirstChild; c != nil; c = c.NextSibling {
		if t := extractTitle(c); t != "" {
			return t
		}
	}
	return ""
}

// extractContent walks the DOM and extracts meaningful text.
func extractContent(n *html.Node) string {
	var b strings.Builder
	extractText(n, &b, 0)
	return strings.TrimSpace(b.String())
}

// extractText recursively walks nodes, skipping unwanted elements.
func extractText(n *html.Node, b *strings.Builder, depth int) {
	if n == nil {
		return
	}

	// Skip entirely
	if n.Type == html.ElementNode {
		tag := n.Data
		switch tag {
		case "script", "style", "noscript", "iframe", "svg",
			"nav", "footer", "header", "aside",
			"form", "input", "select", "textarea", "button":
			return
		}
	}

	// Text node
	if n.Type == html.TextNode {
		text := strings.TrimSpace(n.Data)
		if text != "" {
			if b.Len() > 0 {
				b.WriteByte(' ')
			}
			b.WriteString(text)
		}
	}

	// Block elements — add newlines
	if n.Type == html.ElementNode {
		switch n.Data {
		case "p", "div", "h1", "h2", "h3", "h4", "h5", "h6",
			"br", "hr", "li", "blockquote", "pre", "section":
			if b.Len() > 0 && b.String()[b.Len()-1] != '\n' {
				b.WriteByte('\n')
			}
		}
	}

	// Recurse children
	for c := n.FirstChild; c != nil; c = c.NextSibling {
		extractText(c, b, depth+1)
	}

	// Double newline after block elements
	if n.Type == html.ElementNode {
		switch n.Data {
		case "p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote", "pre":
			b.WriteString("\n\n")
		}
	}
}

// stripMarkdown removes any remaining markdown formatting.
func stripMarkdown(s string) string {
	var b strings.Builder
	inCode := false
	for _, r := range s {
		switch {
		case r == '`':
			inCode = !inCode
		case inCode:
			b.WriteRune(r)
		case r == '#' || r == '*' || r == '_' || r == '~' || r == '>':
			// skip formatting chars
		default:
			b.WriteRune(r)
		}
	}
	return strings.TrimSpace(b.String())
}

func countWords(s string) int {
	count := 0
	inWord := false
	for _, r := range s {
		if unicode.IsSpace(r) {
			inWord = false
		} else if !inWord {
			count++
			inWord = true
		}
	}
	return count
}
