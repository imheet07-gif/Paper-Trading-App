---
title: Draw SVG charts at the container's real width so their text stays readable
date: 2026-10-27
category: design-patterns
module: dashboard and local app charts (dashboard/build.py, app/static/app.js)
problem_type: design_pattern
component: frontend
severity: medium
applies_when:
  - Drawing a chart as inline SVG with text labels (axes, values, legends)
  - The same page is read on a phone and on a desktop
  - A chart uses a fixed viewBox and width 100%
tags: [svg, charts, responsive, mobile, dataviz]
---

# Draw SVG charts at the container's real width so their text stays readable

## Context

Twice in this project a chart was drawn as an SVG with a fixed viewBox
(640 or 760 units wide) and `width: 100%`. The browser scales everything
inside the viewBox, text included: on a 1,200 px desktop the 12 px labels
rendered at about 18 px, and on a 390 px phone at about 5 px, unreadable.
The first time (the portfolio dashboard) the bars were rebuilt as HTML;
the second time (the app's strategy lab) the same mistake came back in new
code.

## Guidance

Pick one of two shapes, never a fixed viewBox with text in it:

- **Draw at the measured width.** Read the container's `clientWidth`, use
  it as the viewBox width so one SVG unit is one CSS pixel, and redraw on
  resize. `app/static/app.js` does this (`widthOf`, `remember` and the
  debounced resize handler); tick counts derive from the width so labels
  thin out instead of overlapping on narrow screens.
- **Use HTML for anything text-heavy.** Labelled bars with values read
  best as HTML elements with CSS widths, as in `dashboard/build.py`
  (`bars_allocation`, `bars_years`); text then follows the page's font size
  at every width.

Then look at it: screenshot desktop and phone widths, light and dark,
before calling a chart done.

## Why This Matters

A chart whose labels are 5 px on a phone fails silently: nothing errors,
the page looks fine on the desktop where it was built, and the reader who
checks from a phone sees noise.

## When to Apply

Every new chart, in the dashboard, the scan page or the app.

## Examples

Before: `<svg viewBox="0 0 760 260" style="width:100%">` with
`<text font-size="11">` labels, so the text scales with the page width.
After: `var W = el.clientWidth; ... "<svg viewBox='0 0 " + W + " 260'>"`,
redrawn on `resize`, so the labels are 11 px at every width.
