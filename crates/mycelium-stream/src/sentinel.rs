use crate::state::Alert;

/// Atom concept data extracted from the JSON returned by the brain API.
///
/// The backend serializes atoms with a `phrase` field (the concept's name) and a
/// `ref_count` field (how many entries reference it). This is the minimal
/// projection the sentinel needs to do its three checks.
#[derive(Clone, Debug)]
pub struct AtomInfo {
    pub name: String,
    pub ref_count: i64,
}

impl AtomInfo {
    /// Build an `AtomInfo` from a raw `serde_json::Value` as returned by
    /// `/api/brain/atoms`. Returns `None` if the value has no `phrase`.
    pub fn from_json(v: &serde_json::Value) -> Option<Self> {
        let name = v.get("phrase").and_then(|s| s.as_str())?.to_string();
        let ref_count = v.get("ref_count").and_then(|c| c.as_i64()).unwrap_or(0);
        Some(AtomInfo { name, ref_count })
    }
}

/// Client-side sentinel that runs three lightweight detectors over the live
/// stream of entries delivered via SSE:
///
/// 1. **Pattern repeat** — flag when the same key words keep reappearing.
/// 2. **Contradiction** — flag when an entry mentions atom phrases that look
///    related but disagree.
/// 3. **Merge candidates** — flag when two known atoms differ only by
///    casing/separators and together are referenced enough to be the same
///    concept.
///
/// The sentinel keeps a small rolling buffer of the most recent entry texts so
/// detectors have something to compare against. Nothing about the sentinel is
/// async — it is called from the SSE `onmessage` closure on the WASM main
/// thread.
pub struct Sentinel {
    /// Most recent entry texts, oldest at index 0.
    recent_entries: Vec<String>,
    /// Maximum number of recent entries to retain.
    buffer_size: usize,
    /// Minimum number of recent entries that must share a key phrase for a
    /// pattern alert to fire.
    pattern_threshold: usize,
}

impl Sentinel {
    pub fn new() -> Self {
        Self {
            recent_entries: Vec::new(),
            buffer_size: 50,
            pattern_threshold: 3,
        }
    }

    /// Run all three detectors against `entry_text` using `atoms` as known
    /// context. Returns 0..=3 alerts. The new entry is pushed into the rolling
    /// buffer before the detectors run so that they see the current entry.
    pub fn analyze(&mut self, entry_text: &str, atoms: &[AtomInfo]) -> Vec<Alert> {
        self.recent_entries.push(entry_text.to_string());
        if self.recent_entries.len() > self.buffer_size {
            self.recent_entries.remove(0);
        }

        let mut alerts = Vec::new();

        // Pattern repeat
        if let Some(pattern) = self.detect_pattern_repeat(entry_text) {
            alerts.push(Alert {
                kind: "pattern".to_string(),
                message: pattern,
                node_id: None,
            });
        }

        // Contradiction
        if let Some(contra) = self.detect_contradiction(entry_text, atoms) {
            alerts.push(Alert {
                kind: "contradiction".to_string(),
                message: contra,
                node_id: None,
            });
        }

        // Merge candidates
        if let Some(merge) = self.detect_merge_candidates(atoms) {
            alerts.push(Alert {
                kind: "merge".to_string(),
                message: merge,
                node_id: None,
            });
        }

        alerts
    }

    // ─────────────────────────────────────────────────────────────────────
    // Detector 1: Pattern repeat
    // ─────────────────────────────────────────────────────────────────────

    /// Lowercased, deduplicated key words from an entry. Filters out very
    /// short words (<= 2 chars) because those tend to be noise ("the",
    /// "is", "a").
    fn extract_key_phrases(&self, entry: &str) -> Vec<String> {
        let mut words: Vec<String> = entry
            .to_lowercase()
            .split(|c: char| !c.is_alphanumeric())
            .filter(|s| !s.is_empty() && s.len() > 2)
            .map(|s| s.to_string())
            .collect();
        words.sort();
        words.dedup();
        words
    }

    /// If any key phrase from the current entry appears in >= `pattern_threshold`
    /// of the last 20 entries (including the current one), surface a pattern
    /// alert naming the strongest-matching phrase.
    fn detect_pattern_repeat(&self, entry: &str) -> Option<String> {
        let phrases = self.extract_key_phrases(entry);
        if phrases.is_empty() {
            return None;
        }

        let lookback = self.recent_entries.len().min(20);
        if lookback < self.pattern_threshold {
            return None;
        }
        let recent = &self.recent_entries[self.recent_entries.len() - lookback..];

        let mut best_count = 0usize;
        let mut best_phrase = String::new();

        for phrase in &phrases {
            let count = recent
                .iter()
                .filter(|prev| prev.to_lowercase().contains(phrase.as_str()))
                .count();
            if count >= self.pattern_threshold && count > best_count {
                best_count = count;
                best_phrase = phrase.clone();
            }
        }

        if best_count >= self.pattern_threshold {
            Some(format!(
                "pattern — \"{}\" {}th time this period",
                best_phrase, best_count
            ))
        } else {
            None
        }
    }

