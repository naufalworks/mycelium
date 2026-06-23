<script lang="ts">
	export let status: any;
</script>

<div class="dashboard">
	<h1>Dashboard</h1>

	{#if status}
	<div class="grid">
		<div class="card">
			<div class="card-icon entries">📝</div>
			<div class="card-body">
				<span class="card-label">Total Entries</span>
				<span class="card-value">{status.total_turns?.toLocaleString()}</span>
			</div>
		</div>
		<div class="card">
			<div class="card-icon sessions">👤</div>
			<div class="card-body">
				<span class="card-label">Sessions</span>
				<span class="card-value">{status.total_sessions}</span>
			</div>
		</div>
		<div class="card">
			<div class="card-icon storage">💾</div>
			<div class="card-body">
				<span class="card-label">Database</span>
				<span class="card-value">{(status.storage_bytes / 1024 / 1024).toFixed(1)} MB</span>
			</div>
		</div>
		<div class="card">
			<div class="card-icon turn">🔗</div>
			<div class="card-body">
				<span class="card-label">Last Turn</span>
				<span class="card-value">#{status.last_turn?.turn?.toLocaleString() ?? '-'}</span>
			</div>
		</div>
	</div>

	<div class="section">
		<h2>Distribution</h2>
		<div class="meta-grid">
			<div class="meta-card">
				<h3>Tiers</h3>
				<div class="tag-list">
					{#each Object.entries(status.tiers ?? {}) as [tier, count]}
						<span class="tag">{tier}: {count}</span>
					{/each}
				</div>
			</div>
			<div class="meta-card">
				<h3>Types</h3>
				<div class="tag-list">
					{#each Object.entries(status.types ?? {}) as [type, count]}
						<span class="tag">{type}: {count}</span>
					{/each}
				</div>
			</div>
		</div>
	</div>
	{/if}
</div>

<style>
	.dashboard { max-width: 900px; }
	h1 { font-size: 1.5rem; font-weight: 700; margin: 0 0 1.5rem 0; color: #f0f0f0; }

	.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 1rem; margin-bottom: 2rem; }
	.card { background: #14141e; border: 1px solid #1e1e2a; border-radius: 12px; padding: 1.25rem; display: flex; align-items: center; gap: 1rem; }
	.card-icon { font-size: 1.5rem; width: 44px; height: 44px; border-radius: 10px; display: flex; align-items: center; justify-content: center; }
	.card-icon.entries { background: #7c5cfc20; }
	.card-icon.sessions { background: #4ade8020; }
	.card-icon.storage { background: #f59e0b20; }
	.card-icon.turn { background: #60a5fa20; }
	.card-body { display: flex; flex-direction: column; }
	.card-label { font-size: 0.8rem; color: #888; text-transform: uppercase; letter-spacing: 0.04em; }
	.card-value { font-size: 1.5rem; font-weight: 700; color: #f0f0f0; margin-top: 0.2rem; }

	.section { margin-bottom: 2rem; }
	.section h2 { font-size: 1rem; font-weight: 600; color: #aaa; margin-bottom: 1rem; }
	.meta-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; }
	.meta-card { background: #14141e; border: 1px solid #1e1e2a; border-radius: 12px; padding: 1.25rem; }
	h3 { font-size: 0.8rem; font-weight: 600; color: #888; text-transform: uppercase; letter-spacing: 0.04em; margin: 0 0 0.75rem 0; }
	.tag-list { display: flex; flex-wrap: wrap; gap: 0.4rem; }
	.tag { background: #1a1a26; border: 1px solid #2a2a3a; border-radius: 6px; padding: 0.3rem 0.6rem; font-size: 0.8rem; color: #ccc; }
</style>
