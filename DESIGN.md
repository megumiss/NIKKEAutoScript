# Framer — Style Reference
> A confident builder marketing site that treats the page like a working artboard — a pure dark canvas by default (pure white in light mode), display type set in GT Walsheim Medium with aggressive negative tracking, and a single confident blue (#0099ff) reserved for hyperlinks and selection states. The page rhythm is broken by oversized vibrant gradient atmosphere panels — magenta, violet, orange spotlights — that act as living showcase tiles, not decoration. Every CTA is an inverse pill (white on dark, black on light); every card is a translucent charcoal (or soft gray in light) surface; every section title pulls letter-spacing tight enough to feel like a poster.

**Theme:** dark (default), with light mode support

**Source website:** [https://www.framer.com/](https://www.framer.com/)  
Use the live official website to compare and validate this extracted snapshot. The current source website remains authoritative.

## Tokens — Colors (Dark, default)

| Name | Value | Token | Role |
|---|---|---|---|
| primary | `#ffffff` | `--color-primary` | primary role extracted from the source design |
| on primary | `#000000` | `--color-on-primary` | on primary role extracted from the source design |
| accent blue | `#0099ff` | `--color-accent-blue` | accent blue role extracted from the source design |
| ink | `#ffffff` | `--color-ink` | ink role extracted from the source design |
| ink muted | `#999999` | `--color-ink-muted` | ink muted role extracted from the source design |
| canvas | `#090909` | `--color-canvas` | canvas role extracted from the source design |
| surface 1 | `#141414` | `--color-surface-1` | surface 1 role extracted from the source design |
| surface 2 | `#1c1c1c` | `--color-surface-2` | surface 2 role extracted from the source design |
| hairline | `#262626` | `--color-hairline` | hairline role extracted from the source design |
| hairline soft | `#1a1a1a` | `--color-hairline-soft` | hairline soft role extracted from the source design |
| inverse canvas | `#ffffff` | `--color-inverse-canvas` | inverse canvas role extracted from the source design |
| inverse ink | `#000000` | `--color-inverse-ink` | inverse ink role extracted from the source design |
| gradient magenta | `#d44df0` | `--color-gradient-magenta` | gradient magenta role extracted from the source design |
| gradient violet | `#6a4cf5` | `--color-gradient-violet` | gradient violet role extracted from the source design |
| gradient orange | `#ff7a3d` | `--color-gradient-orange` | gradient orange role extracted from the source design |
| gradient coral | `#ff5577` | `--color-gradient-coral` | gradient coral role extracted from the source design |
| semantic success | `#22c55e` | `--color-semantic-success` | semantic success role extracted from the source design |

## Tokens — Colors (Light)

Light mode inverts the surface/ink roles while keeping the accent, gradient, and semantic tokens identical.

| Name | Value | Token | Role |
|---|---|---|---|
| primary | `#000000` | `--color-primary` | inverse of dark primary; CTAs become black pills on light |
| on primary | `#ffffff` | `--color-on-primary` | inverse of dark on primary |
| accent blue | `#0099ff` | `--color-accent-blue` | unchanged across themes |
| ink | `#000000` | `--color-ink` | inverse of dark ink |
| ink muted | `#666666` | `--color-ink-muted` | inverse of dark ink muted |
| canvas | `#ffffff` | `--color-canvas` | inverse of dark canvas |
| surface 1 | `#f5f5f5` | `--color-surface-1` | inverse of dark surface 1 |
| surface 2 | `#ececec` | `--color-surface-2` | inverse of dark surface 2 |
| hairline | `#e5e5e5` | `--color-hairline` | inverse of dark hairline |
| hairline soft | `#f0f0f0` | `--color-hairline-soft` | inverse of dark hairline soft |
| inverse canvas | `#090909` | `--color-inverse-canvas` | dark canvas reused for inverse panels in light mode |
| inverse ink | `#ffffff` | `--color-inverse-ink` | text on inverse panels in light mode |
| gradient magenta | `#d44df0` | `--color-gradient-magenta` | unchanged across themes |
| gradient violet | `#6a4cf5` | `--color-gradient-violet` | unchanged across themes |
| gradient orange | `#ff7a3d` | `--color-gradient-orange` | unchanged across themes |
| gradient coral | `#ff5577` | `--color-gradient-coral` | unchanged across themes |
| semantic success | `#22c55e` | `--color-semantic-success` | unchanged across themes |

## Tokens — Typography

### GT Walsheim Framer Medium · `--font-primary`
- **Substitute:** Inter, system-ui, sans-serif
- **Weights:** 500
- **Sizes:** 110px
- **Line height:** 0.85
- **Letter spacing:** -5.5px
- **Role:** Brand typography family observed across the documented type scale.

### GT Walsheim Medium · `--font-family-2`
- **Substitute:** Inter, system-ui, sans-serif
- **Weights:** 500
- **Sizes:** 85px, 62px, 32px
- **Line height:** 0.95, 1, 1.13
- **Letter spacing:** -4.25px, -3.1px, -1.0px
- **Role:** Brand typography family observed across the documented type scale.

### Inter · `--font-family-3`
- **Substitute:** Inter, system-ui, sans-serif
- **Weights:** 700
- **Sizes:** 22px
- **Line height:** 1.2
- **Letter spacing:** -0.8px
- **Role:** Brand typography family observed across the documented type scale.

### Inter Variable · `--font-family-4`
- **Substitute:** Inter, system-ui, sans-serif
- **Weights:** 400, 500
- **Sizes:** 24px, 18px, 15px, 14px, 13px, 12px
- **Line height:** 1.3, 1.4, 1.2, 1
- **Letter spacing:** -0.01px, -0.18px, -0.15px, -0.14px, -0.13px, -0.12px
- **Role:** Brand typography family observed across the documented type scale.

### Type Scale

| Role | Size | Line Height | Letter Spacing | Token |
|---|---|---|---|---|
| display-xxl | 110px | 0.85 | -5.5px | `--text-display-xxl` |
| display-xl | 85px | 0.95 | -4.25px | `--text-display-xl` |
| display-lg | 62px | 1 | -3.1px | `--text-display-lg` |
| display-md | 32px | 1.13 | -1.0px | `--text-display-md` |
| headline | 22px | 1.2 | -0.8px | `--text-headline` |
| subhead | 24px | 1.3 | -0.01px | `--text-subhead` |
| body-lg | 18px | 1.3 | -0.18px | `--text-body-lg` |
| body | 15px | 1.3 | -0.15px | `--text-body` |
| body-sm | 14px | 1.4 | -0.14px | `--text-body-sm` |
| caption | 13px | 1.2 | -0.13px | `--text-caption` |
| micro | 12px | 1.2 | -0.12px | `--text-micro` |
| button | 14px | 1 | -0.14px | `--text-button` |

## Tokens — Spacing & Shapes

**Density:** comfortable

### Spacing Scale

| Name | Value | Token |
|---|---|---|
| hair | 1px | `--spacing-hair` |
| xxs | 4px | `--spacing-xxs` |
| xs | 8px | `--spacing-xs` |
| sm | 12px | `--spacing-sm` |
| md | 15px | `--spacing-md` |
| lg | 20px | `--spacing-lg` |
| xl | 30px | `--spacing-xl` |
| xxl | 40px | `--spacing-xxl` |
| section | 96px | `--spacing-section` |

### Border Radius

| Name | Value | Token |
|---|---|---|
| xs | 4px | `--radius-xs` |
| sm | 6px | `--radius-sm` |
| md | 10px | `--radius-md` |
| lg | 15px | `--radius-lg` |
| xl | 20px | `--radius-xl` |
| xxl | 30px | `--radius-xxl` |
| pill | 100px | `--radius-pill` |
| full | 9999px | `--radius-full` |

### Layout

- **Section gap:** 96px
- **Card padding:** 20px
- **Element gap:** 15px
- **Max content width:** 1200px

## Components

### button primary
**Role:** button primary component

- **backgroundColor:** `{colors.primary}`
- **textColor:** `{colors.on-primary}`
- **typography:** `{typography.button}`
- **rounded:** `{rounded.pill}`
- **padding:** `10px 15px`

### button primary pressed
**Role:** button primary pressed component

- **backgroundColor:** `{colors.primary}`
- **textColor:** `{colors.on-primary}`
- **typography:** `{typography.button}`
- **rounded:** `{rounded.pill}`

### button secondary
**Role:** button secondary component

- **backgroundColor:** `{colors.surface-1}`
- **textColor:** `{colors.ink}`
- **typography:** `{typography.button}`
- **rounded:** `{rounded.pill}`
- **padding:** `10px 15px`

### button translucent
**Role:** button translucent component

- **backgroundColor:** `{colors.surface-2}`
- **textColor:** `{colors.ink}`
- **typography:** `{typography.button}`
- **rounded:** `{rounded.xxl}`
- **padding:** `8px 14px`

### button icon circular
**Role:** button icon circular component

- **backgroundColor:** `{colors.surface-1}`
- **textColor:** `{colors.ink}`
- **typography:** `{typography.button}`
- **rounded:** `{rounded.full}`
- **size:** `40px`

### pricing tab default
**Role:** pricing tab default component

- **backgroundColor:** `{colors.canvas}`
- **textColor:** `{colors.ink-muted}`
- **typography:** `{typography.button}`
- **rounded:** `{rounded.pill}`
- **padding:** `8px 14px`

### pricing tab selected
**Role:** pricing tab selected component

- **backgroundColor:** `{colors.surface-2}`
- **textColor:** `{colors.ink}`
- **typography:** `{typography.button}`
- **rounded:** `{rounded.pill}`
- **padding:** `8px 14px`

### text input
**Role:** text input component

- **backgroundColor:** `{colors.surface-1}`
- **textColor:** `{colors.ink}`
- **typography:** `{typography.body}`
- **rounded:** `{rounded.md}`
- **padding:** `10px 14px`

### text input focused
**Role:** text input focused component

- **backgroundColor:** `{colors.surface-1}`
- **textColor:** `{colors.ink}`
- **typography:** `{typography.body}`
- **rounded:** `{rounded.md}`
- **padding:** `10px 14px`

### pricing card
**Role:** pricing card component

- **backgroundColor:** `{colors.surface-1}`
- **textColor:** `{colors.ink}`
- **typography:** `{typography.body}`
- **rounded:** `{rounded.xl}`
- **padding:** `24px`

### pricing card featured
**Role:** pricing card featured component

- **backgroundColor:** `{colors.surface-2}`
- **textColor:** `{colors.ink}`
- **typography:** `{typography.body}`
- **rounded:** `{rounded.xl}`
- **padding:** `24px`

### template card
**Role:** template card component

- **backgroundColor:** `{colors.surface-1}`
- **textColor:** `{colors.ink}`
- **typography:** `{typography.body-sm}`
- **rounded:** `{rounded.lg}`
- **padding:** `12px`

### gradient spotlight card
**Role:** gradient spotlight card component

- **backgroundColor:** `{colors.gradient-violet}`
- **textColor:** `{colors.ink}`
- **typography:** `{typography.subhead}`
- **rounded:** `{rounded.xl}`
- **padding:** `32px`

### gradient spotlight card magenta
**Role:** gradient spotlight card magenta component

- **backgroundColor:** `{colors.gradient-magenta}`
- **textColor:** `{colors.ink}`
- **typography:** `{typography.subhead}`
- **rounded:** `{rounded.xl}`
- **padding:** `32px`

### gradient spotlight card orange
**Role:** gradient spotlight card orange component

- **backgroundColor:** `{colors.gradient-orange}`
- **textColor:** `{colors.ink}`
- **typography:** `{typography.subhead}`
- **rounded:** `{rounded.xl}`
- **padding:** `32px`

### product mockup tile
**Role:** product mockup tile component

- **backgroundColor:** `{colors.surface-1}`
- **textColor:** `{colors.ink}`
- **typography:** `{typography.body-sm}`
- **rounded:** `{rounded.xl}`
- **padding:** `16px`

### feature row
**Role:** feature row component

- **backgroundColor:** `{colors.canvas}`
- **textColor:** `{colors.ink}`
- **typography:** `{typography.body}`
- **rounded:** `{rounded.xs}`

### comparison row
**Role:** comparison row component

- **backgroundColor:** `{colors.canvas}`
- **textColor:** `{colors.ink-muted}`
- **typography:** `{typography.body-sm}`
- **rounded:** `{rounded.xs}`

### top nav
**Role:** top nav component

- **backgroundColor:** `{colors.canvas}`
- **textColor:** `{colors.ink}`
- **typography:** `{typography.body-sm}`
- **rounded:** `{rounded.xs}`
- **height:** `56px`

### faq row
**Role:** faq row component

- **backgroundColor:** `{colors.canvas}`
- **textColor:** `{colors.ink}`
- **typography:** `{typography.body}`
- **rounded:** `{rounded.md}`
- **padding:** `24px`

### footer
**Role:** footer component

- **backgroundColor:** `{colors.canvas}`
- **textColor:** `{colors.ink-muted}`
- **typography:** `{typography.caption}`
- **rounded:** `{rounded.xs}`
- **padding:** `64px 32px`

## Do's and Don'ts

### Do

- Use `--color-primary` for the brand's primary interaction treatment.
- Keep page surfaces anchored to `--color-canvas`.
- Resolve every color through tokens so both dark and light themes work; never hardcode a theme-specific value.
- Preserve every typography style's documented size, line height, and letter spacing.
- Compare major implementation decisions against [the live Framer website](https://www.framer.com/).

### Don't

- Do not introduce colors outside the documented color token set.
- Do not replace `--color-ink` with an arbitrary neutral.
- Do not flatten documented component states or spacing relationships.
- Do not treat this extracted snapshot as newer than the live source website.

## Layout

Use the documented spacing scale and component geometry as the implementation baseline. Validate responsive composition and current page rhythm against [the live source](https://www.framer.com/).
