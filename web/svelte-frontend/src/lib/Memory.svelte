<script lang="ts">
	import { onMount } from 'svelte';

	const LIMIT = 20;

	let entries = $state<any[]>([]);
	let total = $state(0);
	let page = $state(0);
	let query = $state('');
	let loading = $state(false);

	let totalPages = $derived(Math.ceil(total / LIMIT));

	function getPageNumbers(): (number | 'ellipsis')[] {
		const maxVisible = 20;
		if (totalPages <= maxVisible) {
			return Array.from({ length: totalPages }, (_, i) => i);
		}
		const pages: (number | 'ellipsis')[] = [0];
		if (page <= 8) {
			for (let i = 1; i < maxVisible; i++) pages.push(i);
			pages.push('ellipsis');
			pages.push(totalPages - 1);
		} else if (page >= totalPages - 9) {
			pages.push('ellipsis');
			for (let i = totalPages - maxVisible; i < totalPages; i++) pages.push(i);
		} else {
			pages.push('ellipsis');
			const half = Math.floor((maxVisible - 4) / 2);
			for (let i = page - half; i <= page + half; i++) pages.push(i);
			pages.push('ellipsis');
			pages.push(totalPages - 1);
		}
		return pages;
	}

	async function loadPage(p: number, q: string | null = null) {
		loading = true;
		try {
			const offset = p * LIMIT;
			if (q) {
				const res = await fetch(`/api/search?q=${encodeURIComponent(q)}&limit=${LIMIT}&offset=${offset}`);
				if (!res.ok) throw new Error('Search failed');
				const data = await res.json();
				entries = data.entries ?? [];
				total = data.total ?? entries.length;
			} else {
				const res = await fetch(`/api/entries?limit=${LIMIT}&offset=${offset}`);
				if (!res.ok) throw new Error('Failed to load');
				const data = await res.json();
				entries = data.entries ?? [];
				total = data.total ?? entries.length;
			}
			page = p;
		} catch (e) {
			console.error(e);
			entries = [];
		} finally {
			loading = false;
		}
	}

	function search() {
		if (!query.trim()) {
			loadPage(0);
			return;
		}
		loadPage(0, query.trim());
	}

	function goTo(p: number) {
		if (p < 0 || (totalPages > 0 && p >= totalPages)) return;
		if (query.trim()) {
			loadPage(p, query.trim());
		} else {
			loadPage(p);
		}
	}

	onMount(() => loadPage(0));
</script>

