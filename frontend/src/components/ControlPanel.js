import React, { useState } from 'react';

export default function ControlPanel({ onProviderChange }) {
	const [provider, setProvider] = useState('local');
	const [openaiEnabled, setOpenaiEnabled] = useState(false);

	const handleProviderChange = (e) => {
		const p = e.target.value;
		setProvider(p);
		if (onProviderChange) onProviderChange(p);
	};

	return (
		<div className="card p-3 mb-3">
			<h5 className="card-title">Model / LLM</h5>

			<div className="mb-2">
				<label className="form-label">Provider</label>
				<select className="form-select" value={provider} onChange={handleProviderChange}>
					<option value="local">Local (offline echo)</option>
					<option value="openai">OpenAI</option>
				</select>
			</div>

			<div className="form-check form-switch">
				<input
					className="form-check-input"
					type="checkbox"
					id="openaiEnabled"
					checked={openaiEnabled}
					onChange={(e) => setOpenaiEnabled(e.target.checked)}
				/>
				<label className="form-check-label" htmlFor="openaiEnabled">
					Enable OpenAI (requires backend config)
				</label>
			</div>

			<small className="text-muted d-block mt-2">Pick a provider; OpenAI requires API key and enabling server-side.</small>
		</div>
	);
}

