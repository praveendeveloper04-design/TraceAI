/**
 * TraceAI Investigation Tree Provider — Displays investigation history in the sidebar.
 */

import * as vscode from 'vscode';
import { ApiService, InvestigationSummary } from '../services/apiService';

export class InvestigationTreeItem extends vscode.TreeItem {
    constructor(
        public readonly investigation: InvestigationSummary,
    ) {
        super(investigation.task_title || 'Unknown Task', vscode.TreeItemCollapsibleState.None);

        this.id = investigation.id;
        this.description = `${investigation.status} \u00b7 ${investigation.started_at?.substring(0, 10) || ''}`;

        const statusIcon: Record<string, string> = {
            completed: 'check',
            in_progress: 'loading~spin',
            failed: 'error',
            pending: 'clock',
        };
        this.iconPath = new vscode.ThemeIcon(statusIcon[investigation.status] || 'circle-outline');

        this.contextValue = 'investigation';

        this.command = {
            command: 'traceai.viewReportFromTree',
            title: 'View Report',
            arguments: [investigation],
        };
    }
}

export class InvestigationTreeProvider implements vscode.TreeDataProvider<InvestigationTreeItem> {
    private _onDidChangeTreeData = new vscode.EventEmitter<InvestigationTreeItem | undefined | null | void>();
    readonly onDidChangeTreeData = this._onDidChangeTreeData.event;

    private investigations: InvestigationSummary[] = [];

    constructor(private apiService: ApiService) {}

    refresh(): void {
        this._onDidChangeTreeData.fire();
    }

    async loadInvestigations(): Promise<void> {
        try {
            this.investigations = await this.apiService.listInvestigations();
        } catch {
            this.investigations = [];
        }
        this.refresh();
    }

    getTreeItem(element: InvestigationTreeItem): vscode.TreeItem {
        return element;
    }

    getChildren(element?: InvestigationTreeItem): Thenable<InvestigationTreeItem[]> {
        if (element) {
            return Promise.resolve([]);
        }
        return Promise.resolve(
            this.investigations.map(inv => new InvestigationTreeItem(inv)),
        );
    }
}
