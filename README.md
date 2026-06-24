# claude-usage-meter

Desktop activity monitor and usage meter for Claude Desktop on Windows.

Reads Claude Desktop's UI Automation accessibility tree to detect what Claude is
doing (idle, composing, thinking, tool use, web search, streaming) and scrapes
plan usage from the Claude Code CLI. Displays the results as an animated
pixel-art sprite with usage bars, plus historical stats pages: a usage calendar,
prompt rhythm charts, and 48-hour usage limit graphs.

## Requirements

- Windows 10/11
- Python 3.13+
- Claude Desktop (Windows Store or standalone)
- Claude Code installed in WSL (for usage polling)
- WSL2 with Ubuntu and tmux

## Install

```
git clone https://github.com/boragurel/claude-usage-meter
cd claude-usage-meter
pip install -e .
```

## Run

```
claude-meter
```

This starts three components: the activity monitor (reads Claude Desktop's state),
the usage poller (scrapes plan usage via Claude Code CLI), and the sprite display.

To run without the display:

```
claude-meter --no-sprite
```

## Display

The display is a 300x220px borderless window with five pages, cycled via arrow
buttons at the bottom. Left-drag to move, right-click to close.

- **Sprite** -- animated pixel-art character reflecting Claude's current state,
  with usage bars showing session and weekly plan consumption.
- **Calendar** -- 5-week heatmap of daily peak session usage, colour-coded by
  intensity.
- **Rhythm** -- bar charts of prompts sent by hour (0-23) and by weekday
  (Mon-Sun).
- **Rhythm Matrix** -- weekday-by-hour heatmap showing when you use Claude most.
- **Usage Limits** -- 48-hour history of session and weekly usage peaks,
  colour-coded by proximity to the limit.

Usage history is stored in a local SQLite database
(`%LOCALAPPDATA%\claude-usage-meter\usage_history.db`). Stats pages populate as
data accumulates.

## Sprite characters

The animated sprite characters are from the
[ccstats](https://github.com/eksdeexD/ccstats) project by Zapador, used under
GPLv2. See [THIRD_PARTY.md](THIRD_PARTY.md) for details.

## Licence

GPLv2. See [LICENSE](LICENSE).
