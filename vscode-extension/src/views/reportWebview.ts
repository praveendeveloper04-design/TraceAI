/**
 * Report Webview — Renders investigation reports in a VS Code webview panel.
 *
 * Displays:
 *   - Investigation summary and status
 *   - Root cause analysis with confidence scores (Step 9)
 *   - AI-generated fix suggestions (Step 10)
 *   - Approve/Reject fix workflow buttons (Step 11)
 *   - Findings, recommendations, affected files
 *   - Investigation graph stats
 */

import * as vscode from 'vscode';
import * as fs from 'fs';
import * as path from 'path';
import { InvestigationReport } from '../services/apiService';
import { ApiService, PatchFile } from '../services/apiService';

export class ReportWebview {
    private panel: vscode.WebviewPanel | undefined;
    private currentReport: InvestigationReport | undefined;
    private progressStages: Array<{ stage: string; message: string; status: 'completed' | 'running' | 'pending' }> = [];

    constructor(
        private extensionUri: vscode.Uri,
        private apiService?: ApiService,
    ) {}

    setApiService(api: ApiService): void {
        this.apiService = api;
    }

    /**
     * Show the webview with a live progress panel before the report is ready.
     */
    showProgress(taskId: string): void {
        this.progressStages = [
            { stage: 'loading_ticket', message: 'Loading ticket details...', status: 'pending' },
            { stage: 'skills_execution', message: 'Running investigation skills...', status: 'pending' },
            { stage: 'evidence_aggregation', message: 'Aggregating evidence...', status: 'pending' },
            { stage: 'building_graph', message: 'Building evidence graph...', status: 'pending' },
            { stage: 'building_context', message: 'Building investigation context...', status: 'pending' },
            { stage: 'ai_reasoning', message: 'Running AI reasoning with Claude...', status: 'pending' },
            { stage: 'generating_report', message: 'Generating investigation report...', status: 'pending' },
        ];

        this.ensurePanel(`Investigating: ${taskId}`);
        this.panel!.webview.html = this.getProgressHtml(taskId);
    }

    /**
     * Update a progress stage to 'running' (mark previous as 'completed').
     */
    updateProgress(stage: string, message: string): void {
        if (!this.panel) { return; }

        // Handle special stages
        if (stage === 'complete') {
            for (const s of this.progressStages) { s.status = 'completed'; }
        } else if (stage === 'cancelled' || stage === 'error') {
            // Mark current running stage as the special status
            for (const s of this.progressStages) {
                if (s.status === 'running') {
                    s.status = stage === 'cancelled' ? 'completed' : 'completed';
                    break;
                }
            }
        } else {
            // Mark all previous stages as completed, current as running
            let found = false;
            for (const s of this.progressStages) {
                if (s.stage === stage) {
                    s.status = 'running';
                    s.message = message;
                    found = true;
                } else if (!found) {
                    s.status = 'completed';
                }
            }
        }

        this.panel.webview.postMessage({
            command: 'updateProgress',
            stages: this.progressStages,
            special: (stage === 'complete' || stage === 'cancelled' || stage === 'error') ? stage : null,
            specialMessage: message,
        });
    }

    private ensurePanel(title: string): void {
        if (this.panel) {
            this.panel.reveal(vscode.ViewColumn.Beside);
        } else {
            this.panel = vscode.window.createWebviewPanel(
                'traceaiReport',
                title,
                vscode.ViewColumn.Beside,
                {
                    enableScripts: true,
                    retainContextWhenHidden: true,
                    localResourceRoots: [this.extensionUri],
                    enableFindWidget: true,
                },
            );
            this.panel.onDidDispose(() => { this.panel = undefined; });
            this.panel.webview.onDidReceiveMessage(async (message) => {
                if (message.command === 'approveFix') {
                    await this.handleApproveFix();
                } else if (message.command === 'rejectFix') {
                    vscode.window.showInformationMessage('Fix rejected. No changes made.');
                }
            });
        }
        this.panel.title = title;
    }