<div class="view">
	<div class="header">
		<h2>🧠 Memory</h2>
		<div class="search-bar">
			<input
				type="text"
				placeholder="Search memories..."
				bind:value={query}
				onkeydown={(e: KeyboardEvent) => e.key === 'Enter' && search()}
			/>
			<button onclick={search} disabled={loading}>🔍 Search</button>
		</div>
	</div>

	{#if loading && entries.length === 0}
		<div class="loading"><span class="spinner"></span> Loading memory...</div>
	{/if}

	{#if entries.length > 0}
		<div class="entry-list">
			{#each entries as entry}
				<div class="entry-card">
					<div class="entry-header">
						<span class="turn">#{entry.turn}</span>
						<span class="session">{entry.session?.slice(0, 30)}</span>
						<span class="tier">{entry.tier}</span>
						<span class="type">{entry.entry_type ?? entry.type ?? ''}</span>
						<span class="ts">{entry.ts ? new Date(entry.ts).toLocaleString() : ''}</span>
					</div>
					<div class="entry-preview">
						<div class="user-text">👤 {(entry.user ?? '').slice(0, 200)}</div>
						<div class="ai-text">🤖 {(entry.assistant ?? '').slice(0, 200)}</div>
					</div>
				</div>
			{/each}
		</div>

		<div class="pagination-bar">
			<button class="page-btn" disabled={page === 0} onclick={() => goTo(page - 1)}>‹ Prev</button>
			<div class="page-numbers">
				{#each getPageNumbers() as p, i}
					{#if p === 'ellipsis'}
						<span class="page-ellipsis">…</span>
					{:else}
						<button
							class="page-num"
							class:active={p === page}
							onclick={() => goTo(p)}
						>{p + 1}</button>
					{/if}
				{/each}
			</div>
			<button class="page-btn" disabled={page >= totalPages - 1} onclick={() => goTo(page + 1)}>Next ›</button>
		</div>

		<div class="page-info">
			Page {page + 1} of {totalPages || 1} · {total} entries
			{#if loading}
				<span class="page-loading"> loading…</span>
			{/if}
		</div>
	{:else if !loading}
		<div class="empty-state">
			<div class="empty-icon">⟐</div>
			<p>No entries yet. Talk to Claude Code to build memory.</p>
		</div>
	{/if}
</div>

<style>
	.view { padding: 0; }

	.header {
		display: flex;
		align-items: center;
		gap: 16px;
		margin-bottom: 20px;
		flex-wrap: wrap;
	}

	h2 {
		font-family: 'Space Grotesk', sans-serif;
		font-size: 1.5rem;
		font-weight: 700;
		margin: 0;
		white-space: nowrap;
		color: #EDE6DD;
	}

	.search-bar {
		display: flex;
		gap: 8px;
		flex: 1;
		min-width: 200px;
	}

	.search-bar input {
		flex: 1;
		padding: 8px 14px;
		border: 1px solid #322D26;
		border-radius: 6px;
		background: #181512;
		color: #EDE6DD;
		font-size: 14px;
		font-family: 'Inter', sans-serif;
		transition: all 150ms;
	}
	.search-bar input:focus {
		outline: none;
		border-color: #6EE7B7;
		box-shadow: 0 0 0 3px rgba(110,231,183,0.1);
	}
	.search-bar input::placeholder { color: #7A736A; }

	.search-bar button {
		padding: 8px 18px;
		border: 1px solid #322D26;
		border-radius: 6px;
		background: #181512;
		color: #B0A89E;
		cursor: pointer;
		font-size: 14px;
		font-family: inherit;
		transition: all 150ms;
	}
	.search-bar button:hover {
		background: #201D19;
		color: #EDE6DD;
		border-color: #403A31;
	}
	.search-bar button:disabled {
		opacity: 0.4;
		cursor: default;
		pointer-events: none;
	}

	.loading {
		display: flex;
		align-items: center;
		justify-content: center;
		gap: 10px;
		padding: 48px;
		color: #7A736A;
		font-size: 14px;
	}

	.spinner {
		width: 16px;
		height: 16px;
		border: 2px solid #322D26;
		border-top-color: #6EE7B7;
		border-radius: 50%;
		animation: spin 0.7s linear infinite;
		flex-shrink: 0;
	}

	.entry-list {
		display: flex;
		flex-direction: column;
		gap: 8px;
	}

	.entry-card {
		background: #181512;
		border: 1px solid #322D26;
		border-radius: 10px;
		padding: 14px 16px;
		transition: all 200ms;
	}
	.entry-card:hover { border-color: #403A31; }

	.entry-header {
		display: flex;
		align-items: center;
		gap: 10px;
		margin-bottom: 8px;
		font-size: 12px;
		flex-wrap: wrap;
	}

	.turn { color: #60A5FA; font-weight: 600; font-family: 'JetBrains Mono', monospace; }
	.session { color: #7A736A; }
	.tier { color: #6EE7B7; font-family: 'JetBrains Mono', monospace; }
	.type { color: #A78BFA; }
	.ts { color: #7A736A; font-size: 11px; margin-left: auto; }

	.entry-preview {
		font-size: 13px;
		line-height: 1.5;
	}

	.user-text { color: #B0A89E; margin-bottom: 2px; }
	.ai-text { color: #EDE6DD; }

	.pagination-bar {
		display: flex;
		align-items: center;
		justify-content: center;
		gap: 4px;
		margin-top: 20px;
		flex-wrap: wrap;
	}

	.page-numbers {
		display: flex;
		gap: 3px;
		align-items: center;
		flex-wrap: wrap;
		justify-content: center;
	}

	.page-num, .page-btn {
		padding: 5px 10px;
		border: 1px solid #322D26;
		border-radius: 6px;
		background: transparent;
		color: #B0A89E;
		font-size: 12px;
		font-weight: 500;
		font-family: inherit;
		cursor: pointer;
		min-width: 30px;
		text-align: center;
		transition: all 150ms;
	}

	.page-num:hover, .page-btn:hover {
		background: #201D19;
		color: #EDE6DD;
		border-color: #403A31;
	}

	.page-num.active {
		background: #6EE7B7;
		color: #0E0C0A;
		border-color: #6EE7B7;
		font-weight: 600;
	}

	.page-num:disabled, .page-btn:disabled {
		opacity: 0.35;
		cursor: default;
		pointer-events: none;
	}

	.page-ellipsis {
		color: #7A736A;
		padding: 4px 2px;
		font-size: 13px;
		letter-spacing: 2px;
		user-select: none;
	}

	.page-info {
		text-align: center;
		color: #7A736A;
		font-size: 11px;
		margin-top: 10px;
	}

	.page-loading {
		color: #6EE7B7;
		animation: pulse 1.2s ease-in-out infinite;
	}

	.empty-state {
		text-align: center;
		padding: 48px 24px;
		color: #7A736A;
	}

	.empty-icon {
		font-size: 2.5rem;
		margin-bottom: 12px;
		opacity: 0.4;
	}

	.empty-state p {
		font-size: 14px;
		line-height: 1.6;
		max-width: 360px;
		margin: 0 auto;
	}

	@keyframes spin {
		to { transform: rotate(360deg); }
	}

	@keyframes pulse {
		0%, 100% { opacity: 1; }
		50% { opacity: 0.4; }
	}
</style>
