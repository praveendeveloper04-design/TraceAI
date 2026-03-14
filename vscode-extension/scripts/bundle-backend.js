/**
 * Pre-package script — copies the Python backend into the extension
 * directory so it gets bundled into the .vsix file.
 *
 * Run before: vsce package
 * Usage: node scripts/bundle-backend.js
 */

const fs = require('fs');
const path = require('path');

const EXTENSION_DIR = path.resolve(__dirname, '..');
const REPO_ROOT = path.resolve(EXTENSION_DIR, '..');
const BACKEND_DEST = path.join(EXTENSION_DIR, 'backend');

// Directories and files to copy from repo root into backend/
const ITEMS_TO_COPY = [
    'src',
    'pyproject.toml',
    'configs',
    'LICENSE',
    'README.md',
];

function copyRecursive(src, dest) {
    const stat = fs.statSync(src);
    if (stat.isDirectory()) {
        fs.mkdirSync(dest, { recursive: true });
        for (const child of fs.readdirSync(src)) {
            // Skip __pycache__, .pyc, .egg-info, .git
            if (child === '__pycache__' || child.endsWith('.pyc') || child.endsWith('.egg-info') || child === '.git') {
                continue;
            }
            copyRecursive(path.join(src, child), path.join(dest, child));
        }
    } else {
        // Skip .pyc files
        if (src.endsWith('.pyc') || src.endsWith('.pyo')) {
            return;
        }
        fs.mkdirSync(path.dirname(dest), { recursive: true });
        fs.copyFileSync(src, dest);
    }
}

// Clean previous backend bundle
if (fs.existsSync(BACKEND_DEST)) {
    fs.rmSync(BACKEND_DEST, { recursive: true, force: true });
}

console.log('Bundling Python backend into extension...');
console.log(`  Source: ${REPO_ROOT}`);
console.log(`  Dest:   ${BACKEND_DEST}`);

fs.mkdirSync(BACKEND_DEST, { recursive: true });

for (const item of ITEMS_TO_COPY) {
    const srcPath = path.join(REPO_ROOT, item);
    const destPath = path.join(BACKEND_DEST, item);

    if (fs.existsSync(srcPath)) {
        copyRecursive(srcPath, destPath);
        console.log(`  Copied: ${item}`);
    } else {
        console.log(`  Skipped (not found): ${item}`);
    }
}

console.log('Backend bundled successfully.');