    private getProgressHtml(taskId: string): string {
        return `<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <script>if(navigator.serviceWorker){navigator.serviceWorker.register=function(){return Promise.reject()};}</script>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; worker-src 'none';">
    <title>Investigation Progress</title>
    <style>
        body {
            font-family: var(--vscode-font-family);
            color: var(--vscode-editor-foreground);
            background: var(--vscode-editor-background);
            padding: 32px; line-height: 1.7; max-width: 600px; margin: 0 auto;
        }
        h1 { font-size: 1.3em; margin-bottom: 8px; }
        .subtitle { opacity: 0.6; font-size: 0.9em; margin-bottom: 24px; }
        .progress-list { list-style: none; padding: 0; margin: 0; }
        .progress-item {
            padding: 10px 14px; margin-bottom: 2px; border-radius: 6px;
            display: flex; align-items: center; gap: 12px; font-size: 0.95em;
            transition: all 0.3s ease;
        }
        .progress-item.completed { opacity: 0.6; }
        .progress-item.completed .icon { color: #4caf50; }
        .progress-item.running {
            background: rgba(33, 150, 243, 0.08);
            border-left: 3px solid #2196f3;
            font-weight: 500;
        }
        .progress-item.running .icon { color: #2196f3; }
        .progress-item.pending { opacity: 0.3; }
        .icon { font-size: 1.1em; width: 22px; text-align: center; flex-shrink: 0; }
        .spinner { display: inline-block; animation: spin 1s linear infinite; }
        @keyframes spin { to { transform: rotate(360deg); } }
        .status-banner {
            margin-top: 20px; padding: 12px 16px; border-radius: 6px;
            font-weight: 500; display: none;
        }
        .status-banner.complete { display: block; background: rgba(76,175,80,0.1); color: #4caf50; border: 1px solid rgba(76,175,80,0.3); }
        .status-banner.error { display: block; background: rgba(244,67,54,0.1); color: #f44336; border: 1px solid rgba(244,67,54,0.3); }
        .status-banner.cancelled { display: block; background: rgba(255,152,0,0.1); color: #ff9800; border: 1px solid rgba(255,152,0,0.3); }
    </style>
</head>
<body>
    <h1>Investigating: ${this.esc(taskId)}</h1>
    <div class="subtitle">TraceAI is analyzing this ticket. You can cancel from the notification bar.</div>
    <ul class="progress-list" id="progressList">
        ${this.progressStages.map(s => this.renderProgressItem(s)).join('')}
    </ul>
    <div class="status-banner" id="statusBanner"></div>
    <script>
        const vscode = acquireVsCodeApi();
        window.addEventListener('message', event => {
            const msg = event.data;
            if (msg.command === 'updateProgress') {
                const list = document.getElementById('progressList');
                const banner = document.getElementById('statusBanner');
                if (list) {
                    list.innerHTML = msg.stages.map(function(s) {
                        var icon = s.status === 'completed' ? '<span style="color:#4caf50">&#10004;</span>'
                            : s.status === 'running' ? '<span class="spinner" style="color:#2196f3">&#11044;</span>'
                            : '<span style="opacity:0.3">&#9675;</span>';
                        return '<li class="progress-item ' + s.status + '">'
                            + '<span class="icon">' + icon + '</span>'
                            + '<span>' + s.message + '</span></li>';
                    }).join('');
                }
                if (banner && msg.special) {
                    banner.className = 'status-banner ' + msg.special;
                    banner.style.display = 'block';
                    banner.textContent = msg.specialMessage || '';
                }
            }
        });
    </script>
</body>
</html>`;
    }

    private renderProgressItem(s: { stage: string; message: string; status: string }): string {
        const icon = s.status === 'completed' ? '&#10003;'
            : s.status === 'running' ? '<span class="spinner">&#9696;</span>'
            : '&#9679;';
        return `<li class="progress-item ${s.status}">
            <span class="icon">${icon}</span>
            <span>${this.esc(s.message)}</span>
        </li>`;
    }

    show(report: InvestigationReport): void {
        this.currentReport = report;
        this.ensurePanel(`Investigation: ${report.task_title}`);
        this.panel!.webview.html = this.getHtml(report);
    }

