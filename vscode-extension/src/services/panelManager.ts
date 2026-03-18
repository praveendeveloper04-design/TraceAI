/**
 * TraceAI Panel Manager -- Enterprise-grade investigation webview panels.
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
            {
                enableScripts: true,
                retainContextWhenHidden: false,
                localResourceRoots: [this.extensionUri],
                enableFindWidget: true,
            },
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
            } else if (msg.command === 'applyFixes') {
                await this.handleApplyFixes(panel, msg.investigationId);
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
                {
                    enableScripts: true,
                    retainContextWhenHidden: false,
                    localResourceRoots: [this.extensionUri],
                    enableFindWidget: true,
                },
            );

            panel.onDidDispose(() => { this.panels.delete(key); });
            panel.webview.html = this.getReportHtml(report);

            panel.webview.onDidReceiveMessage(async (msg) => {
                if (msg.command === 'rerun') {
                    vscode.commands.executeCommand('traceai.investigateFromId', msg.taskId);
                } else if (msg.command === 'applyFixes') {
                    await this.handleApplyFixes(panel, msg.investigationId);
                }
            });

            this.panels.set(key, { panel, taskId: report.task_id, investigationId });
        } catch (error) {
            vscode.window.showErrorMessage(`Failed to load investigation: ${error}`);
        }
    }

    // ── Apply Fixes Handler ─────────────────────────────────────────────

    private async handleApplyFixes(panel: vscode.WebviewPanel, investigationId: string): Promise<void> {
        try {
            const workspacePath = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
            const patchResult = await this.apiService.generatePatch(investigationId, workspacePath);

            if (patchResult.parse_error) {
                panel.webview.postMessage({ command: 'patchStatus', status: 'error', message: `Patch parsing failed: ${patchResult.parse_error}` });
                return;
            }
            if (!patchResult.files || patchResult.files.length === 0) {
                panel.webview.postMessage({ command: 'patchStatus', status: 'error', message: 'No patches generated. Investigation may not have found specific code changes.' });
                return;
            }

            let appliedCount = 0;
            for (const file of patchResult.files) {
                try {
                    const lang = this.detectLanguage(file.path);
                    const hasOriginal = file.original && file.original.trim().length > 0;
                    const hasPatched = file.patched && file.patched.trim().length > 0;
                    if (!hasPatched) { continue; }

                    if (hasOriginal) {
                        const originalDoc = await vscode.workspace.openTextDocument({ content: file.original, language: lang });
                        const patchedDoc = await vscode.workspace.openTextDocument({ content: file.patched, language: lang });
                        await vscode.commands.executeCommand('vscode.diff', originalDoc.uri, patchedDoc.uri, `Fix: ${file.description || file.path}`, { preview: false });
                    } else {
                        const doc = await vscode.workspace.openTextDocument({ content: file.patched, language: lang });
                        await vscode.window.showTextDocument(doc, { preview: false, viewColumn: vscode.ViewColumn.Beside });
                    }
                    appliedCount++;
                    await new Promise(r => setTimeout(r, 300));
                } catch (fileErr: any) {
                    console.error(`TraceAI: Failed to open patch for ${file.path}:`, fileErr?.message || fileErr);
                }
            }

            panel.webview.postMessage({ command: 'patchStatus', status: 'success', message: `Generated ${appliedCount} fix(es). Review the diff tabs.` });
            vscode.window.showInformationMessage(`TraceAI: ${appliedCount} suggested fix(es) opened in diff view.`);
        } catch (error) {
            panel.webview.postMessage({ command: 'patchStatus', status: 'error', message: `Failed: ${error}` });
            vscode.window.showErrorMessage(`TraceAI: Failed to generate fixes: ${error}`);
        }
    }

    private detectLanguage(filePath: string): string {
        const ext = filePath.split('.').pop()?.toLowerCase() || '';
        const map: Record<string, string> = {
            'cs': 'csharp', 'py': 'python', 'ts': 'typescript', 'js': 'javascript',
            'json': 'json', 'xml': 'xml', 'sql': 'sql', 'yaml': 'yaml', 'yml': 'yaml',
        };
        return map[ext] || 'plaintext';
    }

    // ── HTML Generators ──────────────────────────────────────────────────

    private getProgressHtml(taskId: string, taskTitle: string): string {
        const nonce = this.getNonce();
        const stageKeys = [
            'loading_ticket', 'classifying', 'parallel_analysis',
            'deep_investigation', 'sql_intelligence', 'evidence_aggregation',
            'building_graph', 'building_context', 'ai_reasoning', 'generating_report',
        ];
        const stageNames = [
            'Loading ticket', 'Classifying task', 'Multi-layer analysis',
            'Deep evidence collection', 'SQL intelligence', 'Aggregating evidence',
            'Building graph', 'Building context', 'AI reasoning', 'Generating report',
        ];
        const stageIcons = [
            'M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2',
            'M7 7h.01M7 3h5c.512 0 1.024.195 1.414.586l7 7a2 2 0 010 2.828l-7 7a2 2 0 01-2.828 0l-7-7A1.994 1.994 0 013 12V7a4 4 0 014-4z',
            'M4 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2V6zM14 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2V6zM4 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2v-2zM14 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2v-2z',
            'M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z',
            'M4 7v10c0 2.21 3.582 4 8 4s8-1.79 8-4V7M4 7c0 2.21 3.582 4 8 4s8-1.79 8-4M4 7c0-2.21 3.582-4 8-4s8 1.79 8 4',
            'M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10',
            'M13 10V3L4 14h7v7l9-11h-7z',
            'M10 20l4-16m4 4l4 4-4 4M6 16l-4-4 4-4',
            'M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z',
            'M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z',
        ];

        return `<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <script nonce="${nonce}">if(navigator.serviceWorker){navigator.serviceWorker.register=function(){return Promise.reject()};}</script>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'nonce-${nonce}'; worker-src 'none';">
    <style>
        :root {
            --bg: var(--vscode-editor-background, #0d1117);
            --fg: var(--vscode-editor-foreground, #c9d1d9);
            --surface: var(--vscode-editorWidget-background, #161b22);
            --surface2: var(--vscode-sideBar-background, #0d1117);
            --border: var(--vscode-panel-border, #30363d);
            --accent: #58a6ff;
            --accent-dim: rgba(88,166,255,0.12);
            --success: #3fb950;
            --success-dim: rgba(63,185,80,0.12);
            --warn: #d29922;
            --error: #f85149;
            --muted: var(--vscode-descriptionForeground, #8b949e);
        }
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: var(--vscode-font-family, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif);
            color: var(--fg); background: var(--bg);
            padding: 0; line-height: 1.5;
        }
        .container { max-width: 680px; margin: 0 auto; padding: 40px 32px; }

        /* Header */
        .header { margin-bottom: 32px; }
        .header-label {
            display: inline-flex; align-items: center; gap: 6px;
            font-size: 10px; font-weight: 600; letter-spacing: 1.2px; text-transform: uppercase;
            color: var(--accent); margin-bottom: 12px;
        }
        .header-label svg { width: 14px; height: 14px; }
        .header h1 {
            font-size: 20px; font-weight: 600; color: var(--fg);
            line-height: 1.3; margin-bottom: 6px;
        }
        .header .meta {
            font-size: 12px; color: var(--muted);
            display: flex; align-items: center; gap: 12px;
        }
        .header .meta .dot { width: 3px; height: 3px; border-radius: 50%; background: var(--muted); }

        /* Progress bar */
        .progress-track {
            height: 2px; background: var(--border); border-radius: 1px;
            margin: 24px 0 28px; overflow: hidden;
        }
        .progress-fill {
            height: 100%; background: var(--accent); border-radius: 1px;
            width: 0%; transition: width 0.6s cubic-bezier(0.4, 0, 0.2, 1);
        }

        /* Stage list */
        .stages { list-style: none; }
        .stage {
            display: flex; align-items: flex-start; gap: 14px;
            padding: 10px 14px; margin-bottom: 2px; border-radius: 8px;
            transition: all 0.3s ease; position: relative;
        }
        .stage.pending { opacity: 0.3; }
        .stage.completed { opacity: 0.55; }
        .stage.running {
            opacity: 1; background: var(--accent-dim);
        }
        .stage-icon {
            width: 32px; height: 32px; border-radius: 8px; flex-shrink: 0;
            display: flex; align-items: center; justify-content: center;
            background: var(--surface); border: 1px solid var(--border);
            transition: all 0.3s ease;
        }
        .stage.completed .stage-icon { background: var(--success-dim); border-color: var(--success); }
        .stage.running .stage-icon { background: var(--accent-dim); border-color: var(--accent); }
        .stage-icon svg { width: 16px; height: 16px; stroke: var(--muted); stroke-width: 1.5; fill: none; }
        .stage.completed .stage-icon svg { stroke: var(--success); }
        .stage.running .stage-icon svg { stroke: var(--accent); }
        .stage-check { display: none; }
        .stage.completed .stage-check { display: block; }
        .stage.completed .stage-svg { display: none; }
        .stage-text { padding-top: 5px; }
        .stage-name { font-size: 13px; font-weight: 500; }
        .stage.running .stage-name { color: var(--accent); }

        /* Pulse animation for running stage */
        .stage.running .stage-icon { animation: pulse 2s ease-in-out infinite; }
        @keyframes pulse {
            0%, 100% { box-shadow: 0 0 0 0 rgba(88,166,255,0.3); }
            50% { box-shadow: 0 0 0 6px rgba(88,166,255,0); }
        }

        /* Elapsed timer */
        .elapsed-bar {
            display: flex; align-items: center; justify-content: space-between;
            margin-top: 24px; padding: 12px 16px;
            background: var(--surface); border: 1px solid var(--border); border-radius: 8px;
            font-size: 12px; color: var(--muted);
        }
        .elapsed-bar .timer { font-variant-numeric: tabular-nums; font-weight: 600; color: var(--fg); }

        /* Banner */
        .banner { margin-top: 16px; padding: 12px 16px; border-radius: 8px; display: none; font-size: 13px; font-weight: 500; }
        .banner.show { display: flex; align-items: center; gap: 8px; }
        .banner.complete { background: var(--success-dim); color: var(--success); }
        .banner.error { background: rgba(248,81,73,0.1); color: var(--error); }
        .banner.cancelled { background: rgba(210,153,34,0.1); color: var(--warn); }

        /* Log area */
        .log-area {
            margin-top: 16px; font-size: 11px; color: var(--muted);
            max-height: 160px; overflow-y: auto; font-family: var(--vscode-editor-font-family, monospace);
            background: var(--surface); border: 1px solid var(--border); border-radius: 8px;
            padding: 12px; display: none;
        }
        .log-area.has-logs { display: block; }
        .log-area div { padding: 1px 0; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div class="header-label">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z"/></svg>
                TraceAI Investigation
            </div>
            <h1>${this.esc(taskTitle)}</h1>
            <div class="meta">
                <span>Task ${this.esc(taskId)}</span>
                <span class="dot"></span>
                <span id="statusText">Initializing</span>
            </div>
        </div>

        <div class="progress-track"><div class="progress-fill" id="progressFill"></div></div>

        <ul class="stages" id="stageList">
            ${stageNames.map((s, i) => `
            <li class="stage pending" data-idx="${i}">
                <div class="stage-icon">
                    <svg class="stage-svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="${stageIcons[i]}"/></svg>
                    <svg class="stage-check" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>
                </div>
                <div class="stage-text"><div class="stage-name">${s}</div></div>
            </li>`).join('')}
        </ul>

        <div class="elapsed-bar">
            <span>Elapsed</span>
            <span class="timer" id="elapsed">0s</span>
        </div>
        <div class="banner" id="banner"></div>
        <div class="log-area" id="logs"></div>
    </div>
    <script nonce="${nonce}">
        (function() {
            var vscode = acquireVsCodeApi();
            var stageNames = ${JSON.stringify(stageNames)};
            var stageKeys = ${JSON.stringify(stageKeys)};
            var startTime = Date.now();
            var totalStages = stageNames.length;

            setInterval(function() {
                var el = document.getElementById('elapsed');
                if (el) {
                    var secs = Math.floor((Date.now() - startTime) / 1000);
                    var m = Math.floor(secs / 60);
                    var s = secs % 60;
                    el.textContent = m > 0 ? m + 'm ' + s + 's' : s + 's';
                }
            }, 1000);

            window.addEventListener('message', function(e) {
                var msg = e.data;
                if (msg.command === 'updateProgress') {
                    var items = document.querySelectorAll('.stage');
                    var banner = document.getElementById('banner');
                    var logs = document.getElementById('logs');
                    var fill = document.getElementById('progressFill');
                    var statusText = document.getElementById('statusText');
                    var idx = stageKeys.indexOf(msg.stage);

                    if (idx >= 0) {
                        items.forEach(function(item, i) {
                            item.className = 'stage ' + (i < idx ? 'completed' : i === idx ? 'running' : 'pending');
                        });
                        if (fill) fill.style.width = Math.round(((idx + 1) / totalStages) * 100) + '%';
                        if (statusText) statusText.textContent = stageNames[idx];
                    }

                    if (msg.special && banner) {
                        banner.className = 'banner show ' + msg.special;
                        banner.textContent = msg.message || '';
                    }

                    if (logs && msg.message) {
                        logs.classList.add('has-logs');
                        var t = new Date().toLocaleTimeString();
                        logs.innerHTML += '<div><span style="opacity:0.5">' + t + '</span> ' + msg.message + '</div>';
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
        const isCompleted = report.status === 'completed';
        const isFailed = report.status === 'failed';
        const statusColor = isCompleted ? '#3fb950' : isFailed ? '#f85149' : '#d29922';
        const statusLabel = (report.status || 'unknown').toUpperCase();
        const findingsCount = report.findings?.length || 0;

        const confidenceColor = (c: number) => c >= 0.7 ? '#3fb950' : c >= 0.4 ? '#d29922' : '#8b949e';
        const categoryIcon = (cat: string) => {
            if (cat.includes('verified')) return 'M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z';
            if (cat.includes('hypothesis')) return 'M8.228 9c.549-1.165 2.03-2 3.772-2 2.21 0 4 1.343 4 3 0 1.4-1.278 2.575-3.006 2.907-.542.104-.994.54-.994 1.093m0 3h.01';
            if (cat.includes('insufficient')) return 'M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z';
            return 'M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z';
        };

        const findingsHtml = (report.findings || []).map((f, i) => {
            const pct = Math.round(f.confidence * 100);
            const color = confidenceColor(f.confidence);
            return `
            <div class="finding-card">
                <div class="finding-header">
                    <div class="finding-icon">
                        <svg viewBox="0 0 24 24" fill="none" stroke="${color}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="${categoryIcon(f.category)}"/></svg>
                    </div>
                    <div class="finding-title-area">
                        <div class="finding-title">${this.esc(f.title)}</div>
                        <div class="finding-meta">
                            <span class="tag">${this.esc(f.category).replace(/_/g, ' ')}</span>
                            <span class="confidence" style="color:${color}">${pct}%</span>
                        </div>
                    </div>
                </div>
                <div class="finding-body">${this.esc(f.description)}</div>
                ${f.evidence?.length ? '<div class="finding-evidence">' + f.evidence.map(e => '<div class="evidence-item">' + this.esc(e) + '</div>').join('') + '</div>' : ''}
            </div>`;
        }).join('');

        const recsHtml = (report.recommendations || []).map(r =>
            `<div class="rec-item"><svg viewBox="0 0 24 24" fill="none" stroke="var(--accent)" stroke-width="2" stroke-linecap="round"><path d="M9 5l7 7-7 7"/></svg><span>${this.esc(r)}</span></div>`
        ).join('');

        const hypothesesHtml = (report.root_cause_hypotheses || []).map((h, i) => {
            const pct = Math.round((h.confidence || 0) * 100);
            return `<div class="hypothesis-card">
                <div class="hypothesis-header"><span class="hypothesis-num">#${i + 1}</span><span class="confidence" style="color:var(--warn)">${pct}%</span></div>
                <div class="hypothesis-text">${this.esc(h.description)}</div>
            </div>`;
        }).join('');

        return `<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <script nonce="${nonce}">if(navigator.serviceWorker){navigator.serviceWorker.register=function(){return Promise.reject()};}</script>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'nonce-${nonce}'; worker-src 'none';">
    <style>
        :root {
            --bg: var(--vscode-editor-background, #0d1117);
            --fg: var(--vscode-editor-foreground, #c9d1d9);
            --surface: var(--vscode-editorWidget-background, #161b22);
            --border: var(--vscode-panel-border, #30363d);
            --accent: #58a6ff;
            --accent-dim: rgba(88,166,255,0.1);
            --success: #3fb950;
            --success-dim: rgba(63,185,80,0.1);
            --warn: #d29922;
            --warn-dim: rgba(210,153,34,0.1);
            --error: #f85149;
            --error-dim: rgba(248,81,73,0.1);
            --muted: var(--vscode-descriptionForeground, #8b949e);
        }
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: var(--vscode-font-family, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif);
            color: var(--fg); background: var(--bg);
            line-height: 1.6; padding: 0;
        }
        .container { max-width: 820px; margin: 0 auto; padding: 36px 32px 60px; }

        /* Header */
        .report-header { margin-bottom: 28px; }
        .header-top { display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px; }
        .header-label {
            display: inline-flex; align-items: center; gap: 6px;
            font-size: 10px; font-weight: 600; letter-spacing: 1.2px; text-transform: uppercase;
            color: var(--accent);
        }
        .status-chip {
            display: inline-flex; align-items: center; gap: 5px;
            padding: 4px 12px; border-radius: 20px; font-size: 11px; font-weight: 600;
            letter-spacing: 0.5px; text-transform: uppercase;
            background: ${isCompleted ? 'var(--success-dim)' : isFailed ? 'var(--error-dim)' : 'var(--warn-dim)'};
            color: ${statusColor};
        }
        .status-dot { width: 6px; height: 6px; border-radius: 50%; background: ${statusColor}; }
        .report-header h1 { font-size: 22px; font-weight: 600; line-height: 1.3; margin-bottom: 8px; }
        .report-meta { font-size: 12px; color: var(--muted); display: flex; gap: 16px; flex-wrap: wrap; }
        .report-meta span { display: flex; align-items: center; gap: 4px; }

        /* Stats bar */
        .stats-bar {
            display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px;
            margin-bottom: 28px;
        }
        .stat-card {
            background: var(--surface); border: 1px solid var(--border); border-radius: 10px;
            padding: 14px 16px; text-align: center;
        }
        .stat-value { font-size: 24px; font-weight: 700; color: var(--fg); font-variant-numeric: tabular-nums; }
        .stat-label { font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.8px; margin-top: 2px; }

        /* Section headers */
        .section { margin-bottom: 24px; }
        .section-header {
            font-size: 11px; font-weight: 600; letter-spacing: 1px; text-transform: uppercase;
            color: var(--muted); margin-bottom: 12px; padding-bottom: 8px;
            border-bottom: 1px solid var(--border);
        }

        /* Cards */
        .summary-card {
            background: var(--surface); border: 1px solid var(--border); border-radius: 10px;
            padding: 18px 20px; font-size: 14px; line-height: 1.7;
        }
        .root-cause-card {
            background: var(--surface); border: 1px solid var(--border); border-radius: 10px;
            padding: 18px 20px; font-size: 14px; line-height: 1.7;
            border-left: 3px solid var(--error);
        }

        /* Findings */
        .finding-card {
            background: var(--surface); border: 1px solid var(--border); border-radius: 10px;
            padding: 16px 18px; margin-bottom: 10px;
            transition: border-color 0.2s;
        }
        .finding-card:hover { border-color: var(--accent); }
        .finding-header { display: flex; gap: 12px; align-items: flex-start; margin-bottom: 10px; }
        .finding-icon { width: 28px; height: 28px; flex-shrink: 0; padding-top: 2px; }
        .finding-icon svg { width: 20px; height: 20px; }
        .finding-title { font-size: 14px; font-weight: 600; margin-bottom: 4px; }
        .finding-meta { display: flex; gap: 8px; align-items: center; }
        .tag {
            font-size: 10px; font-weight: 600; letter-spacing: 0.5px; text-transform: uppercase;
            padding: 2px 8px; border-radius: 4px;
            background: var(--accent-dim); color: var(--accent);
        }
        .confidence { font-size: 12px; font-weight: 700; font-variant-numeric: tabular-nums; }
        .finding-body { font-size: 13px; color: var(--muted); line-height: 1.6; margin-left: 40px; }
        .finding-evidence {
            margin: 10px 0 0 40px; padding: 10px 14px;
            background: var(--bg); border-radius: 6px; border: 1px solid var(--border);
        }
        .evidence-item {
            font-size: 12px; color: var(--muted); padding: 3px 0;
            font-family: var(--vscode-editor-font-family, monospace);
        }

        /* Hypotheses */
        .hypothesis-card {
            background: var(--surface); border: 1px solid var(--border); border-radius: 10px;
            padding: 14px 18px; margin-bottom: 8px; border-left: 3px solid var(--warn);
        }
        .hypothesis-header { display: flex; justify-content: space-between; margin-bottom: 6px; }
        .hypothesis-num { font-size: 11px; font-weight: 700; color: var(--warn); }
        .hypothesis-text { font-size: 13px; line-height: 1.6; }

        /* Recommendations */
        .rec-item {
            display: flex; align-items: flex-start; gap: 8px; padding: 6px 0;
            font-size: 13px;
        }
        .rec-item svg { width: 16px; height: 16px; flex-shrink: 0; margin-top: 3px; }

        /* Warning card */
        .warning-card {
            background: var(--warn-dim); border: 1px solid rgba(210,153,34,0.2); border-radius: 10px;
            padding: 14px 18px; font-size: 13px; color: var(--warn);
        }

        /* Action buttons */
        .actions { display: flex; gap: 10px; margin-top: 28px; flex-wrap: wrap; }
        .btn {
            display: inline-flex; align-items: center; gap: 6px;
            padding: 10px 20px; border-radius: 8px; border: 1px solid var(--border);
            cursor: pointer; font-size: 13px; font-weight: 600;
            background: var(--surface); color: var(--fg);
            transition: all 0.2s;
        }
        .btn:hover { border-color: var(--accent); color: var(--accent); }
        .btn svg { width: 16px; height: 16px; }
        .btn-primary {
            background: var(--accent); color: #fff; border-color: var(--accent);
        }
        .btn-primary:hover { background: #4c9aed; border-color: #4c9aed; color: #fff; }
        .btn:disabled { opacity: 0.4; cursor: not-allowed; }

        /* Patch status */
        .patch-status { margin-top: 12px; padding: 12px 16px; border-radius: 8px; display: none; font-size: 13px; }
        .patch-status.show { display: block; }
        .patch-status.loading { background: var(--accent-dim); color: var(--accent); }
        .patch-status.success { background: var(--success-dim); color: var(--success); }
        .patch-status.error { background: var(--error-dim); color: var(--error); }
    </style>
</head>
<body>
    <div class="container">
        <div class="report-header">
            <div class="header-top">
                <div class="header-label">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><path d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"/></svg>
                    Investigation Report
                </div>
                <div class="status-chip"><span class="status-dot"></span>${statusLabel}</div>
            </div>
            <h1>${this.esc(report.task_title || '')}</h1>
            <div class="report-meta">
                <span>Task: ${this.esc(report.task_id || '')}</span>
                <span>${report.started_at?.substring(0, 19).replace('T', ' ') || ''}</span>
            </div>
        </div>

        <div class="stats-bar">
            <div class="stat-card"><div class="stat-value">${findingsCount}</div><div class="stat-label">Findings</div></div>
            <div class="stat-card"><div class="stat-value">${report.recommendations?.length || 0}</div><div class="stat-label">Recommendations</div></div>
            <div class="stat-card"><div class="stat-value">${report.affected_files?.length || 0}</div><div class="stat-label">Files Affected</div></div>
        </div>

        ${report.summary ? `<div class="section"><div class="section-header">Summary</div><div class="summary-card">${this.esc(report.summary)}</div></div>` : ''}
        ${report.root_cause ? `<div class="section"><div class="section-header">Root Cause</div><div class="root-cause-card">${this.esc(report.root_cause)}</div></div>` : ''}
        ${hypothesesHtml ? `<div class="section"><div class="section-header">Hypotheses</div>${hypothesesHtml}</div>` : ''}
        ${findingsHtml ? `<div class="section"><div class="section-header">Findings (${findingsCount})</div>${findingsHtml}</div>` : ''}
        ${recsHtml ? `<div class="section"><div class="section-header">Recommendations</div>${recsHtml}</div>` : ''}
        ${report.error ? `<div class="section"><div class="section-header">Warnings</div><div class="warning-card">${this.esc(report.error)}</div></div>` : ''}

        <div class="actions">
            <button class="btn" id="rerunBtn">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/></svg>
                Re-run Investigation
            </button>
            <button class="btn btn-primary" id="applyBtn">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M13 10V3L4 14h7v7l9-11h-7z"/></svg>
                Apply Fixes
            </button>
        </div>
        <div class="patch-status" id="patchStatus"></div>
    </div>

    <script nonce="${nonce}">
    (function() {
        var vscode = acquireVsCodeApi();
        var rawId = '${this.esc(report.task_id || '')}';
        var taskId = rawId.replace(/^ado-/, '');
        var investigationId = '${this.esc(report.id || '')}';

        document.getElementById('rerunBtn').addEventListener('click', function() {
            vscode.postMessage({ command: 'rerun', taskId: taskId });
        });

        document.getElementById('applyBtn').addEventListener('click', function() {
            var btn = document.getElementById('applyBtn');
            var status = document.getElementById('patchStatus');
            btn.disabled = true;
            btn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="16" height="16" style="animation:spin 1s linear infinite"><path d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/></svg> Generating fixes...';
            status.className = 'patch-status show loading';
            status.textContent = 'Asking Claude to generate code fixes...';
            vscode.postMessage({ command: 'applyFixes', investigationId: investigationId, taskId: taskId });
        });

        window.addEventListener('message', function(e) {
            var msg = e.data;
            var btn = document.getElementById('applyBtn');
            var status = document.getElementById('patchStatus');
            if (msg.command === 'patchStatus') {
                if (msg.status === 'success') {
                    btn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" width="16" height="16" stroke-linecap="round"><polyline points="20 6 9 17 4 12"/></svg> Fixes Applied';
                    status.className = 'patch-status show success';
                    status.textContent = msg.message || 'Done.';
                } else if (msg.status === 'error') {
                    btn.disabled = false;
                    btn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="16" height="16" stroke-linecap="round"><path d="M13 10V3L4 14h7v7l9-11h-7z"/></svg> Apply Fixes';
                    status.className = 'patch-status show error';
                    status.textContent = msg.message || 'Failed.';
                }
            }
        });
    })();
    </style>
    </script>
</body>
</html>`;
    }

    private esc(text: string): string {
        return (text || '')
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#039;').replace(/\n/g, '<br>');
    }
}
