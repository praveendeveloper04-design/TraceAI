/**
 * TraceAI Task Tree Provider — Displays tasks in the VS Code sidebar
 * grouped by status with collapsible headers.
 *
 * Groups:
 *   > In Progress (3)
 *     BUG-123: Fix login timeout
 *   > New (2)
 *     BUG-101: Null pointer in checkout
 *
 * Welcome nodes shown when empty. Click task -> investigate immediately.
 */

import * as vscode from 'vscode';
import { ApiService, TaskItem } from '../services/apiService';

// ── Status Group Node ────────────────────────────────────────────────────────

class StatusGroupItem extends vscode.TreeItem {
    constructor(
        public readonly status: string,
        public readonly count: number,
    ) {
        super(
            `${StatusGroupItem.formatStatus(status)} (${count})`,
            vscode.TreeItemCollapsibleState.Expanded,
        );
        this.contextValue = 'statusGroup';
        this.iconPath = new vscode.ThemeIcon(StatusGroupItem.statusIcon(status));
    }

    private static formatStatus(status: string): string {
        const map: Record<string, string> = {
            in_progress: 'In Progress',
            new: 'New',
            active: 'Active',
            unknown: 'Unknown',
            resolved: 'Resolved',
            closed: 'Closed',
        };
        return map[status] || status.charAt(0).toUpperCase() + status.slice(1);
    }

    private static statusIcon(status: string): string {
        const map: Record<string, string> = {
            in_progress: 'play-circle',
            new: 'circle-outline',
            active: 'circle-filled',
            unknown: 'question',
            resolved: 'check',
            closed: 'archive',
        };
        return map[status] || 'circle-outline';
    }
}

// ── Task Node ────────────────────────────────────────────────────────────────

export class TaskTreeItem extends vscode.TreeItem {
    constructor(
        public readonly task: TaskItem,
        public readonly collapsibleState: vscode.TreeItemCollapsibleState = vscode.TreeItemCollapsibleState.None,
    ) {
        super(task.title, collapsibleState);

        this.id = task.id;
        this.description = `${task.external_id} \u00b7 ${task.severity}`;
        this.tooltip = new vscode.MarkdownString(
            `**${task.title}**\n\n` +
            `- **Type**: ${task.task_type}\n` +
            `- **Severity**: ${task.severity}\n` +
            `- **Status**: ${task.status}\n` +
            `- **Assigned To**: ${task.assigned_to || 'Unassigned'}\n\n` +
            `${task.description?.substring(0, 300) || 'No description'}`,
        );

        // Icon based on task type
        const iconMap: Record<string, string> = {
            bug: 'bug',
            incident: 'flame',
            user_story: 'book',
            feature: 'lightbulb',
            task: 'tasklist',
        };
        this.iconPath = new vscode.ThemeIcon(iconMap[task.task_type] || 'circle-outline');

        // Context value for menus
        this.contextValue = 'task';

        // Command on click — investigate immediately
        this.command = {
            command: 'traceai.investigateFromTree',
            title: 'Investigate Task',
            arguments: [task],
        };
    }
}

// ── Welcome Node ─────────────────────────────────────────────────────────────

class WelcomeItem extends vscode.TreeItem {
    constructor(message: string, command?: string) {
        super(message, vscode.TreeItemCollapsibleState.None);
        this.iconPath = new vscode.ThemeIcon('info');
        this.contextValue = 'welcome';
        if (command) {
            this.command = {
                command,
                title: message,
            };
        }
    }
}

// ── Tree Provider ────────────────────────────────────────────────────────────

type TreeNode = StatusGroupItem | TaskTreeItem | WelcomeItem;

export class TaskTreeProvider implements vscode.TreeDataProvider<TreeNode> {
    private _onDidChangeTreeData = new vscode.EventEmitter<TreeNode | undefined | null | void>();
    readonly onDidChangeTreeData = this._onDidChangeTreeData.event;

    private tasks: TaskItem[] = [];
    private loading = false;

    constructor(private apiService: ApiService) {}

    refresh(): void {
        this._onDidChangeTreeData.fire();
    }

    /**
     * Set tasks directly (e.g., from cache or fresh fetch).
     */
    setTasks(tasks: TaskItem[]): void {
        this.tasks = tasks;
        this.refresh();
    }

    async loadTasks(assignedTo?: string, query?: string): Promise<void> {
        this.loading = true;
        this.refresh();

        try {
            this.tasks = await this.apiService.fetchTasks(
                assignedTo,
                query,
                50,
                ['new', 'active', 'in_progress', 'unknown'],
            );
        } catch (error) {
            this.tasks = [];
            throw error;
        } finally {
            this.loading = false;
            this.refresh();
        }
    }

    getTreeItem(element: TreeNode): vscode.TreeItem {
        return element;
    }

    getChildren(element?: TreeNode): Thenable<TreeNode[]> {
        if (this.loading) {
            return Promise.resolve([new WelcomeItem('Loading tasks...')]);
        }

        // Root level: show status groups or welcome message
        if (!element) {
            if (this.tasks.length === 0) {
                return Promise.resolve([
                    new WelcomeItem('No tasks found. Click to refresh.', 'traceai.refreshTasks'),
                    new WelcomeItem('Run setup wizard', 'traceai.setup'),
                ]);
            }

            // Group tasks by status
            const groups = this.groupByStatus();
            const statusOrder = ['in_progress', 'active', 'new', 'unknown'];
            const nodes: StatusGroupItem[] = [];

            for (const status of statusOrder) {
                const count = groups.get(status)?.length || 0;
                if (count > 0) {
                    nodes.push(new StatusGroupItem(status, count));
                }
            }

            // Add any remaining statuses not in the order
            for (const [status, tasks] of groups) {
                if (!statusOrder.includes(status) && tasks.length > 0) {
                    nodes.push(new StatusGroupItem(status, tasks.length));
                }
            }

            return Promise.resolve(nodes);
        }

        // Status group children: show tasks in that group
        if (element instanceof StatusGroupItem) {
            const groups = this.groupByStatus();
            const tasks = groups.get(element.status) || [];
            return Promise.resolve(
                tasks.map(task => new TaskTreeItem(task)),
            );
        }

        return Promise.resolve([]);
    }

    private groupByStatus(): Map<string, TaskItem[]> {
        const groups = new Map<string, TaskItem[]>();
        for (const task of this.tasks) {
            const status = task.status || 'unknown';
            if (!groups.has(status)) {
                groups.set(status, []);
            }
            groups.get(status)!.push(task);
        }
        return groups;
    }
}