    /**
     * Handle the Approve Fix workflow:
     *   1. Send investigation to Claude for patch generation
     *   2. Show patch preview with diffs
     *   3. Let user confirm each file change
     *   4. Apply patches to local files
     */
    private async handleApproveFix(): Promise<void> {
        if (!this.currentReport || !this.apiService) {
            vscode.window.showErrorMessage('Cannot generate patch: no investigation data.');
            return;
        }

        const workspaceFolders = vscode.workspace.workspaceFolders;
        const workspacePath = workspaceFolders?.[0]?.uri.fsPath;

        // Step 1: Generate patch via Claude
        const patchResult = await vscode.window.withProgress(
            {
                location: vscode.ProgressLocation.Notification,
                title: 'Generating fix with Claude...',
                cancellable: false,
            },
            async () => {
                try {
                    return await this.apiService!.generatePatch(
                        this.currentReport!.id,
                        workspacePath,
                    );
                } catch (error) {
                    vscode.window.showErrorMessage(`Patch generation failed: ${error}`);
                    return null;
                }
            },
        );

        if (!patchResult || !patchResult.files || patchResult.files.length === 0) {
            vscode.window.showWarningMessage(
                'Claude could not generate a specific code patch for this issue. ' +
                'Review the recommendations manually.',
            );
            return;
        }

        // Step 2: Show patch preview and let user confirm
        const fileDescriptions = patchResult.files.map(
            (f, i) => `${i + 1}. ${f.path}: ${f.description}`
        ).join('\n');

        const confirm = await vscode.window.showWarningMessage(
            `Claude generated patches for ${patchResult.files.length} file(s):\n\n${fileDescriptions}\n\nPreview and apply?`,
            { modal: true },
            'Preview Patches',
            'Cancel',
        );

        if (confirm !== 'Preview Patches') {
            vscode.window.showInformationMessage('Patch cancelled.');
            return;
        }

        // Step 3: Show diff for each file and apply if confirmed
        let applied = 0;
        for (const patchFile of patchResult.files) {
            const result = await this.previewAndApplyPatch(patchFile, workspacePath);
            if (result) {
                applied++;
            }
        }

        if (applied > 0) {
            vscode.window.showInformationMessage(
                `Applied ${applied}/${patchResult.files.length} patch(es). Changes are local only — not committed.`,
            );
        } else {
            vscode.window.showInformationMessage('No patches applied.');
        }
    }

    /**
     * Preview a single file patch and apply if user confirms.
     */
    private async previewAndApplyPatch(
        patchFile: PatchFile,
        workspacePath: string | undefined,
    ): Promise<boolean> {
        if (!workspacePath) {
            vscode.window.showErrorMessage('No workspace folder open. Cannot apply patches.');
            return false;
        }

        // SECURITY: Resolve path and validate it stays inside the workspace
        let filePath = patchFile.path;
        if (!path.isAbsolute(filePath)) {
            filePath = path.join(workspacePath, filePath);
        }
        const resolvedPath = path.resolve(filePath);
        const resolvedWorkspace = path.resolve(workspacePath);

        // Path traversal check: resolved path must be inside workspace
        if (!resolvedPath.startsWith(resolvedWorkspace + path.sep) && resolvedPath !== resolvedWorkspace) {
            vscode.window.showErrorMessage(
                `Security: patch path "${patchFile.path}" resolves outside the workspace. Blocked.`,
            );
            return false;
        }

        // Block writes to dotfiles/hidden directories (.git, .ssh, .env)
        const relativePath = path.relative(resolvedWorkspace, resolvedPath);
        const parts = relativePath.split(path.sep);
        for (const part of parts) {
            if (part.startsWith('.')) {
                vscode.window.showErrorMessage(
                    `Security: patch targets hidden path "${part}". Blocked.`,
                );
                return false;
            }
        }

        if (!fs.existsSync(resolvedPath)) {
            const action = await vscode.window.showWarningMessage(
                `File not found: ${patchFile.path}\n${patchFile.description}`,
                'Skip',
            );
            return false;
        }

        // Read current content
        const currentContent = fs.readFileSync(resolvedPath, 'utf-8');

        // Check if the original text exists in the file
        if (!currentContent.includes(patchFile.original)) {
            const action = await vscode.window.showWarningMessage(
                `Cannot locate the code to patch in ${patchFile.path}.\nThe file may have changed since the investigation.`,
                'Skip',
            );
            return false;
        }

        // Build the patched content
        const patchedContent = currentContent.replace(patchFile.original, patchFile.patched);

        // Show diff using VS Code's built-in diff editor
        const originalUri = vscode.Uri.parse(`traceai-original:${patchFile.path}`);
        const patchedUri = vscode.Uri.parse(`traceai-patched:${patchFile.path}`);

        // Use a temporary file approach for the diff
        const tmpDir = path.join(workspacePath, '.traceai-patches');
        if (!fs.existsSync(tmpDir)) {
            fs.mkdirSync(tmpDir, { recursive: true });
        }
        const tmpOriginal = path.join(tmpDir, `original_${path.basename(resolvedPath)}`);
        const tmpPatched = path.join(tmpDir, `patched_${path.basename(resolvedPath)}`);
        fs.writeFileSync(tmpOriginal, currentContent, 'utf-8');
        fs.writeFileSync(tmpPatched, patchedContent, 'utf-8');

        // Open diff view
        await vscode.commands.executeCommand(
            'vscode.diff',
            vscode.Uri.file(tmpOriginal),
            vscode.Uri.file(tmpPatched),
            `Patch: ${patchFile.path} (${patchFile.description})`,
        );

        // Ask user to confirm
        const apply = await vscode.window.showWarningMessage(
            `Apply this patch to ${patchFile.path}?\n\n${patchFile.description}`,
            { modal: true },
            'Apply',
            'Skip',
        );

        // Clean up temp files
        try {
            fs.unlinkSync(tmpOriginal);
            fs.unlinkSync(tmpPatched);
            fs.rmdirSync(tmpDir);
        } catch {
            // Ignore cleanup errors
        }

        if (apply === 'Apply') {
            fs.writeFileSync(resolvedPath, patchedContent, 'utf-8');
            vscode.window.showInformationMessage(`Patched: ${patchFile.path}`);
            return true;
        }

        return false;
    }

