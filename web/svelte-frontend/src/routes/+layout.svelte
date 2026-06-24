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
			<h1 class="logo">🍄 <span class="logo-text">mycelium</span></h1>
			<span class="logo-tag">permanent brain</span>
		</div>
		<ul class="nav-list">
			<li><button class="nav-item" class:active={view === 'dashboard'} onclick={() => view = 'dashboard'}>
				<span class="nav-icon">📊</span> <span>Dashboard</span>
			</button></li>
			<li><button class="nav-item" class:active={view === 'memory'} onclick={() => view = 'memory'}>
				<span class="nav-icon">🧠</span> <span>Memory</span>
			</button></li>
		</ul>
		<div class="sidebar-footer">
			{#if status}
				<span class="footer-turns">{status.total_turns ?? 0} turns</span>
			{/if}
		</div>
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
	:global(*) { margin: 0; padding: 0; box-sizing: border-box; }
	:global(body) {
		font-family: 'Inter', system-ui, -apple-system, sans-serif;
		font-size: 14px;
		line-height: 1.6;
		-webkit-font-smoothing: antialiased;
		overflow: hidden;
	}
	:global(::-webkit-scrollbar) { width: 5px; height: 5px; }
	:global(::-webkit-scrollbar-track) { background: transparent; }
	:global(::-webkit-scrollbar-thumb) { background: #2a2620; border-radius: 999px; }
	:global(::-webkit-scrollbar-thumb:hover) { background: #403a31; }

	.app-shell {
		display: flex;
		height: 100vh;
		background: #0E0C0A;
		color: #EDE6DD;
	}
	.sidebar {
		width: 220px;
		background: #181512;
		border-right: 1px solid #26221D;
		padding: 16px 8px;
		display: flex;
		flex-direction: column;
		flex-shrink: 0;
		position: relative;
		overflow: hidden;
	}
	.sidebar::after {
		content: '';
		position: absolute;
		bottom: 0;
		left: 0;
		right: 0;
		height: 80px;
		background: linear-gradient(to top, rgba(110,231,183,0.04), rgba(167,139,250,0.02), transparent);
		pointer-events: none;
	}
	.sidebar-header {
		padding: 8px 8px 16px;
		border-bottom: 1px solid #26221D;
		margin-bottom: 12px;
	}
	.logo {
		font-family: 'Space Grotesk', sans-serif;
		font-size: 1.25rem;
		font-weight: 700;
		color: #EDE6DD;
		margin: 0;
		display: flex;
		align-items: center;
		gap: 6px;
	}
	.logo-text { color: #6EE7B7; }
	.logo-tag {
		font-size: 0.6875rem;
		color: #7A736A;
		display: block;
		margin-top: 2px;
		margin-left: 32px;
	}
	.nav-list {
		list-style: none;
		padding: 0;
		margin: 0;
		display: flex;
		flex-direction: column;
		gap: 2px;
		flex: 1;
	}
	.nav-item {
		width: 100%;
		padding: 8px 12px;
		border: none;
		border-radius: 6px;
		background: transparent;
		color: #B0A89E;
		font-size: 0.8125rem;
		font-weight: 500;
		font-family: inherit;
		cursor: pointer;
		text-align: left;
		transition: all 150ms;
		display: flex;
		align-items: center;
		gap: 8px;
		position: relative;
	}
	.nav-item:hover { background: #201D19; color: #EDE6DD; }
	.nav-item.active {
		color: #6EE7B7;
		background: rgba(110,231,183,0.10);
		box-shadow: inset 2px 0 0 #6EE7B7;
	}
	.nav-icon { width: 20px; text-align: center; flex-shrink: 0; }
	.sidebar-footer {
		padding: 12px 8px 4px;
		border-top: 1px solid #26221D;
		position: relative;
		z-index: 1;
	}
	.footer-turns {
		font-size: 0.75rem;
		color: #7A736A;
		font-family: 'JetBrains Mono', monospace;
	}
	.main-content {
		flex: 1;
		overflow-y: auto;
		padding: 24px 32px;
		background:
			radial-gradient(ellipse at 20% 0%, rgba(110,231,183,0.015) 0%, transparent 50%),
			radial-gradient(ellipse at 80% 100%, rgba(167,139,250,0.012) 0%, transparent 50%);
	}
	.error-banner {
		background: rgba(251,113,133,0.12);
		border: 1px solid rgba(251,113,133,0.3);
		border-radius: 8px;
		padding: 10px 16px;
		margin-bottom: 20px;
		color: #FB7185;
		font-size: 0.875rem;
	}
</style>
