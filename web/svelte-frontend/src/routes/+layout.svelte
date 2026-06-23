<script lang="ts">
	import { onMount } from 'svelte';

	let status = $state<any>(null);
	let error = $state<string | null>(null);
	let view = $state('dashboard');

	onMount(async () => {
		try {
			const res = await fetch('http://127.0.0.1:8421/api/status');
			if (!res.ok) throw new Error(`HTTP ${res.status}`);
			status = await res.json();
		} catch (e: any) {
			error = e.message;
		}
	});
</script>

<div class="app-shell">
	<nav class="sidebar">
		<div class="sidebar-header">
			<h1 class="logo">🍄 mycelium</h1>
		</div>
		<ul class="nav-list">
			<li>
				<button class="nav-item" class:active={view === 'dashboard'} onclick={() => view = 'dashboard'}>
					<span class="icon">📊</span> Dashboard
				</button>
			</li>
			<li>
				<button class="nav-item" class:active={view === 'memory'} onclick={() => view = 'memory'}>
					<span class="icon">🧠</span> Memory
				</button>
			</li>
			<li>
				<button class="nav-item" class:active={view === 'settings'} onclick={() => view = 'settings'}>
					<span class="icon">⚙️</span> Settings
				</button>
			</li>
		</ul>
	</nav>

	<main class="main-content">
		{#if error}
			<div class="error-banner">
				⚠️ Backend unreachable: {error}
			</div>
		{/if}

		{#if view === 'dashboard'}
			<div class="view">
				<h2>Dashboard</h2>
				{#if status}
					<div class="stats-grid">
						<div class="stat-card">
							<h3>Total Entries</h3>
							<p class="stat-value">{status.total_turns?.toLocaleString()}</p>
						</div>
						<div class="stat-card">
							<h3>Sessions</h3>
							<p class="stat-value">{status.total_sessions}</p>
						</div>
						<div class="stat-card">
							<h3>DB Size</h3>
							<p class="stat-value">{(status.storage_bytes / 1024 / 1024).toFixed(1)} MB</p>
						</div>
						<div class="stat-card">
							<h3>Last Turn</h3>
							<p class="stat-value">#{status.last_turn?.turn ?? '-'}</p>
						</div>
					</div>

					<div class="section">
						<h3>Tiers</h3>
						<div class="pill-list">
							{#each Object.entries(status.tiers ?? {}) as [tier, count]}
								<span class="pill">{tier}: {count}</span>
							{/each}
						</div>
					</div>

					<div class="section">
						<h3>Types</h3>
						<div class="pill-list">
							{#each Object.entries(status.types ?? {}) as [type, count]}
								<span class="pill">{type}: {count}</span>
							{/each}
						</div>
					</div>
				{:else}
					<p class="loading">Loading...</p>
				{/if}
			</div>
		{:else if view === 'memory'}
			<MemoryView />
		{:else if view === 'settings'}
			<SettingsView />
		{/if}
	</main>
</div>

<style>
	.app-shell { display: flex; height: 100vh; background: #0a0a0f; color: #e0e0e0; }
	.sidebar { width: 240px; background: #121218; border-right: 1px solid #1e1e28; padding: 1.5rem; display: flex; flex-direction: column; }
	.sidebar-header { margin-bottom: 2rem; }
	.logo { font-size: 1.25rem; font-weight: 700; color: #7c5cfc; margin: 0; }
	.nav-list { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 0.25rem; }
	.nav-item { width: 100%; padding: 0.75rem 1rem; border: none; border-radius: 8px; background: transparent; color: #888; font-size: 0.9rem; cursor: pointer; text-align: left; transition: all 0.15s; display: flex; align-items: center; gap: 0.5rem; }
	.nav-item:hover { background: #1a1a24; color: #e0e0e0; }
	.nav-item.active { background: #7c5cfc20; color: #7c5cfc; font-weight: 600; }
	.icon { font-size: 1.1rem; }
	.main-content { flex: 1; overflow-y: auto; padding: 2rem; }
	.error-banner { background: #dc262640; border: 1px solid #dc2626; border-radius: 8px; padding: 0.75rem 1rem; margin-bottom: 1.5rem; color: #fca5a5; font-size: 0.9rem; }
	.view { max-width: 900px; }
	h2 { font-size: 1.5rem; font-weight: 700; margin: 0 0 1.5rem 0; }
	h3 { font-size: 0.85rem; font-weight: 600; color: #888; text-transform: uppercase; letter-spacing: 0.05em; margin: 0 0 0.75rem 0; }
	.stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 1rem; margin-bottom: 2rem; }
	.stat-card { background: #121218; border: 1px solid #1e1e28; border-radius: 12px; padding: 1.25rem; }
	.stat-card h3 { margin-bottom: 0.5rem; }
	.stat-value { font-size: 1.75rem; font-weight: 700; color: #e0e0e0; margin: 0; }
	.section { margin-bottom: 2rem; }
	.pill-list { display: flex; flex-wrap: wrap; gap: 0.5rem; }
	.pill { background: #1e1e28; border: 1px solid #2a2a38; border-radius: 20px; padding: 0.35rem 0.75rem; font-size: 0.85rem; color: #aaa; }
	.loading { color: #666; font-style: italic; }
</style>