    private getHtml(report: InvestigationReport): string {
        const statusColor = report.status === 'completed' ? '#4caf50' : report.status === 'failed' ? '#f44336' : '#ff9800';
        const statusIcon = report.status === 'completed' ? '&#9989;' : report.status === 'failed' ? '&#10060;' : '&#9203;';

        return `<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <script>if(navigator.serviceWorker){navigator.serviceWorker.register=function(){return Promise.reject()};}</script>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; worker-src 'none';">
    <title>Investigation Report</title>
    <style>
        :root {
            --bg: var(--vscode-editor-background);
            --fg: var(--vscode-editor-foreground);
            --border: var(--vscode-panel-border);
            --accent: var(--vscode-textLink-foreground);
            --card-bg: var(--vscode-editorWidget-background);
        }
        body {
            font-family: var(--vscode-font-family);
            color: var(--fg);
            background: var(--bg);
            padding: 24px;
            line-height: 1.6;
            max-width: 900px;
            margin: 0 auto;
        }
        h1 { font-size: 1.5em; margin-bottom: 8px; }
        h2 { font-size: 1.2em; margin-top: 24px; border-bottom: 1px solid var(--border); padding-bottom: 8px; }
        h3 { font-size: 1.05em; margin-bottom: 4px; }
        .status-bar {
            display: flex; gap: 16px; align-items: center;
            padding: 12px 16px; background: var(--card-bg);
            border-radius: 6px; margin-bottom: 20px;
            border-left: 4px solid ${statusColor};
        }
        .status-bar .status { font-weight: bold; color: ${statusColor}; }
        .badge {
            display: inline-block; padding: 2px 8px; border-radius: 12px;
            font-size: 0.8em; margin-right: 6px;
        }
        .badge.category { background: var(--vscode-badge-background); color: var(--vscode-badge-foreground); }
        .badge.confidence { background: rgba(76, 175, 80, 0.2); color: #4caf50; }
        .badge.high { background: rgba(244, 67, 54, 0.2); color: #f44336; }
        .card {
            background: var(--card-bg); border-radius: 6px;
            padding: 16px; margin-bottom: 12px; border: 1px solid var(--border);
        }
        .root-cause-card {
            background: var(--card-bg); border-radius: 6px;
            padding: 16px; margin-bottom: 12px;
            border: 1px solid var(--border); border-left: 4px solid #ff9800;
        }
        .confidence-bar {
            height: 6px; border-radius: 3px; background: rgba(128,128,128,0.2);
            margin-top: 6px; overflow: hidden;
        }
        .confidence-fill {
            height: 100%; border-radius: 3px; background: #4caf50;
            transition: width 0.3s;
        }
        .fix-card {
            background: var(--card-bg); border-radius: 6px;
            padding: 16px; margin-bottom: 12px;
            border: 1px solid var(--border); border-left: 4px solid var(--accent);
        }
        .fix-actions {
            display: flex; gap: 12px; margin-top: 16px;
        }
        .btn {
            padding: 8px 20px; border-radius: 4px; border: none;
            cursor: pointer; font-size: 0.9em; font-weight: 600;
        }
        .btn-approve {
            background: #4caf50; color: white;
        }
        .btn-approve:hover { background: #43a047; }
        .btn-reject {
            background: transparent; color: var(--fg);
            border: 1px solid var(--border);
        }
        .btn-reject:hover { background: rgba(128,128,128,0.1); }
        code {
            background: var(--vscode-textCodeBlock-background);
            padding: 2px 6px; border-radius: 3px; font-size: 0.9em;
        }
        pre {
            background: var(--vscode-textCodeBlock-background);
            padding: 12px; border-radius: 6px; overflow-x: auto;
            font-size: 0.85em; line-height: 1.4;
        }
        ul { padding-left: 20px; }
        li { margin-bottom: 4px; }
        .graph-stats { display: flex; gap: 24px; }
        .graph-stat { text-align: center; }
        .graph-stat .number { font-size: 1.5em; font-weight: bold; color: var(--accent); }
        .graph-stat .label { font-size: 0.8em; opacity: 0.7; }
    </style>
</head>
<body>
    <h1>${statusIcon} Investigation: ${this.esc(report.task_title)}</h1>

    <div class="status-bar">
        <span class="status">${report.status.toUpperCase()}</span>
        <span>Task: ${this.esc(report.task_id)}</span>
        <span>Started: ${report.started_at?.substring(0, 19) || 'N/A'}</span>
        ${report.completed_at ? `<span>Completed: ${report.completed_at.substring(0, 19)}</span>` : ''}
    </div>

    ${this.renderSummary(report)}
    ${this.renderRootCause(report)}
    ${this.renderHypotheses(report)}
    ${this.renderFindings(report)}
    ${this.renderSuggestedFix(report)}
    ${this.renderRecommendations(report)}
    ${this.renderAffectedFiles(report)}
    ${this.renderGraph(report)}
    ${this.renderError(report)}

    <script>
        const vscode = acquireVsCodeApi();
        function approveFix() { vscode.postMessage({ command: 'approveFix' }); }
        function rejectFix() { vscode.postMessage({ command: 'rejectFix' }); }
    </script>
</body>
</html>`;
    }

