//! Full-text search engine powered by Tantivy.
//!
//! Tantivy is a fast full-text search engine library (similar to Lucene).
//! We index entries on write for millisecond search across all memory.

use tantivy::collector::TopDocs;
use tantivy::query::QueryParser;
use tantivy::schema::*;
use tantivy::{doc, Index, IndexWriter, ReloadPolicy};
use std::path::PathBuf;
use tracing::debug;

use crate::error::Result;
use crate::types::Entry;

/// The full-text search index over memory entries.
pub struct SearchIndex {
    index: Index,
    /// Field references
    turn: Field,
    session: Field,
    user: Field,
    assistant: Field,
    entities: Field,
}

impl SearchIndex {
    /// Open or create the search index at the given directory.
    pub fn open(index_dir: PathBuf) -> Result<Self> {
        std::fs::create_dir_all(&index_dir).ok();

        let mut schema_builder = Schema::builder();
        let turn = schema_builder.add_i64_field("turn", STORED | INDEXED);
        let session = schema_builder.add_text_field("session", STRING | STORED);
        let user = schema_builder.add_text_field("user", TEXT | STORED);
        let assistant = schema_builder.add_text_field("assistant", TEXT | STORED);
        let entities = schema_builder.add_text_field("entities", TEXT | STORED);
        let schema = schema_builder.build();

        let index = if index_dir.join("meta.json").exists() {
            Index::open_in_dir(&index_dir)?
        } else {
            Index::create_in_dir(&index_dir, schema.clone())?
        };

        Ok(SearchIndex {
            index,
            turn,
            session,
            user,
            assistant,
            entities,
        })
    }

    /// Index a single entry for full-text search.
    pub fn index_entry(&self, entry: &Entry) -> Result<()> {
        let mut writer: IndexWriter = self.index.writer(50_000_000)?; // 50MB buffer
        writer.add_document(doc!(
            self.turn => entry.turn,
            self.session => entry.session.clone(),
            self.user => entry.user.clone(),
            self.assistant => entry.assistant.clone(),
            self.entities => entry.entities.join(" "),
        ))?;
        writer.commit()?;
        debug!("Indexed entry turn={}", entry.turn);
        Ok(())
    }

    /// Index multiple entries in batch (faster).
    pub fn index_entries(&self, entries: &[Entry]) -> Result<()> {
        let mut writer: IndexWriter = self.index.writer(100_000_000)?; // 100MB buffer
        for entry in entries {
            writer.add_document(doc!(
                self.turn => entry.turn,
                self.session => entry.session.clone(),
                self.user => entry.user.clone(),
                self.assistant => entry.assistant.clone(),
                self.entities => entry.entities.join(" "),
            ))?;
        }
        writer.commit()?;
        debug!("Indexed {} entries", entries.len());
        Ok(())
    }

    /// Search the index for the given query. Returns matching entry turn numbers.
    pub fn search(&self, query_text: &str, limit: usize) -> Result<Vec<i64>> {
        let reader = self
            .index
            .reader_builder()
            .reload_policy(ReloadPolicy::OnCommitWithDelay)
            .try_into()?;

        let searcher = reader.searcher();
        let query_parser = QueryParser::for_index(
            &self.index,
            vec![self.user, self.assistant, self.entities, self.session],
        );

        let query = query_parser.parse_query(query_text)?;
        let top_docs = searcher.search(&query, &TopDocs::with_limit(limit))?;

        let results: Vec<i64> = top_docs
            .into_iter()
            .filter_map(|(_score, doc_address)| {
                let doc = searcher.doc::<TantivyDocument>(doc_address).ok()?;
                doc.get_first(self.turn).and_then(|v| v.as_i64())
            })
            .collect();

        Ok(results)
    }

    /// Get total indexed document count.
    pub fn doc_count(&self) -> Result<u64> {
        let reader = self
            .index
            .reader_builder()
            .reload_policy(ReloadPolicy::OnCommitWithDelay)
            .try_into()?;
        Ok(reader.searcher().num_docs() as u64)
    }
}
