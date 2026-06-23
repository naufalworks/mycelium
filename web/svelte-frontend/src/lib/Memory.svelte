<script lang="ts">
	import { onMount } from 'svelte';

	const LIMIT = 20;

	let entries = $state<any[]>([]);
	let total = $state(0);
	let page = $state(0);
	let query = $state('');
	let loading = $state(false);

	let totalPages = $derived(Math.ceil(total / LIMIT));

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
			<button onclick={search} disabled={loading}>
				{loading ? '...' : 'Search'}
			</button>
		</div>
	</div>

	{#if loading && entries.length === 0}
		<div class="loading">Loading...</div>
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
				{#each Array(Math.min(totalPages, 20)) as _, i}
					<button
						class="page-num"
						class:active={i === page}
						onclick={() => goTo(i)}
					>{i + 1}</button>
				{/each}
				{#if totalPages > 20}
					<span class="page-ellipsis">…</span>
				{/if}
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
		<p class="empty">No entries found.</p>
	{/if}
</div>

<style>
	.view {
		padding: 24px;
	}

	.header {
		display: flex;
		align-items: center;
		gap: 16px;
		margin-bottom: 20px;
		flex-wrap: wrap;
	}

	h2 {
		font-size: 24px;
		font-weight: 700;
		margin: 0;
		white-space: nowrap;
	}

	.search-bar {
		display: flex;
		gap: 8px;
		flex: 1;
		min-width: 200px;
	}

	.search-bar input {
		flex: 1;
		padding: 10px 14px;
		border: 1px solid rgba(255,255,255,0.12);
		border-radius: 8px;
		background: rgba(255,255,255,0.06);
		color: #f0f4f8;
		font-size: 14px;
	}

	.search-bar input:focus {
		outline: none;
		border-color: rgba(255,255,255,0.25);
	}

	.search-bar button {
		padding: 10px 20px;
		border: 1px solid rgba(255,255,255,0.12);
		border-radius: 8px;
		background: rgba(255,255,255,0.08);
		color: #f0f4f8;
		cursor: pointer;
		font-size: 14px;
	}

	.search-bar button:disabled {
		opacity: 0.5;
		cursor: default;
	}

	.loading {
		text-align: center;
		padding: 40px;
		color: #94a3b8;
	}

	.entry-list {
		display: flex;
		flex-direction: column;
		gap: 8px;
	}

	.entry-card {
		background: rgba(255,255,255,0.03);
		border: 1px solid rgba(255,255,255,0.08);
		border-left: 3px solid rgba(255,255,255,0.15);
		border-radius: 8px;
		padding: 14px 18px;
	}

	.entry-header {
		display: flex;
		gap: 12px;
		margin-bottom: 6px;
		font-size: 12px;
	}

	.turn { color: #60a5fa; font-weight: 600; }
	.session { color: #94a3b8; }
	.tier { color: #a78bfa; }
	.type { color: #34d399; }

	.entry-preview {
		font-size: 13px;
		line-height: 1.5;
	}

	.user-text { color: #94a3b8; margin-bottom: 2px; }
	.ai-text { color: #f0f4f8; }

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
		gap: 2px;
		align-items: center;
		flex-wrap: wrap;
		justify-content: center;
	}

	.page-num, .page-btn {
		padding: 6px 12px;
		border: 1px solid rgba(255,255,255,0.10);
		border-radius: 8px;
		background: transparent;
		color: #94a3b8;
		font-size: 12px;
		font-weight: 500;
		cursor: pointer;
		min-width: 32px;
		text-align: center;
	}

	.page-num:hover, .page-btn:hover {
		background: rgba(255,255,255,0.06);
		color: #f0f4f8;
	}

	.page-num.active {
		background: linear-gradient(135deg, #6366f1, #8b5cf6);
		color: #fff;
		border-color: transparent;
	}

	.page-num:disabled, .page-btn:disabled {
		opacity: 0.4;
		cursor: default;
		pointer-events: none;
	}

	.page-ellipsis {
		color: #94a3b8;
		padding: 0 4px;
		font-size: 13px;
	}

	.page-info {
		text-align: center;
		color: #94a3b8;
		font-size: 11px;
		margin-top: 10px;
	}

	.page-loading {
		color: #6366f1;
	}

	.empty {
		text-align: center;
		color: #94a3b8;
		padding: 40px;
		font-size: 14px;
	}
</style>