    // ── Section Renderers ────────────────────────────────────────────────

    private renderSummary(report: InvestigationReport): string {
        if (!report.summary) { return ''; }
        return `
            <h2>Summary</h2>
            <div class="card">${this.esc(report.summary)}</div>
        `;
    }

    private renderRootCause(report: InvestigationReport): string {
        if (!report.root_cause) { return ''; }
        return `
            <h2>Root Cause Analysis</h2>
            <div class="root-cause-card">${this.esc(report.root_cause)}</div>
        `;
    }

    private renderHypotheses(report: InvestigationReport): string {
        const hyps = report.root_cause_hypotheses;
        if (!hyps || hyps.length === 0) { return ''; }

        const items = hyps.map((h, i) => {
            const pct = Math.round(h.confidence * 100);
            const color = pct >= 70 ? '#4caf50' : pct >= 40 ? '#ff9800' : '#9e9e9e';
            return `
                <div class="root-cause-card">
                    <h3>#${i + 1}: ${this.esc(h.description)}</h3>
                    <div style="display:flex;align-items:center;gap:12px;margin-top:8px;">
                        <span class="badge confidence">Confidence: ${pct}%</span>
                        <div class="confidence-bar" style="flex:1;">
                            <div class="confidence-fill" style="width:${pct}%;background:${color};"></div>
                        </div>
                    </div>
                    ${h.evidence.length > 0 ? `
                        <div style="margin-top:8px;font-size:0.9em;">
                            <strong>Evidence:</strong>
                            <ul>${h.evidence.map(e => `<li>${this.esc(e)}</li>`).join('')}</ul>
                        </div>
                    ` : ''}
                </div>
            `;
        }).join('');

        return `<h2>Root Cause Hypotheses (${hyps.length})</h2>${items}`;
    }

