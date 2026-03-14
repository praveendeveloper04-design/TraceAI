/**
 * TraceAI State Manager — Persist state via VS Code globalState.
 *
 * Tracks:
 *   - First run detection
 *   - Setup completion
 *   - Default assignee
 */

import * as vscode from 'vscode';

const KEY_SETUP_COMPLETE = 'traceai.setupComplete';
const KEY_ASSIGNEE = 'traceai.assignee';
const KEY_FIRST_RUN = 'traceai.firstRun';

export class StateManager {
    constructor(private globalState: vscode.Memento) {}

    /**
     * Check if this is the first time the extension is running.
     */
    isFirstRun(): boolean {
        return !this.globalState.get<boolean>(KEY_FIRST_RUN, false);
    }

    /**
     * Mark that the extension has been run at least once.
     */
    markFirstRunComplete(): void {
        this.globalState.update(KEY_FIRST_RUN, true);
    }

    /**
     * Check if setup has been completed.
     */
    isSetupComplete(): boolean {
        return this.globalState.get<boolean>(KEY_SETUP_COMPLETE, false);
    }

    /**
     * Mark setup as complete.
     */
    markSetupComplete(): void {
        this.globalState.update(KEY_SETUP_COMPLETE, true);
    }

    /**
     * Get the stored assignee email.
     */
    getAssignee(): string | undefined {
        return this.globalState.get<string>(KEY_ASSIGNEE);
    }

    /**
     * Set the assignee email.
     */
    setAssignee(email: string): void {
        this.globalState.update(KEY_ASSIGNEE, email);
    }
}
