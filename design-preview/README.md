# Jarvis design preview

A local React preview of the native PySide6 dashboard. It is not connected to Python,
credentials, microphone capture, the planner, or execution adapters. Demo commands only
update in-memory UI state. The working desktop application remains `python -m jarvis`.

## Run

```sh
cd design-preview
npm ci
npm run dev
```

Open the localhost URL printed by Vite. `npm run build` type-checks and produces `dist/`.
Ctrl/Cmd+Enter runs a simulated task; Escape cancels it. Theme selection, calendar,
navigation and animation pause are interactive. The OS reduced-motion setting is respected.

## Components and assets

- Actual shadcn/ui Button, Textarea, Badge, Calendar, Dialog, Select and Tooltip,
  retrieved with shadcn MCP and CLI; Radix provides focus management and keyboard behavior.
- Inter is locally served through `@fontsource-variable/inter` (SIL OFL).
- The sphere is an export of the repository's `OrbWidget._render_orb()` artwork.
  It uses a CSS transform/opacity animation, with no WebGL or external asset request.
- 21st was searched before component implementation. The ElevenLabs Orb (component 8602)
  was retrieved and evaluated, but its WebGL implementation produced a blank canvas in QA;
  it and its dependencies were removed from the delivered preview.

The native calendar shows the current local month. The browser calendar additionally
supports date selection for visual/keyboard review. Neither implies a connected calendar.

See [design decisions and QA](../docs/UI_REFINEMENT.md).
