/**
 * Task Analyzer — VS Code Extension Entry Point
 *
 * This extension provides a sidebar UI for:
 *   1. Fetching assigned tasks from the configured ticket source
 *   2. Selecting a task for AI investigation
 *   3. Viewing structured investigation reports
 *   4. Browsing investigation history
 *
 * Communication with the Python backend happens via the API service
 * over HTTP to localhost:7420.
 */

import * as vscode from 'vscode';
import * as cp from 'child_process';
import { ApiService, TaskItem, InvestigationSummary } from './services/apiService';
import { TaskTreeProvider } from './providers/taskTreeProvider';
import { InvestigationTreeProvider } from './providers/investigationTreeProvider';
import { ReportWebview } from './views/reportWebview';

let apiService: ApiService;
let taskTreeProvider: TaskTreeProvider;
let investigationTreeProvider: InvestigationTreeProvider;
let reportWebview: ReportWebview;
let serverProcess: cp.ChildProcess | undefined;

export function activate(context: vscode.ExtensionContext): void {
    const config = vscode.workspace.getConfiguration('taskAnalyzer');
    const port = config.get<number>('serverPort', 7420);

    // Initialize services
    apiService = new ApiService(port);
    taskTreeProvider = new TaskTreeProvider(apiService);
    investigationTreeProvider = new InvestigationTreeProvider(apiService);
    reportWebview = new ReportWebview(context.extensionUri);

    // Register tree views
    context.subscriptions.push(
        vscode.window.registerTreeDataProvider('taskAnalyzer.tasks', taskTreeProvider),
        vscode.window.registerTreeDataProvider('taskAnalyzer.investigations', investigationTreeProvider),
    );

    // ── Register Commands ────────────────────────────────────────────────

    // Setup wizard
    context.subscriptions.push(
        vscode.commands.registerCommand('taskAnalyzer.setup', async () => {
            const terminal = vscode.window.createTerminal('Task Analyzer Setup');
            terminal.show();
            terminal.sendText('task-analyzer setup');
        }),
    );

    // Fetch tasks
    context.subscriptions.push(
        vscode.commands.registerCommand('taskAnalyzer.fetchTasks', async () => {
            await fetchTasks();
        }),
    );

    // Investigate (from command palette)
    context.subscriptions.push(
        vscode.commands.registerCommand('taskAnalyzer.investigate', async () => {
            const taskId = await vscode.window.showInputBox({
                prompt: 'Enter the task ID to investigate',
                placeHolder: 'e.g., 12345 or PROJ-123',
            });
            if (taskId) {
                await investigateTask(taskId);
            }
        }),
    );

    // Investigate from tree view
    context.subscriptions.push(
        vscode.commands.registerCommand('taskAnalyzer.investigateFromTree', async (task: TaskItem) => {
            await investigateTask(task.external_id);
        }),
    );

    // View report (from command palette)
    context.subscriptions.push(
        vscode.commands.registerCommand('taskAnalyzer.viewReport', async () => {
            try {
                const investigations = await apiService.listInvestigations();
                if (investigations.length === 0) {
                    vscode.window.showInformationMessage('No investigations found.');
                    return;
                }

                const items = investigations.map(inv => ({
                    label: inv.task_title || 'Unknown',
                    description: `${inv.status} · ${inv.started_at?.substring(0, 10) || ''}`,
                    detail: `ID: ${inv.id}`,
                    id: inv.id,
                }));

                const selected = await vscode.window.showQuickPick(items, {
                    placeHolder: 'Select an investigation to view',
                });

                if (selected) {
                    await viewReport(selected.id);
                }
            } catch (error) {
                vscode.window.showErrorMessage(`Failed to load investigations: ${error}`);
            }
        }),
    );

    // View report from tree
    context.subscriptions.push(
        vscode.commands.registerCommand('taskAnalyzer.viewReportFromTree', async (inv: InvestigationSummary) => {
            await viewReport(inv.id);
        }),
    );

    // History
    context.subscriptions.push(
        vscode.commands.registerCommand('taskAnalyzer.history', async () => {
            await investigationTreeProvider.loadInvestigations();
        }),
    );

    // Status
    context.subscriptions.push(
        vscode.commands.registerCommand('taskAnalyzer.status', async () => {
            try {
                const status = await apiService.getStatus();
                const message = [
                    `Task Analyzer v${status.version}`,
                    `Configured: ${status.configured ? 'Yes' : 'No'}`,
                    `Ticket Source: ${status.ticket_source || 'None'}`,
                    `Repositories: ${status.repositories}`,
                    `Connectors: ${status.connectors}`,
                    `Profiles: ${status.profiles}`,
                ].join('\n');

                vscode.window.showInformationMessage(message, { modal: true });
            } catch {
                vscode.window.showWarningMessage(
                    'Task Analyzer server is not running. Start it with: task-analyzer serve',
                );
            }
        }),
    );

    // ── Auto-start server ────────────────────────────────────────────────

    if (config.get<boolean>('autoStartServer', true)) {
        startServer(port);
    }

    // Initial load
    checkServerAndLoad();
}

