<script lang="ts">
	import { onMount } from 'svelte';
	import Dashboard from '$lib/Dashboard.svelte';
	import Memory from '$lib/Memory.svelte';

	let status = $state<any>(null);
	let error = $state<string | null>(null);
	let view = $state('dashboard');

	onMount(async () => {
		try {
			const res = await fetch('/api/status');
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
			<li><button class="nav-item" class:active={view === 'dashboard'} onclick={() => view = 'dashboard'}>
				<span class="icon">📊</span> Dashboard
			</button></li>
			<li><button class="nav-item" class:active={view === 'memory'} onclick={() => view = 'memory'}>
				<span class="icon">🧠</span> Memory
			</button></li>
		</ul>
	</nav>

	<main class="main-content">
		{#if error}
			<div class="error-banner">⚠️ Backend unreachable: {error}</div>
		{/if}

		{#if view === 'dashboard'}
			<Dashboard {status} />
		{:else if view === 'memory'}
			<Memory />
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
</style>
