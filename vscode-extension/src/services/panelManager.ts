/**
 * TraceAI Panel Manager -- Manages multiple investigation webview panels.
 *
 * Each investigation gets its own tab. Panels are tracked by investigation ID.
 * Clicking the same investigation reveals the existing panel.
 */

import * as vscode from 'vscode';
import { InvestigationReport, ApiService } from './apiService';

interface PanelEntry {
    panel: vscode.WebviewPanel;
    taskId: string;
    investigationId: string | null;
}

export class PanelManager {
    private panels = new Map<string, PanelEntry>();

    constructor(
        private extensionUri: vscode.Uri,
        private apiService: ApiService,
    ) {}

    /**
     * Open a progress panel for a new investigation.
     * Key = taskId until we get an investigationId.
     */
    openProgress(taskId: string, taskTitle: string): vscode.WebviewPanel {
        const key = `task:${taskId}`;

        // Reuse existing panel for same task
        const existing = this.panels.get(key);
        if (existing) {
            existing.panel.reveal(vscode.ViewColumn.Active);
            existing.panel.webview.html = this.getProgressHtml(taskId, taskTitle);
            return existing.panel;
        }

        const panel = vscode.window.createWebviewPanel(
            'traceaiInvestigation',
            `Investigating: ${taskTitle.substring(0, 40)}`,
            vscode.ViewColumn.Active,
            { enableScripts: true, retainContextWhenHidden: true },
        );

        panel.onDidDispose(() => {
            this.panels.delete(key);
            for (const [k, v] of this.panels) {
                if (v.panel === panel) {
                    this.panels.delete(k);
                }
            }
        });

        // Register message handler once at creation
        panel.webview.onDidReceiveMessage(async (msg) => {
            if (msg.command === 'rerun') {
                vscode.commands.executeCommand('traceai.investigateFromId', msg.taskId);
            }
        });

        this.panels.set(key, { panel, taskId, investigationId: null });
        panel.webview.html = this.getProgressHtml(taskId, taskTitle);
        return panel;
    }

    /**
     * Update progress on an existing panel.
     */
    updateProgress(taskId: string, stage: string, message: string, special?: string): void {
        const key = `task:${taskId}`;
        const entry = this.panels.get(key);
        if (entry) {
            entry.panel.webview.postMessage({
                command: 'updateProgress',
                stage,
                message,
                special: special || null,
            });
        }
    }

    /**
     * Show the final report in the panel, replacing progress.
     */
    showReport(taskId: string, report: InvestigationReport): void {
        const key = `task:${taskId}`;
        const entry = this.panels.get(key);
        if (entry) {
            // Re-key by investigation ID
            if (report.id) {
                entry.investigationId = report.id;
                this.panels.set(`inv:${report.id}`, entry);
            }
            entry.panel.title = `Investigation: ${report.task_title?.substring(0, 40) || taskId}`;
            entry.panel.webview.html = this.getReportHtml(report);
        }
    }

    /**
     * Open a saved report by investigation ID.
     */
    async openSavedReport(investigationId: string): Promise<void> {
        const key = `inv:${investigationId}`;
        const existing = this.panels.get(key);
        if (existing) {
            existing.panel.reveal(vscode.ViewColumn.Active);
            return;
        }

        try {
            const report = await this.apiService.getInvestigation(investigationId);
            const panel = vscode.window.createWebviewPanel(
                'traceaiInvestigation',
                `Investigation: ${report.task_title?.substring(0, 40) || investigationId}`,
                vscode.ViewColumn.Active,
                { enableScripts: true, retainContextWhenHidden: true },
            );

            panel.onDidDispose(() => { this.panels.delete(key); });
            panel.webview.html = this.getReportHtml(report);

            panel.webview.onDidReceiveMessage(async (msg) => {
                if (msg.command === 'rerun') {
                    vscode.commands.executeCommand('traceai.investigateFromId', msg.taskId);
                }
            });

            this.panels.set(key, { panel, taskId: report.task_id, investigationId });
        } catch (error) {
            vscode.window.showErrorMessage(`Failed to load investigation: ${error}`);
        }
    }

    // ── HTML Generators ──────────────────────────────────────────────────