    private renderFindings(report: InvestigationReport): string {
        if (report.findings.length === 0) { return ''; }

        const items = report.findings.map((f, i) => {
            const pct = Math.round(f.confidence * 100);
            return `
                <div class="card">
                    <h3>${i + 1}. ${this.esc(f.title)}</h3>
                    <div style="margin-bottom:8px;">
                        <span class="badge category">${this.esc(f.category)}</span>
                        <span class="badge confidence">Confidence: ${pct}%</span>
                    </div>
                    <p>${this.esc(f.description)}</p>
                    ${f.file_references.length > 0 ? `
                        <div style="margin-top:8px;font-size:0.9em;">
                            <strong>Files:</strong>
                            ${f.file_references.map(fr => `<code>${this.esc(fr)}</code>`).join(', ')}
                        </div>
                    ` : ''}
                    ${f.evidence.length > 0 ? `
                        <div style="margin-top:8px;font-size:0.9em;">
                            <strong>Evidence:</strong>
                            <ul>${f.evidence.map(e => `<li>${this.esc(e)}</li>`).join('')}</ul>
                        </div>
                    ` : ''}
                </div>
            `;
        }).join('');

        return `<h2>Findings (${report.findings.length})</h2>${items}`;
    }

    private renderSuggestedFix(report: InvestigationReport): string {
        // Extract fix suggestion from the highest-confidence root_cause finding
        const rootCauseFindings = report.findings.filter(f => f.category === 'root_cause');
        if (rootCauseFindings.length === 0 && !report.root_cause) { return ''; }

        const topFinding = rootCauseFindings[0];
        const fixDescription = topFinding
            ? topFinding.description
            : report.root_cause;

        // Build a suggested fix from recommendations
        const fixSteps = report.recommendations.length > 0
            ? report.recommendations
            : ['Review the root cause analysis above and apply appropriate changes.'];

        return `
            <h2>Suggested Fix</h2>
            <div class="fix-card">
                <p><strong>Root Cause:</strong> ${this.esc(fixDescription)}</p>
                <p><strong>Recommended Changes:</strong></p>
                <ul>${fixSteps.map(s => `<li>${this.esc(s)}</li>`).join('')}</ul>
                ${report.affected_files.length > 0 ? `
                    <p><strong>Files to modify:</strong></p>
                    <ul>${report.affected_files.slice(0, 10).map(f => `<li><code>${this.esc(f)}</code></li>`).join('')}</ul>
                ` : ''}
                <div class="fix-actions">
                    <button class="btn btn-approve" onclick="approveFix()">Approve Fix</button>
                    <button class="btn btn-reject" onclick="rejectFix()">Reject Fix</button>
                </div>
            </div>
        `;
    }

    private renderRecommendations(report: InvestigationReport): string {
        if (report.recommendations.length === 0) { return ''; }
        const items = report.recommendations.map(r => `<li>${this.esc(r)}</li>`).join('');
        return `<h2>Recommendations</h2><ul>${items}</ul>`;
    }

    private renderAffectedFiles(report: InvestigationReport): string {
        if (report.affected_files.length === 0) { return ''; }
        const items = report.affected_files.map(f => `<li><code>${this.esc(f)}</code></li>`).join('');
        return `<h2>Affected Files</h2><ul>${items}</ul>`;
    }

