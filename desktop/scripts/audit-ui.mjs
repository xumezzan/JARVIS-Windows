// Source inventory, not a heuristic verdict: preload gestures and draft submissions
// need the manual end-to-end trace in docs/ELECTRON_UI_BACKEND_AUDIT.md.
import fs from "node:fs/promises";
import path from "node:path";
import crypto from "node:crypto";
import { fileURLToPath } from "node:url";
import { API } from "typescript/unstable/sync";
import { SyntaxKind } from "typescript/unstable/ast";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const api = new API({ cwd: root });
const files = [];
try {
  const snapshot = api.updateSnapshot({ openProjects: [path.join(root, "tsconfig.json")] });
  try {
    const program = snapshot.getProjects()[0].program;
    for (const name of program.getSourceFileNames().filter(name =>
      path.resolve(name).startsWith(path.join(root, "src") + path.sep)).sort()) {
      const source = program.getSourceFile(name);
      const icons = new Set();
      const importedIcons = [];
      for (const statement of source.statements) {
        if (statement.kind === SyntaxKind.ImportDeclaration && statement.moduleSpecifier.text === "lucide-react") {
          for (const item of statement.importClause.namedBindings.elements) {
            icons.add(item.name.text);
            importedIcons.push(item.name.text);
          }
        }
      }
      const elements = [];
      function visit(node) {
        if ([SyntaxKind.JsxOpeningElement, SyntaxKind.JsxSelfClosingElement].includes(node.kind)) {
          const tag = node.tagName.getText(source);
          const attrs = Object.fromEntries(node.attributes.properties.filter(a =>
            a.kind === SyntaxKind.JsxAttribute).map(a => [a.name.getText(source), a.initializer?.getText(source) ?? true]));
          const isIcon = icons.has(tag) || tag === "Icon";
          const isControl = ["button", "motion.button", "input", "select", "textarea", "form", "a", "summary"].includes(tag);
          if (isIcon || isControl || Object.keys(attrs).some(name => /^on(Click|Change|Submit)$/.test(name))) {
            const parent = node.kind === SyntaxKind.JsxOpeningElement ? node.parent : null;
            const text = parent?.children?.filter(n => n.kind === SyntaxKind.JsxText)
              .map(n => n.getText(source).trim()).filter(Boolean).join(" ") || "";
            elements.push({ line: source.getLineAndCharacterOfPosition(node.getStart(source)).line + 1,
              tag, kind: isIcon ? "icon" : "control", text, attributes: attrs });
          }
        }
        node.forEachChild(visit);
      }
      visit(source);
      files.push({ file: path.relative(path.dirname(root), name).split(path.sep).join("/"),
        sha256: crypto.createHash("sha256").update(source.text).digest("hex"), importedIcons, elements });
    }
  } finally { snapshot.dispose(); }
} finally { api.close(); }
const result = { schema: 1, note: "Static JSX sites; map templates expand to multiple visible controls. See manual trace for semantics.", files };
const output = process.argv[2];
if (output) await fs.writeFile(output, JSON.stringify(result, null, 2) + "\n");
console.log(JSON.stringify({ files: files.length,
  controls: files.flatMap(f => f.elements).filter(e => e.kind === "control").length,
  icons: files.flatMap(f => f.elements).filter(e => e.kind === "icon").length, output }));
