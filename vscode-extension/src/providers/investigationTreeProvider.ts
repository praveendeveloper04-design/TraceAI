/**
 * TraceAI Investigation Tree Provider -- Displays investigation history
 * grouped by status: Running, Completed, Failed.
 */

import * as vscode from 'vscode';
import { ApiService, InvestigationSummary } from '../services/apiService';

// ── Group Header ─────────────────────────────────────────────────────────────

class GroupItem extends vscode.TreeItem {
    constructor(
        public readonly label: string,
        public readonly groupStatus: string,
        public readonly count: number,
    ) {
        super(`${label} (${count})`, vscode.TreeItemCollapsibleState.Expanded);
        this.contextValue = 'investigationGroup';
        const icons: Record<string, string> = {
            running: 'loading~spin',
            completed: 'check',
            failed: 'error',
            cancelled: 'circle-slash',
        };
        this.iconPath = new vscode.ThemeIcon(icons[groupStatus] || 'circle-outline');
    }
}

// ── Investigation Item ───────────────────────────────────────────────────────

export class InvestigationTreeItem extends vscode.TreeItem {
    constructor(
        public readonly investigation: InvestigationSummary,
    ) {
        super(investigation.task_title || 'Unknown Task', vscode.TreeItemCollapsibleState.None);

        this.id = investigation.id;
        const date = investigation.started_at?.substring(0, 10) || '';
        this.description = `${investigation.status} \u00b7 ${date}`;

        const statusIcon: Record<string, string> = {
            completed: 'check',
            completed_with_errors: 'warning',
            in_progress: 'loading~spin',
            running: 'loading~spin',
            failed: 'error',
            cancelled: 'circle-slash',
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

// ── Tree Provider ────────────────────────────────────────────────────────────

type TreeNode = GroupItem | InvestigationTreeItem;

export class InvestigationTreeProvider implements vscode.TreeDataProvider<TreeNode> {
    private _onDidChangeTreeData = new vscode.EventEmitter<TreeNode | undefined | null | void>();
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

    getTreeItem(element: TreeNode): vscode.TreeItem {
        return element;
    }

    getChildren(element?: TreeNode): Thenable<TreeNode[]> {
        // Root level: show groups
        if (!element) {
            const groups: TreeNode[] = [];
            const running = this.investigations.filter(i =>
                i.status === 'in_progress' || i.status === 'running' || i.status === 'pending'
            );
            const completed = this.investigations.filter(i =>
                i.status === 'completed' || i.status === 'completed_with_errors'
            );
            const failed = this.investigations.filter(i =>
                i.status === 'failed' || i.status === 'cancelled'
            );

            if (running.length > 0) {
                groups.push(new GroupItem('Running', 'running', running.length));
            }
            if (completed.length > 0) {
                groups.push(new GroupItem('Completed', 'completed', completed.length));
            }
            if (failed.length > 0) {
                groups.push(new GroupItem('Failed', 'failed', failed.length));
            }

            if (groups.length === 0) {
                return Promise.resolve([]);
            }
            return Promise.resolve(groups);
        }

        // Group children
        if (element instanceof GroupItem) {
            let filtered: InvestigationSummary[];
            if (element.groupStatus === 'running') {
                filtered = this.investigations.filter(i =>
                    i.status === 'in_progress' || i.status === 'running' || i.status === 'pending'
                );
            } else if (element.groupStatus === 'completed') {
                filtered = this.investigations.filter(i =>
                    i.status === 'completed' || i.status === 'completed_with_errors'
                );
            } else {
                filtered = this.investigations.filter(i =>
                    i.status === 'failed' || i.status === 'cancelled'
                );
            }
            return Promise.resolve(filtered.map(inv => new InvestigationTreeItem(inv)));
        }

        return Promise.resolve([]);
    }
}