    // ─────────────────────────────────────────────────────────────────────
    // Detector 2: Contradiction
    // ─────────────────────────────────────────────────────────────────────

    /// v1 contradiction detector: when an entry mentions two atom phrases that
    /// look related (one is a substring of the other) but have different
    /// suffixes, surface a contradiction. This is intentionally simple — it
    /// catches cases like an entry mentioning both `jwt` and `jwt_secret` and
    /// making different claims about each.
    fn detect_contradiction(&self, entry: &str, atoms: &[AtomInfo]) -> Option<String> {
        if atoms.len() < 2 {
            return None;
        }

        let entry_lower = entry.to_lowercase();
        let names: Vec<&str> = atoms.iter().map(|a| a.name.as_str()).collect();

        for i in 0..names.len() {
            for j in i + 1..names.len() {
                let a = names[i];
                let b = names[j];
                let (shorter, longer) = if a.len() <= b.len() { (a, b) } else { (b, a) };

                // One name must be a strict substring of the other, not equal,
                // and of meaningful length.
                if longer.len() <= shorter.len() + 2 {
                    continue;
                }
                if !longer.to_lowercase().contains(&shorter.to_lowercase()) {
                    continue;
                }
                if shorter.len() < 3 {
                    continue;
                }

                if entry_lower.contains(&a.to_lowercase())
                    && entry_lower.contains(&b.to_lowercase())
                {
                    return Some(format!("contradiction — {} vs {} disagree", a, b));
                }
            }
        }
        None
    }

    // ─────────────────────────────────────────────────────────────────────
    // Detector 3: Merge candidates
    // ─────────────────────────────────────────────────────────────────────

    /// Find pairs of atoms whose names differ only by casing or token
    /// separators (underscore, hyphen). If their combined `ref_count > 5`,
    /// they're likely the same concept and should be merged.
    fn detect_merge_candidates(&self, atoms: &[AtomInfo]) -> Option<String> {
        if atoms.len() < 2 {
            return None;
        }

        let normalize = |s: &str| -> String {
            s.chars()
                .filter(|c| c.is_alphanumeric())
                .flat_map(|c| c.to_lowercase())
                .collect()
        };

        for i in 0..atoms.len() {
            for j in i + 1..atoms.len() {
                let a = &atoms[i];
                let b = &atoms[j];

                if a.name == b.name {
                    continue;
                }

                let a_norm = normalize(&a.name);
                let b_norm = normalize(&b.name);
                if a_norm.is_empty() || a_norm != b_norm {
                    continue;
                }

                if a.ref_count + b.ref_count > 5 {
                    return Some(format!(
                        "merge — {} / {} → 1 concept",
                        a.name, b.name
                    ));
                }
            }
        }
        None
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn atom(name: &str, ref_count: i64) -> AtomInfo {
        AtomInfo {
            name: name.to_string(),
            ref_count,
        }
    }

    #[test]
    fn pattern_repeat_fires_when_phrase_repeats() {
        let mut s = Sentinel::new();
        s.analyze("alpha beta", &[]);
        s.analyze("alpha gamma", &[]);
        let alerts = s.analyze("alpha delta", &[]);
        assert!(alerts.iter().any(|a| a.kind == "pattern" && a.message.contains("alpha")));
    }

    #[test]
    fn pattern_repeat_does_not_fire_when_phrase_is_fresh() {
        let mut s = Sentinel::new();
        s.analyze("hello", &[]);
        s.analyze("world", &[]);
        let alerts = s.analyze("banana split", &[]);
        assert!(!alerts.iter().any(|a| a.kind == "pattern"));
    }

    #[test]
    fn merge_candidates_fires_on_casing_difference() {
        let mut s = Sentinel::new();
        let atoms = vec![atom("jwt", 3), atom("JWT", 4)];
        let alerts = s.analyze("anything here", &atoms);
        assert!(alerts.iter().any(|a| a.kind == "merge" && a.message.contains("jwt / JWT")));
    }

    #[test]
    fn merge_candidates_fires_on_separator_difference() {
        let mut s = Sentinel::new();
        let atoms = vec![atom("jwttoken", 3), atom("jwt_token", 4)];
        let alerts = s.analyze("anything here", &atoms);
        assert!(alerts.iter().any(|a| a.kind == "merge"));
    }

    #[test]
    fn merge_candidates_skipped_when_ref_count_too_low() {
        let mut s = Sentinel::new();
        let atoms = vec![atom("jwt", 1), atom("JWT", 2)];
        let alerts = s.analyze("anything here", &atoms);
        assert!(!alerts.iter().any(|a| a.kind == "merge"));
    }

    #[test]
    fn contradiction_fires_on_substring_pair() {
        let mut s = Sentinel::new();
        let atoms = vec![atom("jwt", 5), atom("jwt_secret", 5)];
        let alerts = s.analyze("we use jwt but jwt_secret is bad", &atoms);
        assert!(alerts.iter().any(|a| a.kind == "contradiction"));
    }
}
