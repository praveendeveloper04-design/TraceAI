/**
 * Task Tree Provider — Displays tasks in the VS Code sidebar.
 */

import * as vscode from 'vscode';
import { ApiService, TaskItem } from '../services/apiService';

export class TaskTreeItem extends vscode.TreeItem {
    constructor(
        public readonly task: TaskItem,
        public readonly collapsibleState: vscode.TreeItemCollapsibleState = vscode.TreeItemCollapsibleState.None,
    ) {
        super(task.title, collapsibleState);

        this.id = task.id;
        this.description = `${task.external_id} · ${task.severity}`;
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

        // Command on click
        this.command = {
            command: 'taskAnalyzer.investigateFromTree',
            title: 'Investigate Task',
            arguments: [task],
        };
    }
}

export class TaskTreeProvider implements vscode.TreeDataProvider<TaskTreeItem> {
    private _onDidChangeTreeData = new vscode.EventEmitter<TaskTreeItem | undefined | null | void>();
    readonly onDidChangeTreeData = this._onDidChangeTreeData.event;

    private tasks: TaskItem[] = [];
    private loading = false;

    constructor(private apiService: ApiService) {}

    refresh(): void {
        this._onDidChangeTreeData.fire();
    }

    async loadTasks(assignedTo?: string, query?: string): Promise<void> {
        this.loading = true;
        this.refresh();

        try {
            this.tasks = await this.apiService.fetchTasks(assignedTo, query);
        } catch (error) {
            this.tasks = [];
            throw error;
        } finally {
            this.loading = false;
            this.refresh();
        }
    }

    getTreeItem(element: TaskTreeItem): vscode.TreeItem {
        return element;
    }

    getChildren(element?: TaskTreeItem): Thenable<TaskTreeItem[]> {
        if (element) {
            return Promise.resolve([]);
        }

        if (this.loading) {
            return Promise.resolve([]);
        }

        if (this.tasks.length === 0) {
            return Promise.resolve([]);
        }

        return Promise.resolve(
            this.tasks.map(task => new TaskTreeItem(task)),
        );
    }
}
