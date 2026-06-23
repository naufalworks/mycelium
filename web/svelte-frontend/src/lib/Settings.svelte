<script lang="ts">
	export let status: any;
</script>

<div class="view">
	<h2>Settings</h2>
	<div class="settings-grid">
		<div class="setting-card">
			<h3>Database</h3>
			<p class="setting-value">{(status?.storage_bytes / 1024 / 1024).toFixed(1)} MB</p>
		</div>
		<div class="setting-card">
			<h3>Entries</h3>
			<p class="setting-value">{status?.total_turns?.toLocaleString()}</p>
		</div>
		<div class="setting-card">
			<h3>Sessions</h3>
			<p class="setting-value">{status?.total_sessions}</p>
		</div>
	</div>
</div>

<style>
	h2 { font-size: 1.5rem; font-weight: 700; margin: 0 0 1.5rem 0; }
	.settings-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 1rem; }
	.setting-card { background: #121218; border: 1px solid #1e1e28; border-radius: 12px; padding: 1.25rem; }
	h3 { font-size: 0.85rem; font-weight: 600; color: #888; text-transform: uppercase; letter-spacing: 0.05em; margin: 0 0 0.5rem 0; }
	.setting-value { font-size: 1.25rem; font-weight: 700; margin: 0; }
</style>
