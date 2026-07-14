---
name: sap-slides
description: Create SAP-branded HTML presentations. Wraps the frontend-slides skill with SAP Horizon brand constraints (palette, typography). Use when the user asks to create a SAP presentation, internal deck, BTP demo, or any SAP-branded slides.
---

# SAP Slides

Create SAP-branded presentations by delegating to the `frontend-slides` skill with SAP brand context pre-loaded.

## Step 1: Invoke frontend-slides

Invoke the `frontend-slides` skill from `/Users/I577081/Workdir/Github/frontend-slides`.

## Step 2: Inject SAP Brand Context Before Style Discovery

Before `frontend-slides` Phase 2 (style discovery), apply these constraints. Tell the user you are applying SAP brand constraints.

### Palette (non-negotiable)

Use these CSS variables in every generated presentation:

```css
:root {
  --sap-blue:      #0070F2;
  --sap-dark-blue: #003765;
  --sap-white:     #FFFFFF;
  --sap-gray:      #F5F6F7;
  --sap-cta:       #0064D9;
}
```

Dominant colors must come from this palette. Accent colors and data-visualization colors may extend it, but must not clash.

### Typography

1. Attempt to load SAP's "72" typeface via the SAP font CDN:
   ```html
   <link rel="stylesheet" href="https://fonts.sap.com/css?family=72:300,400,600,700">
   ```
2. If the CDN is blocked (corporate firewall) or fails to load, fall back to Google Fonts:
   ```html
   <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@300;400;600;700&display=swap">
   ```
3. Never use: Arial, Roboto, Inter, Helvetica, or system-ui as the primary typeface.

### Logo

Ask the user: *"Do you have an SAP or team logo (SVG or PNG) to include? If so, what's the file path?"*

- If yes: embed it as a base64 `<img>` in title/closing slides. Read the file and convert with `base64 <path>`.
- If no: skip entirely — do not use a placeholder SAP wordmark or generic logo.

### Design Feel

SAP-branded but layout-flexible. Professional corporate quality. This is NOT a Fiori UI component library — do not use Fiori component patterns (shell bar, tiles, etc.) as slide layouts.

## Step 3: Respect User Choices

SAP brand constraints apply to palette and type only. The user's choices for density (low/high), content, layout style, and animation still take full priority. Do not override them.

## Step 4: Hand Off to frontend-slides

Continue with `frontend-slides` Phase 2 through Phase 6:
- Phase 2: Style discovery (3 preview options, all using the SAP palette)
- Phase 3: Full deck generation
- Phase 4: PPT conversion (if applicable)
- Phase 5: Delivery (open in browser)
- Phase 6: Share & export (PDF, Vercel deploy)

## What This Skill Does NOT Do

- Does not re-implement any `frontend-slides` logic
- Does not hardcode a single rigid SAP template
- Does not override the user's density or content choices