    private getProgressHtml(taskId: string, taskTitle: string): string {
        const nonce = this.getNonce();
        const stages = [
            'Loading ticket', 'Running skills', 'Aggregating evidence',
            'Building graph', 'Building context', 'AI reasoning', 'Generating report',
        ];

        return `<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'nonce-${nonce}';">
    <style>
        body { font-family: var(--vscode-font-family, sans-serif); color: var(--vscode-editor-foreground, #ccc);
               background: var(--vscode-editor-background, #1e1e1e); padding: 32px; max-width: 600px; margin: 0 auto; }
        h1 { font-size: 1.3em; margin-bottom: 4px; }
        .subtitle { opacity: 0.6; font-size: 0.9em; margin-bottom: 24px; }
        .stages { list-style: none; padding: 0; }
        .stage { padding: 8px 12px; margin: 2px 0; border-radius: 6px; display: flex; align-items: center; gap: 10px; }
        .stage.completed { opacity: 0.6; }
        .stage.running { background: rgba(33,150,243,0.08); border-left: 3px solid #2196f3; font-weight: 500; }
        .stage.pending { opacity: 0.3; }
        .icon { width: 20px; text-align: center; }
        .spinner { display: inline-block; animation: spin 1s linear infinite; }
        @keyframes spin { to { transform: rotate(360deg); } }
        .banner { margin-top: 16px; padding: 10px 14px; border-radius: 6px; display: none; font-weight: 500; }
        .banner.show { display: block; }
        .banner.complete { background: rgba(76,175,80,0.1); color: #4caf50; }
        .banner.error { background: rgba(244,67,54,0.1); color: #f44336; }
        .banner.cancelled { background: rgba(255,152,0,0.1); color: #ff9800; }
        .log-area { margin-top: 20px; font-size: 0.82em; opacity: 0.7; max-height: 200px; overflow-y: auto; }
    </style>
</head>
<body>
    <h1>Investigating: ${this.esc(taskTitle)}</h1>
    <div class="subtitle">Task ${this.esc(taskId)}</div>
    <ul class="stages" id="stageList">
        ${stages.map(s => `<li class="stage pending"><span class="icon">&#9675;</span> ${s}</li>`).join('\n        ')}
    </ul>
    <div class="banner" id="banner"></div>
    <div class="log-area" id="logs"></div>
    <script nonce="${nonce}">
        (function() {
            var vscode = acquireVsCodeApi();
            var stageNames = ${JSON.stringify(stages)};
            var stageKeys = ['loading_ticket','skills_execution','evidence_aggregation',
                'building_graph','building_context','ai_reasoning','generating_report'];

            window.addEventListener('message', function(e) {
                var msg = e.data;
                if (msg.command === 'updateProgress') {
                    var list = document.getElementById('stageList');
                    var banner = document.getElementById('banner');
                    var logs = document.getElementById('logs');
                    var idx = stageKeys.indexOf(msg.stage);

                    if (list && idx >= 0) {
                        var html = '';
                        for (var i = 0; i < stageNames.length; i++) {
                            var cls = 'pending';
                            var icon = '&#9675;';
                            if (i < idx) { cls = 'completed'; icon = '&#10004;'; }
                            else if (i === idx) { cls = 'running'; icon = '<span class="spinner">&#9679;</span>'; }
                            html += '<li class="stage ' + cls + '"><span class="icon">' + icon + '</span> ' + stageNames[i] + '</li>';
                        }
                        list.innerHTML = html;
                    }

                    if (msg.special && banner) {
                        banner.className = 'banner show ' + msg.special;
                        banner.textContent = msg.message || '';
                    }

                    if (logs && msg.message) {
                        var t = new Date().toLocaleTimeString();
                        logs.innerHTML += '<div>[' + t + '] ' + msg.message + '</div>';
                        logs.scrollTop = logs.scrollHeight;
                    }
                }
            });
        })();
    </script>
</body>
</html>`;
    }

    private getNonce(): string {
        let text = '';
        const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
        for (let i = 0; i < 32; i++) {
            text += chars.charAt(Math.floor(Math.random() * chars.length));
        }
        return text;
    }

    private getReportHtml(report: InvestigationReport): string {
        const nonce = this.getNonce();
        const statusColor = report.status === 'completed' ? '#4caf50' : report.status === 'failed' ? '#f44336' : '#ff9800';

        const findingsHtml = (report.findings || []).map((f, i) => `
            <div class="card">
                <h3>${i + 1}. ${this.esc(f.title)}</h3>
                <span class="badge">${this.esc(f.category)}</span>
                <span class="badge conf">${Math.round(f.confidence * 100)}%</span>
                <p>${this.esc(f.description)}</p>
                ${f.evidence?.length ? '<ul>' + f.evidence.map(e => '<li>' + this.esc(e) + '</li>').join('') + '</ul>' : ''}
            </div>
        `).join('');

        const recsHtml = (report.recommendations || []).map(r => `<li>${this.esc(r)}</li>`).join('');

        const hypothesesHtml = (report.root_cause_hypotheses || []).map((h, i) => {
            const pct = Math.round((h.confidence || 0) * 100);
            return `<div class="card" style="border-left:3px solid #ff9800;">
                <strong>#${i + 1}: ${this.esc(h.description)}</strong>
                <span class="badge conf">${pct}%</span>
                ${h.evidence?.length ? '<ul>' + h.evidence.map(e => '<li>' + this.esc(e) + '</li>').join('') + '</ul>' : ''}
            </div>`;
        }).join('');

        return `<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'nonce-${nonce}';">
    <style>
body { font-family: var(--vscode-font-family, sans-serif); color: var(--vscode-editor-foreground, #ccc);
       background: var(--vscode-editor-background, #1e1e1e); padding: 24px; max-width: 900px; margin: 0 auto; line-height: 1.6; }
h1 { font-size: 1.4em; } h2 { font-size: 1.15em; border-bottom: 1px solid var(--vscode-panel-border); padding-bottom: 6px; margin-top: 24px; }
.status-bar { display: flex; gap: 16px; padding: 10px 14px; background: var(--vscode-editorWidget-background);
              border-radius: 6px; margin-bottom: 16px; border-left: 4px solid ${statusColor}; align-items: center; }
.status-bar .status { font-weight: bold; color: ${statusColor}; }
.card { background: var(--vscode-editorWidget-background); border: 1px solid var(--vscode-panel-border);
        border-radius: 6px; padding: 14px; margin-bottom: 10px; }
.badge { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 0.8em; margin-right: 4px;
         background: var(--vscode-badge-background); color: var(--vscode-badge-foreground); }
.badge.conf { background: rgba(76,175,80,0.15); color: #4caf50; }
code { background: var(--vscode-textCodeBlock-background); padding: 2px 6px; border-radius: 3px; font-size: 0.9em; }
ul { padding-left: 18px; } li { margin-bottom: 3px; }
.btn { padding: 8px 18px; border-radius: 4px; border: none; cursor: pointer; font-weight: 600; margin-right: 8px; margin-top: 12px; }
.btn-rerun { background: var(--vscode-button-background); color: var(--vscode-button-foreground); }
</style></head><body>
<h1>Investigation: ${this.esc(report.task_title || '')}</h1>
<div class="status-bar">
    <span class="status">${(report.status || '').toUpperCase()}</span>
    <span>Task: ${this.esc(report.task_id || '')}</span>
    <span>${report.started_at?.substring(0, 19) || ''}</span>
</div>

${report.summary ? `<h2>Summary</h2><div class="card">${this.esc(report.summary)}</div>` : ''}
${report.root_cause ? `<h2>Root Cause</h2><div class="card" style="border-left:3px solid #f44336;">${this.esc(report.root_cause)}</div>` : ''}
${hypothesesHtml ? `<h2>Hypotheses</h2>${hypothesesHtml}` : ''}
${findingsHtml ? `<h2>Findings (${report.findings?.length || 0})</h2>${findingsHtml}` : ''}
${recsHtml ? `<h2>Recommendations</h2><ul>${recsHtml}</ul>` : ''}
${report.error ? `<h2>Warnings</h2><div class="card" style="border-left:3px solid #ff9800;">${this.esc(report.error)}</div>` : ''}

<button class="btn btn-rerun" id="rerunBtn">Re-run Investigation</button>

<script nonce="${nonce}">
(function() {
    var vscode = acquireVsCodeApi();
    var rawId = '${this.esc(report.task_id || '')}';
    var taskId = rawId.replace(/^ado-/, '');
    document.getElementById('rerunBtn').addEventListener('click', function() {
        vscode.postMessage({ command: 'rerun', taskId: taskId });
    });
})();
</script></body></html>`;
    }

    private esc(text: string): string {
        return (text || '')
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#039;').replace(/\n/g, '<br>');
    }
}