    private renderGraph(report: InvestigationReport): string {
        const graph = report.investigation_graph as {
            nodes?: Array<{ id: string; type: string; label?: string; confidence?: number | null; is_root_cause?: boolean; data?: Record<string, unknown> }>;
            edges?: Array<{ source: string; target: string; relationship?: string }>;
            root_cause_node_id?: string | null;
            causal_chains?: string[][] | null;
            stats?: { node_count?: number; edge_count?: number; node_types?: Record<string, number> };
        } | null;

        if (!graph || !graph.nodes || graph.nodes.length === 0) { return ''; }

        const nodes = graph.nodes || [];
        const edges = graph.edges || [];
        const stats = graph.stats || {};
        const nodeTypes = stats.node_types || {};
        const rootCauseId = graph.root_cause_node_id || (report as unknown as Record<string, unknown>).root_cause_node_id as string | undefined;
        const causalChains = graph.causal_chains || [];

        const typeIcon: Record<string, string> = {
            ticket: '&#127915;', file: '&#128196;', function: '&#9881;',
            database_table: '&#128451;', sql_query: '&#128269;',
            evidence: '&#128161;', hypothesis: '&#129300;',
            git_commit: '&#128230;', log_entry: '&#128203;', service: '&#9889;',
        };
        const typeColor: Record<string, string> = {
            ticket: '#2196f3', file: '#4caf50', function: '#ff9800',
            database_table: '#9c27b0', sql_query: '#00bcd4',
            evidence: '#ffc107', hypothesis: '#f44336',
            git_commit: '#607d8b', log_entry: '#795548', service: '#e91e63',
        };

        // Stats bar
        const statsHtml = `
            <div class="graph-stats">
                <div class="graph-stat">
                    <div class="number">${stats.node_count || 0}</div>
                    <div class="label">Entities</div>
                </div>
                <div class="graph-stat">
                    <div class="number">${stats.edge_count || 0}</div>
                    <div class="label">Relationships</div>
                </div>
                ${Object.entries(nodeTypes).map(([t, c]) =>
                    `<div class="graph-stat"><div class="number">${c}</div><div class="label">${t}</div></div>`
                ).join('')}
            </div>
        `;

        // Root cause highlight
        let rootCauseHtml = '';
        if (rootCauseId) {
            const rcNode = nodes.find(n => n.id === rootCauseId);
            if (rcNode) {
                const conf = rcNode.confidence != null ? ` (confidence: ${Math.round(rcNode.confidence * 100)}%)` : '';
                rootCauseHtml = `
                    <div class="root-cause-highlight">
                        <span class="rc-icon">&#128308;</span>
                        <strong>Root Cause:</strong> ${this.esc(rcNode.label || rcNode.id)}${conf}
                    </div>
                `;
            }
        }

        // Causal chains
        let chainsHtml = '';
        if (causalChains.length > 0) {
            const chainItems = causalChains.slice(0, 3).map(chain =>
                `<div class="causal-chain">${chain.map(label => `<span class="chain-node">${this.esc(label)}</span>`).join('<span class="chain-arrow"> &#8594; </span>')}</div>`
            ).join('');
            chainsHtml = `
                <div class="causal-section">
                    <h3>Causal Chains</h3>
                    ${chainItems}
                </div>
            `;
        }

        // Node lookup
        const nodeMap = new Map<string, typeof nodes[0]>();
        for (const n of nodes) { nodeMap.set(n.id, n); }

        // Render nodes grouped by type
        const groupedHtml = Object.entries(nodeTypes).map(([nodeType, count]) => {
            const typeNodes = nodes.filter(n => n.type === nodeType);
            const icon = typeIcon[nodeType] || '&#9679;';
            const color = typeColor[nodeType] || '#999';

            const nodeItems = typeNodes.map(n => {
                const isRC = n.id === rootCauseId || n.is_root_cause;
                const label = this.esc(n.label || n.id);
                const confBadge = n.confidence != null
                    ? `<span class="conf-badge">${Math.round(n.confidence * 100)}%</span>`
                    : '';
                const rcBadge = isRC ? '<span class="rc-badge">ROOT CAUSE</span>' : '';

                const outEdges = edges.filter(e => e.source === n.id);
                const inEdges = edges.filter(e => e.target === n.id);

                let edgeHtml = '';
                if (outEdges.length > 0 || inEdges.length > 0) {
                    const edgeItems = [
                        ...outEdges.map(e => {
                            const tgt = nodeMap.get(e.target);
                            const tgtLabel = tgt ? this.esc(tgt.label || tgt.id) : this.esc(e.target);
                            const rel = e.relationship || '?';
                            const isCausal = ['causes', 'triggers', 'impacts', 'explains'].includes(rel);
                            const cls = isCausal ? 'edge-item causal' : 'edge-item';
                            return `<span class="${cls}">--[${this.esc(rel)}]--&gt; ${tgtLabel}</span>`;
                        }),
                        ...inEdges.map(e => {
                            const src = nodeMap.get(e.source);
                            const srcLabel = src ? this.esc(src.label || src.id) : this.esc(e.source);
                            const rel = e.relationship || '?';
                            const isCausal = ['causes', 'triggers', 'impacts', 'explains'].includes(rel);
                            const cls = isCausal ? 'edge-item causal' : 'edge-item';
                            return `<span class="${cls}">&lt;--[${this.esc(rel)}]-- ${srcLabel}</span>`;
                        }),
                    ];
                    edgeHtml = `<div class="node-edges">${edgeItems.join('<br>')}</div>`;
                }

                const nodeClass = isRC ? 'graph-node root-cause-node' : 'graph-node';
                return `<div class="${nodeClass}">
                    <span class="node-label" style="color:${color}">${icon} ${label}</span>
                    ${confBadge}${rcBadge}
                    ${edgeHtml}
                </div>`;
            }).join('');

            return `
                <div class="graph-group">
                    <div class="graph-group-header" style="border-left: 3px solid ${color}; padding-left: 8px;">
                        ${icon} <strong>${nodeType}</strong> (${count})
                    </div>
                    ${nodeItems}
                </div>
            `;
        }).join('');

        return `
            <h2>Evidence Graph</h2>
            <div class="card">${statsHtml}</div>
            ${rootCauseHtml}
            ${chainsHtml}
            <style>
                .graph-group { margin-bottom: 16px; }
                .graph-group-header { font-size: 0.95em; margin-bottom: 6px; padding: 4px 0; }
                .graph-node { padding: 4px 0 4px 20px; font-size: 0.9em; }
                .root-cause-node { background: rgba(244, 67, 54, 0.08); border-left: 3px solid #f44336; padding-left: 17px; border-radius: 4px; margin: 2px 0; }
                .node-label { font-weight: 500; }
                .node-edges { padding-left: 24px; font-size: 0.82em; opacity: 0.8; margin-top: 2px; }
                .edge-item { display: inline-block; margin-right: 8px; }
                .edge-item.causal { color: #f44336; font-weight: 600; }
                .conf-badge { display: inline-block; background: rgba(76,175,80,0.15); color: #4caf50; padding: 1px 6px; border-radius: 8px; font-size: 0.75em; margin-left: 6px; font-weight: 600; }
                .rc-badge { display: inline-block; background: #f44336; color: white; padding: 1px 8px; border-radius: 8px; font-size: 0.7em; margin-left: 6px; font-weight: 700; letter-spacing: 0.5px; }
                .root-cause-highlight { background: rgba(244,67,54,0.1); border: 1px solid rgba(244,67,54,0.3); border-radius: 6px; padding: 12px 16px; margin: 12px 0; font-size: 1em; }
                .rc-icon { font-size: 1.2em; margin-right: 4px; }
                .causal-section { margin: 12px 0; }
                .causal-section h3 { font-size: 1em; margin-bottom: 8px; }
                .causal-chain { background: var(--card-bg); border: 1px solid var(--border); border-radius: 6px; padding: 8px 12px; margin-bottom: 6px; font-size: 0.9em; display: flex; flex-wrap: wrap; align-items: center; }
                .chain-node { background: rgba(128,128,128,0.1); padding: 2px 8px; border-radius: 4px; }
                .chain-arrow { color: #f44336; font-weight: bold; margin: 0 2px; }
            </style>
            ${groupedHtml}
        `;
    }

    private renderError(report: InvestigationReport): string {
        if (!report.error) { return ''; }
        return `
            <h2>Warnings</h2>
            <div class="card" style="border-left: 4px solid #ff9800;">
                ${this.esc(report.error)}
            </div>
        `;
    }

    private esc(text: string): string {
        return text
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;')
            .replace(/\n/g, '<br>');
    }
}
