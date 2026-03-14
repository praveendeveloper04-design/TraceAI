/**
 * Report Webview — Renders investigation reports in a VS Code webview panel.
 */

import * as vscode from 'vscode';
import { InvestigationReport } from '../services/apiService';

export class ReportWebview {
    private panel: vscode.WebviewPanel | undefined;

    constructor(private extensionUri: vscode.Uri) {}

    show(report: InvestigationReport): void {
        if (this.panel) {
            this.panel.reveal(vscode.ViewColumn.Beside);
        } else {
            this.panel = vscode.window.createWebviewPanel(
                'taskAnalyzerReport',
                `Investigation: ${report.task_title}`,
                vscode.ViewColumn.Beside,
                {
                    enableScripts: true,
                    retainContextWhenHidden: true,
                },
            );

            this.panel.onDidDispose(() => {
                this.panel = undefined;
            });
        }

        this.panel.title = `Investigation: ${report.task_title}`;
        this.panel.webview.html = this.getHtml(report);
    }

    private getHtml(report: InvestigationReport): string {
        const statusColor = report.status === 'completed' ? '#4caf50' : report.status === 'failed' ? '#f44336' : '#ff9800';
        const statusEmoji = report.status === 'completed' ? '&#9989;' : report.status === 'failed' ? '&#10060;' : '&#9203;';

        const findingsHtml = report.findings.map((f, i) => `
            <div class="finding">
                <h3>${i + 1}. ${this.escapeHtml(f.title)}</h3>
                <div class="meta">
                    <span class="badge category">${this.escapeHtml(f.category)}</span>
                    <span class="badge confidence">Confidence: ${(f.confidence * 100).toFixed(0)}%</span>
                </div>
                <p>${this.escapeHtml(f.description)}</p>
                ${f.file_references.length > 0 ? `
                    <div class="files">
                        <strong>Files:</strong>
                        ${f.file_references.map(fr => `<code>${this.escapeHtml(fr)}</code>`).join(', ')}
                    </div>
                ` : ''}
                ${f.evidence.length > 0 ? `
                    <div class="evidence">
                        <strong>Evidence:</strong>
                        <ul>${f.evidence.map(e => `<li>${this.escapeHtml(e)}</li>`).join('')}</ul>
                    </div>
                ` : ''}
            </div>
        `).join('');

        const recommendationsHtml = report.recommendations.map(r =>
            `<li>${this.escapeHtml(r)}</li>`
        ).join('');

        const affectedFilesHtml = report.affected_files.map(f =>
            `<li><code>${this.escapeHtml(f)}</code></li>`
        ).join('');

        return `<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
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
            display: flex;
            gap: 16px;
            align-items: center;
            padding: 12px 16px;
            background: var(--card-bg);
            border-radius: 6px;
            margin-bottom: 20px;
            border-left: 4px solid ${statusColor};
        }
        .status-bar .status { font-weight: bold; color: ${statusColor}; }
        .badge {
            display: inline-block;
            padding: 2px 8px;
            border-radius: 12px;
            font-size: 0.8em;
            margin-right: 6px;
        }
        .badge.category { background: var(--vscode-badge-background); color: var(--vscode-badge-foreground); }
        .badge.confidence { background: rgba(76, 175, 80, 0.2); color: #4caf50; }
        .finding {
            background: var(--card-bg);
            border-radius: 6px;
            padding: 16px;
            margin-bottom: 12px;
            border: 1px solid var(--border);
        }
        .finding .meta { margin-bottom: 8px; }
        .files, .evidence { margin-top: 8px; font-size: 0.9em; }
        code {
            background: var(--vscode-textCodeBlock-background);
            padding: 2px 6px;
            border-radius: 3px;
            font-size: 0.9em;
        }
        .summary-box {
            background: var(--card-bg);
            border-radius: 6px;
            padding: 16px;
            margin: 12px 0;
            border: 1px solid var(--border);
        }
        ul { padding-left: 20px; }
        li { margin-bottom: 4px; }
    </style>
</head>
<body>
    <h1>${statusEmoji} Investigation: ${this.escapeHtml(report.task_title)}</h1>

    <div class="status-bar">
        <span class="status">${report.status.toUpperCase()}</span>
        <span>Task: ${this.escapeHtml(report.task_id)}</span>
        <span>Started: ${report.started_at?.substring(0, 19) || 'N/A'}</span>
        ${report.completed_at ? `<span>Completed: ${report.completed_at.substring(0, 19)}</span>` : ''}
    </div>

    ${report.summary ? `
        <h2>Summary</h2>
        <div class="summary-box">${this.escapeHtml(report.summary)}</div>
    ` : ''}

    ${report.root_cause ? `
        <h2>Root Cause Analysis</h2>
        <div class="summary-box">${this.escapeHtml(report.root_cause)}</div>
    ` : ''}

    ${report.findings.length > 0 ? `
        <h2>Findings (${report.findings.length})</h2>
        ${findingsHtml}
    ` : ''}

    ${report.recommendations.length > 0 ? `
        <h2>Recommendations</h2>
        <ul>${recommendationsHtml}</ul>
    ` : ''}

    ${report.affected_files.length > 0 ? `
        <h2>Affected Files</h2>
        <ul>${affectedFilesHtml}</ul>
    ` : ''}

    ${report.error ? `
        <h2>Error</h2>
        <div class="summary-box" style="border-left: 4px solid #f44336;">
            ${this.escapeHtml(report.error)}
        </div>
    ` : ''}
</body>
</html>`;
    }

    private escapeHtml(text: string): string {
        return text
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;')
            .replace(/\n/g, '<br>');
    }
}