// ── Helper Functions ─────────────────────────────────────────────────────────

async function fetchTasks(): Promise<void> {
    const config = vscode.workspace.getConfiguration('taskAnalyzer');
    const assignee = config.get<string>('defaultAssignee', '');

    try {
        await vscode.window.withProgress(
            {
                location: vscode.ProgressLocation.Notification,
                title: 'Fetching tasks...',
                cancellable: false,
            },
            async () => {
                await taskTreeProvider.loadTasks(assignee || undefined);
            },
        );
    } catch (error) {
        vscode.window.showErrorMessage(`Failed to fetch tasks: ${error}`);
    }
}

async function investigateTask(taskId: string): Promise<void> {
    try {
        const report = await vscode.window.withProgress(
            {
                location: vscode.ProgressLocation.Notification,
                title: `Investigating task ${taskId}...`,
                cancellable: false,
            },
            async (progress) => {
                progress.report({ message: 'Fetching task details...' });
                progress.report({ increment: 20 });

                progress.report({ message: 'Running AI investigation...' });
                progress.report({ increment: 30 });

                const result = await apiService.investigate(taskId);

                progress.report({ message: 'Rendering report...' });
                progress.report({ increment: 50 });

                return result;
            },
        );

        // Show the report
        reportWebview.show(report);

        // Refresh investigation history
        await investigationTreeProvider.loadInvestigations();

        if (report.status === 'completed') {
            vscode.window.showInformationMessage(
                `Investigation complete: ${report.findings.length} finding(s)`,
            );
        } else if (report.status === 'failed') {
            vscode.window.showErrorMessage(
                `Investigation failed: ${report.error || 'Unknown error'}`,
            );
        }
    } catch (error) {
        vscode.window.showErrorMessage(`Investigation failed: ${error}`);
    }
}

async function viewReport(reportId: string): Promise<void> {
    try {
        const report = await apiService.getInvestigation(reportId);
        reportWebview.show(report);
    } catch (error) {
        vscode.window.showErrorMessage(`Failed to load report: ${error}`);
    }
}

function startServer(port: number): void {
    try {
        serverProcess = cp.spawn('task-analyzer', ['serve', '--port', port.toString()], {
            stdio: 'ignore',
            detached: true,
            shell: true,
        });
        serverProcess.unref();
    } catch {
        // Server might already be running or not installed
    }
}

async function checkServerAndLoad(): Promise<void> {
    // Wait a moment for server to start
    await new Promise(resolve => setTimeout(resolve, 2000));

    const running = await apiService.isServerRunning();
    if (running) {
        // Auto-load tasks and investigations
        try {
            await taskTreeProvider.loadTasks();
        } catch {
            // Silently fail on initial load
        }
        try {
            await investigationTreeProvider.loadInvestigations();
        } catch {
            // Silently fail
        }
    }
}

export function deactivate(): void {
    if (serverProcess) {
        serverProcess.kill();
    }
}
