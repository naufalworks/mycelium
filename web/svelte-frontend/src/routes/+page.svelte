<script lang="ts">
	import { onMount } from 'svelte';
	import Dashboard from '$lib/Dashboard.svelte';

	let status = $state<any>(null);

	onMount(async () => {
		try {
			const res = await fetch('/api/status');
			if (res.ok) status = await res.json();
		} catch (_) {}
	});
</script>

<Dashboard {status} />

<style>
	/* Dashboard styles are in Dashboard.svelte */
</style>
