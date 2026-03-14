/**
 * TraceAI — VS Code Extension Entry Point
 *
 * Single entry point with full auto-detect flow:
 *   1. Auto-bootstrap Python environment (~/.traceai/runtime/venv)
 *   2. Auto-install backend from bundled extension package
 *   3. Auto-start Python backend (with crash recovery)
 *   4. Check configuration status
 *   5. Load cached tasks instantly (fast startup)
 *   6. Fetch fresh tasks async
 *   7. Background refresh every 5 minutes
 *
 * Teammates install the .vsix and everything works automatically.
 * No repo clone or manual setup required.
 */

import * as vscode from 'vscode';
import { ApiService, TaskItem, InvestigationSummary } from './services/apiService';
import { ServerManager } from './services/serverManager';
import { StateManager } from './services/stateManager';
import { TaskCache } from './services/taskCache';
import { TaskTreeProvider } from './providers/taskTreeProvider';
import { InvestigationTreeProvider } from './providers/investigationTreeProvider';
import { ReportWebview } from './views/reportWebview';

let apiService: ApiService;
let serverManager: ServerManager;
let stateManager: StateManager;
let taskCache: TaskCache;
let taskTreeProvider: TaskTreeProvider;
let investigationTreeProvider: InvestigationTreeProvider;
let reportWebview: ReportWebview;
let statusBarItem: vscode.StatusBarItem;
let refreshInterval: ReturnType<typeof setInterval> | undefined;

