After making ANY change to webview HTML in the TraceAI VS Code extension, you MUST run this checklist:

## Webview Service Worker Fix Checklist

Every `<!DOCTYPE html>` block in these files MUST have the service worker stub as the FIRST element inside `<head>`:

### Files to check:
1. `vscode-extension/src/services/panelManager.ts` — getProgressHtml() and getReportHtml()
2. `vscode-extension/src/views/reportWebview.ts` — getProgressHtml() and getHtml()

### Required pattern:
Every `<head>` must start with:
```html
<head>
    <meta charset="UTF-8">
    <script nonce="${nonce}">if(navigator.serviceWorker){navigator.serviceWorker.register=function(){return Promise.reject()};}</script>
```

For files using `'unsafe-inline'` instead of nonce:
```html
<head>
    <meta charset="UTF-8">
    <script>if(navigator.serviceWorker){navigator.serviceWorker.register=function(){return Promise.reject()};}</script>
```

### How to verify:
Search for `<!DOCTYPE html` in both files. Count the occurrences. Each one MUST have the stub.

- `panelManager.ts`: 2 occurrences (progress HTML + report HTML)
- `reportWebview.ts`: 2 occurrences (progress HTML + report HTML)

Total: 4 HTML blocks, all 4 must have the stub.

### When to run this check:
- After ANY edit to panelManager.ts
- After ANY edit to reportWebview.ts
- After ANY edit that touches webview HTML
- Before every commit that includes webview changes
