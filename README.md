# claude-usage-meter

Desktop activity monitor and usage meter for Claude Desktop on Windows.

Reads Claude Desktop's UI Automation accessibility tree to detect what Claude is
doing (idle, composing, thinking, tool use, web search, streaming) and scrapes
plan usage from the Claude Code CLI. Displays the results as an animated
pixel-art sprite with usage bars.

## Requirements

- Windows 10/11
- Python 3.13+
- Claude Desktop (Windows Store or standalone)
- Claude Code installed in WSL (for usage polling)
- WSL2 with Ubuntu and tmux

## Install

```
git clone https://github.com/yourname/claude-usage-meter
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

## Sprite characters

The animated sprite characters are from the
[ccstats](https://github.com/eksdeexD/ccstats) project by Zapador, used under
GPLv2. See [THIRD_PARTY.md](THIRD_PARTY.md) for details.

## Licence

GPLv2. See [LICENSE](LICENSE).
