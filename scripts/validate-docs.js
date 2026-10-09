#!/usr/bin/env node
/**
 * Catches documentation drift: repository paths and relative links in the docs
 * that no longer exist. Ported from ww-mobile-app's scripts/validate-docs.js,
 * where it caught some 30 dead references after one rename-heavy quarter.
 *
 * Checked: every *.md under documentation/, plus readme.md, AGENTS.md,
 * e2e/README.md and the READMEs of frontend/ and backend/ when present.
 * A backticked path that starts with a repository folder must exist; a
 * relative markdown link must resolve. External links are not fetched.
 *
 * Usage: node scripts/validate-docs.js
 */

const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const TARGETS = ['documentation', 'readme.md', 'AGENTS.md', 'e2e/README.md', 'frontend/README.md', 'backend/README.md'];

// Paths named in prose that point at the repo. Tree diagrams and globs are
// skipped: they describe shape, not specific files.
const CODE_PATH = /`((?:frontend|backend|e2e|scripts|documentation|test-fixtures|\.github)\/[A-Za-z0-9._/-]+)`/g;
const MD_LINK = /\[[^\]]*\]\(([^)#\s]+)(?:#[^)\s]*)?\)/g;

// Gitignored files the docs legitimately tell you to create yourself.
const EXPECTED_ABSENT = new Set(['frontend/.env', 'backend/.env', 'e2e/.env', 'frontend/.env.local', 'frontend/dist']);

const decode = (s) => { try { return decodeURIComponent(s); } catch { return s; } };

// Archived reports are history: their links described the tree of their day.
const walk = (dir) =>
	fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
		const p = path.join(dir, e.name);
		if (e.isDirectory()) return e.name === 'node_modules' || e.name === '_archive' ? [] : walk(p);
		return p.endsWith('.md') ? [p] : [];
	});

// Development reports quote paths from other repositories and files they plan
// to add, so only their links are checked, not the paths in their prose.
const PROSE_PATHS_SKIPPED = (rel) => rel.startsWith('documentation/development reports/');

const files = TARGETS.flatMap((t) => {
	const p = path.join(ROOT, t);
	if (!fs.existsSync(p)) return [];
	return fs.statSync(p).isDirectory() ? walk(p) : [p];
});

const problems = [];
for (const file of files) {
	const rel = path.relative(ROOT, file).split(path.sep).join('/');
	const body = fs.readFileSync(file, 'utf8');
	const lineOf = (index) => body.slice(0, index).split('\n').length;

	for (const m of PROSE_PATHS_SKIPPED(rel) ? [] : body.matchAll(CODE_PATH)) {
		const target = m[1];
		if (target.includes('*') || target.endsWith('/') || EXPECTED_ABSENT.has(target)) continue;
		// A path quoted beside the name of another repository belongs to it:
		// the docs write "ww-backend `documentation/resources/X.md`".
		const line = body.slice(body.lastIndexOf('\n', m.index) + 1, body.indexOf('\n', m.index));
		if (/ww-backend|ww-mobile-app|wildlife-watcher-backend/.test(line)) continue;
		if (!fs.existsSync(path.join(ROOT, target))) problems.push([rel, lineOf(m.index), `missing path: ${target}`]);
	}
	for (const m of body.matchAll(MD_LINK)) {
		const target = m[1];
		if (/^(https?:|mailto:|data:|file:)/.test(target)) continue;
		const resolved = path.resolve(path.dirname(file), decode(target));
		// A link that climbs out of the repository points at a sibling checkout.
		if (!resolved.startsWith(ROOT + path.sep)) continue;
		if (!fs.existsSync(resolved)) problems.push([rel, lineOf(m.index), `broken link: ${target}`]);
	}
}

console.log(`Checked ${files.length} markdown files.`);
if (problems.length === 0) {
	console.log('All referenced paths and links resolve.');
	process.exit(0);
}
for (const [file, line, message] of problems) console.log(`  ${file}:${line}: ${message}`);
console.log(`\n${problems.length} documentation reference(s) do not resolve.`);
process.exit(1);
