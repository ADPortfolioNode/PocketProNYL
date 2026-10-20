import React from 'react';
import DataFlowPipeline from './DataFlowPipeline';

export default function WorkflowSummary() {
  return (
    <>
    <DataFlowPipeline />
    <details className="card p-3 mb-3 workflow-howto">
      <summary>How the workflow works</summary>
      <h5 className="mt-3">UI/UX and User Workflow Summary</h5>
      <p>This application provides a dashboard for a machine learning workflow with the following steps:</p>
      <ol>
        <li>
          <strong>Select a Game:</strong> The user starts by selecting a "game" from a dropdown menu. 
          The list of games is fetched from the backend.
        </li>
        <li>
          <strong>Ingest Data:</strong> After selecting a game, the user can initiate data ingestion. 
          The UI displays the progress and status of this process.
        </li>
        <li>
          <strong>Train Model:</strong> Once data ingestion is complete, the user can start model training. 
          The UI shows the training progress and status.
        </li>
        <li>
          <strong>Make Suggestions:</strong> After the model is trained, the user can input numeric features to get a suggestion.
        </li>
        <li>
          <strong>Chat with Agent:</strong> A chat panel is available for the user to interact with a conversational agent.
        </li>
      </ol>
    </details>
    </>
  );
}
