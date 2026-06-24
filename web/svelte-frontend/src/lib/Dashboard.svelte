<script lang="ts">
	import { onMount, onDestroy } from 'svelte';

	export let status: any;
	let interval: ReturnType<typeof setInterval>;

	onMount(() => {
		interval = setInterval(async () => {
			try {
				const res = await fetch('/api/status');
				if (res.ok) status = await res.json();
			} catch (_) {}
		}, 5000);
	});

	onDestroy(() => clearInterval(interval));
</script>

<div class="dashboard">
	<h1>
		<span class="title-icon">📡</span>
		Dashboard
		{#if status}
			<span class="live-badge">● LIVE</span>
		{/if}
	</h1>

	{#if status}
		<div class="stats-grid">
			<div class="stat-card">
				<div class="stat-icon teal">⟐</div>
				<div class="stat-body">
					<span class="stat-label">Total Turns</span>
					<span class="stat-value">{status.total_turns}</span>
				</div>
			</div>
			<div class="stat-card">
				<div class="stat-icon amber">💾</div>
				<div class="stat-body">
					<span class="stat-label">Storage</span>
					<span class="stat-value">{(status.storage_bytes / 1024 / 1024).toFixed(1)} MB</span>
				</div>
			</div>
		</div>

		<div class="section">
			<h2 class="section-title">Distribution</h2>
			<div class="meta-grid">
				<div class="meta-card">
					<h3>Tiers</h3>
					<div class="tag-list">
						{#each Object.entries(status.tiers ?? {}) as [tier, count]}
							<span class="tag tier-tag">{tier}: {count}</span>
						{/each}
					</div>
				</div>
				<div class="meta-card">
					<h3>Types</h3>
					<div class="tag-list">
						{#each Object.entries(status.types ?? {}) as [type, count]}
							<span class="tag type-tag">{type}: {count}</span>
						{/each}
					</div>
				</div>
			</div>
		</div>
	{/if}
</div>

<style>
	.dashboard { max-width: 900px; }

	h1 {
		font-family: 'Space Grotesk', sans-serif;
		font-size: 1.5rem;
		font-weight: 700;
		margin: 0 0 1.5rem 0;
		color: #EDE6DD;
		display: flex;
		align-items: center;
		gap: 8px;
	}
	.title-icon { font-size: 1.1rem; }
	.live-badge {
		font-size: 0.625rem;
		font-weight: 600;
		letter-spacing: 0.04em;
		background: rgba(110,231,183,0.12);
		color: #6EE7B7;
		border: 1px solid rgba(110,231,183,0.25);
		border-radius: 6px;
		padding: 2px 8px;
		text-transform: uppercase;
	}

	.stats-grid {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
		gap: 12px;
		margin-bottom: 2rem;
	}

	.stat-card {
		background: #181512;
		border: 1px solid #322D26;
		border-radius: 10px;
		padding: 16px;
		display: flex;
		align-items: center;
		gap: 14px;
		transition: all 200ms;
	}
	.stat-card:hover {
		border-color: #403A31;
		transform: translateY(-1px);
	}

	.stat-icon {
		width: 40px;
		height: 40px;
		border-radius: 10px;
		display: flex;
		align-items: center;
		justify-content: center;
		font-size: 1.25rem;
		flex-shrink: 0;
	}
	.stat-icon.teal { background: rgba(110,231,183,0.1); color: #6EE7B7; }
	.stat-icon.amber { background: rgba(251,191,36,0.1); color: #FBBF24; }

	.stat-body { display: flex; flex-direction: column; min-width: 0; }
	.stat-label {
		font-size: 0.75rem;
		color: #7A736A;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		font-weight: 600;
	}
	.stat-value {
		font-family: 'Space Grotesk', sans-serif;
		font-size: 1.25rem;
		font-weight: 700;
		color: #EDE6DD;
		margin-top: 2px;
	}

	.section { margin-bottom: 2rem; }
	.section-title {
		font-size: 0.8125rem;
		font-weight: 600;
		color: #7A736A;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		margin-bottom: 12px;
	}
	.meta-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
	.meta-card {
		background: #181512;
		border: 1px solid #322D26;
		border-radius: 10px;
		padding: 16px;
	}
	h3 {
		font-size: 0.6875rem;
		font-weight: 600;
		color: #7A736A;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		margin: 0 0 8px 0;
	}
	.tag-list { display: flex; flex-wrap: wrap; gap: 4px; }
	.tag {
		background: #1F1C18;
		border: 1px solid #322D26;
		border-radius: 5px;
		padding: 3px 8px;
		font-size: 0.75rem;
		color: #B0A89E;
	}
	.tier-tag { color: #6EE7B7; }
	.type-tag { color: #A78BFA; }
</style>