export function activate(context: vscode.ExtensionContext): void {
    const config = vscode.workspace.getConfiguration('traceai');
    const port = config.get<number>('serverPort', 7420);

    // Initialize services — pass extensionPath so ServerManager can find bundled backend
    apiService = new ApiService(port);
    serverManager = new ServerManager(port, context.extensionPath);
    stateManager = new StateManager(context.globalState);
    taskCache = new TaskCache();
    taskTreeProvider = new TaskTreeProvider(apiService);
    investigationTreeProvider = new InvestigationTreeProvider(apiService);
    reportWebview = new ReportWebview(context.extensionUri);

    // Create status bar item
    statusBarItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 100);
    statusBarItem.command = 'traceai.showStatus';
    statusBarItem.show();
    context.subscriptions.push(statusBarItem);

    // Register tree views
    context.subscriptions.push(
        vscode.window.registerTreeDataProvider('traceai.tasks', taskTreeProvider),
        vscode.window.registerTreeDataProvider('traceai.investigations', investigationTreeProvider),
    );

    // ── Register Commands ────────────────────────────────────────────────

    // Setup wizard — uses the venv Python to run task-analyzer setup
    context.subscriptions.push(
        vscode.commands.registerCommand('traceai.setup', async () => {
            const terminal = vscode.window.createTerminal('TraceAI Setup');
            terminal.show();
            const venvPython = serverManager.getVenvPython();
            // Use & (call operator) for PowerShell compatibility.
            // PowerShell cannot invoke a quoted path directly — "path" -m fails.
            // & "path" -m works in PowerShell AND cmd/bash ignore the & harmlessly.
            terminal.sendText(`& "${venvPython}" -m task_analyzer.cli.main setup`);
        }),
    );

    // Fetch tasks (refresh)
    context.subscriptions.push(
        vscode.commands.registerCommand('traceai.refresh', async () => {
            await refreshTasks();
        }),
    );

    // Backward-compatible alias
    context.subscriptions.push(
        vscode.commands.registerCommand('traceai.refreshTasks', async () => {
            await refreshTasks();
        }),
    );

    // Investigate (from command palette)
    context.subscriptions.push(
        vscode.commands.registerCommand('traceai.investigate', async () => {
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
        vscode.commands.registerCommand('traceai.investigateFromTree', async (task: TaskItem) => {
            await investigateTask(task.external_id);
        }),
    );

    // View report (from command palette)
    context.subscriptions.push(
        vscode.commands.registerCommand('traceai.viewReport', async () => {
            try {
                const investigations = await apiService.listInvestigations();
                if (investigations.length === 0) {
                    vscode.window.showInformationMessage('No investigations found.');
                    return;
                }

                const items = investigations.map(inv => ({
                    label: inv.task_title || 'Unknown',
                    description: `${inv.status} \u00b7 ${inv.started_at?.substring(0, 10) || ''}`,
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
        vscode.commands.registerCommand('traceai.viewReportFromTree', async (inv: InvestigationSummary) => {
            await viewReport(inv.id);
        }),
    );

    // History
    context.subscriptions.push(
        vscode.commands.registerCommand('traceai.history', async () => {
            await investigationTreeProvider.loadInvestigations();
        }),
    );

    // Show Status
    context.subscriptions.push(
        vscode.commands.registerCommand('traceai.showStatus', async () => {
            try {
                const status = await apiService.getStatus();
                const message = [
                    `TraceAI v${status.version}`,
                    `Configured: ${status.configured ? 'Yes' : 'No'}`,
                    `Ticket Source: ${status.ticket_source || 'None'}`,
                    `Repositories: ${status.repositories}`,
                    `Connectors: ${status.connectors}`,
                    `Profiles: ${status.profiles}`,
                ].join('\n');

                vscode.window.showInformationMessage(message, { modal: true });
            } catch {
                vscode.window.showWarningMessage(
                    'TraceAI server is not running.',
                    'Start Server',
                ).then(action => {
                    if (action === 'Start Server') {
                        mainFlow();
                    }
                });
            }
        }),
    );

    // Backward-compatible alias
    context.subscriptions.push(
        vscode.commands.registerCommand('traceai.status', async () => {
            vscode.commands.executeCommand('traceai.showStatus');
        }),
    );

    // ── Run main flow ────────────────────────────────────────────────────

    mainFlow();

    // ── Background refresh ───────────────────────────────────────────────

    refreshInterval = setInterval(() => {
        refreshTasks().catch(() => {
            // Silently fail on background refresh
        });
    }, 5 * 60 * 1000); // 5 minutes

    context.subscriptions.push({
        dispose: () => {
            if (refreshInterval) {
                clearInterval(refreshInterval);
            }
        },
    });
}

// ── Main Flow ────────────────────────────────────────────────────────────────

async function mainFlow(): Promise<void> {
    // A. Status bar: Starting
    statusBarItem.text = '$(loading~spin) TraceAI: Starting...';

    // B. Bootstrap and start server (Python detection, venv, install, start)
    const serverRunning = await serverManager.ensureRunning();
    if (!serverRunning) {
        statusBarItem.text = '$(error) TraceAI: Server offline';
        // ensureRunning() already showed specific error messages
        return;
    }

    // C. Check if configured
    try {
        const status = await apiService.getStatus();
        if (!status.configured) {
            statusBarItem.text = '$(gear) TraceAI: Not configured';
            const action = await vscode.window.showInformationMessage(
                'Welcome to TraceAI. Run setup to configure your ticket source.',
                'Run Setup',
                'Later',
            );
            if (action === 'Run Setup') {
                vscode.commands.executeCommand('traceai.setup');
            }
            return;
        }
    } catch {
        statusBarItem.text = '$(error) TraceAI: Connection failed';
        return;
    }

    // D. Load cached tasks instantly (fast startup)
    statusBarItem.text = '$(loading~spin) TraceAI: Loading tasks...';
    const cachedTasks = await taskCache.loadCached();
    if (cachedTasks.length > 0) {
        taskTreeProvider.setTasks(cachedTasks);
        statusBarItem.text = `$(check) TraceAI: ${cachedTasks.length} tasks (cached)`;
    }

    // E. Fetch fresh tasks async
    await refreshTasks();

    // F. Load investigation history
    try {
        await investigationTreeProvider.loadInvestigations();
    } catch {
        // Silently fail
    }

    // G. Populate sidebar complete
    stateManager.markFirstRunComplete();
}

// ── Helper Functions ─────────────────────────────────────────────────────────

async function refreshTasks(): Promise<void> {
    const config = vscode.workspace.getConfiguration('traceai');
    const assignee = stateManager.getAssignee() || config.get<string>('defaultAssignee', '');

    try {
        const tasks = await apiService.fetchTasks(
            assignee || undefined,
            undefined,
            50,
            ['new', 'active', 'in_progress', 'unknown'],
        );
        taskTreeProvider.setTasks(tasks);
        await taskCache.save(tasks);
        statusBarItem.text = `$(check) TraceAI: ${tasks.length} tasks`;
    } catch (error) {
        // Don't overwrite status bar if we have cached data
        const cached = await taskCache.loadCached();
        if (cached.length === 0) {
            statusBarItem.text = '$(warning) TraceAI: Fetch failed';
        }
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

export function deactivate(): void {
    if (refreshInterval) {
        clearInterval(refreshInterval);
    }
    serverManager?.dispose();
}
