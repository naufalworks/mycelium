<script lang="ts">
	let entries = $state<any[]>([]);
	let query = $state('');
	let loading = $state(false);

	async function search() {
		if (!query.trim()) return;
		loading = true;
		try {
			const res = await fetch(`/api/search?q=${encodeURIComponent(query)}&limit=100`);
			if (!res.ok) throw new Error('Search failed');
			const data = await res.json();
			entries = data.entries ?? [];
		} catch (e: any) {
			console.error(e);
			entries = [];
		} finally {
			loading = false;
		}
	}
</script>

<div class="view">
	<h2>Memory</h2>

	<div class="search-bar">
		<input type="text" placeholder="Search memories..." bind:value={query}
			onkeydown={(e: KeyboardEvent) => e.key === 'Enter' && search()} />
		<button onclick={search} disabled={loading}>{loading ? '...' : 'Search'}</button>
	</div>

	{#if entries.length > 0}
		<div class="entry-list">
			{#each entries as entry}
				<div class="entry-card">
					<div class="entry-header">
						<span class="turn">#{entry.turn}</span>
						<span class="session">{entry.session?.slice(0, 30)}</span>
						<span class="tier">{entry.tier}</span>
						<span class="type">{entry.entry_type}</span>
					</div>
					<p class="entry-user">{entry.user?.slice(0, 300)}</p>
				</div>
			{/each}
		</div>
	{:else if query && !loading}
		<p class="empty">No results.</p>
	{/if}
</div>

<style>
	h2 { font-size: 1.5rem; font-weight: 700; margin: 0 0 1.5rem 0; }
	.search-bar { display: flex; gap: 0.5rem; margin-bottom: 1.5rem; }
	.search-bar input { flex: 1; padding: 0.7rem 1rem; border: 1px solid #2a2a38; border-radius: 8px; background: #121218; color: #e0e0e0; font-size: 0.95rem; outline: none; }
	.search-bar input:focus { border-color: #7c5cfc; }
	.search-bar button { padding: 0.7rem 1.25rem; border: none; border-radius: 8px; background: #7c5cfc; color: #fff; font-weight: 600; cursor: pointer; }
	.search-bar button:disabled { opacity: 0.5; }
	.entry-list { display: flex; flex-direction: column; gap: 0.75rem; }
	.entry-card { background: #121218; border: 1px solid #1e1e28; border-radius: 10px; padding: 1rem; }
	.entry-header { display: flex; gap: 1rem; margin-bottom: 0.5rem; font-size: 0.8rem; }
	.turn { color: #7c5cfc; font-weight: 700; }
	.session { color: #666; }
	.tier { color: #888; }
	.type { color: #4ade80; }
	.entry-user { color: #ccc; font-size: 0.9rem; line-height: 1.5; margin: 0; }
	.empty { color: #666; font-style: italic; }
</style>
