# Waffler v3.0.9 - Wizard UX Improvements

## 🎯 Major Improvements

### Setup Wizard Redesign
- **No more scrolling** - Permissions page now fits on screen with wider layout (700px)
- **Real-time permission status** - See checkmarks appear instantly when you grant permissions
- **Smart Next button** - Only enables when both permissions are granted
- **Clearer API key setup** - Choose one provider (Groq or OpenAI) with toggle buttons, no confusion

### Bug Fixes
- **Fixed blank screen for new users** - Wizard now loads properly on first launch
- **Less scary permission popup** - Added explanation for local network access
- **Better Fn key handling** - Experimental fix to suppress language switcher popup (macOS)

## ✨ What's New

### Permissions Page
- 2-column grid layout for side-by-side permission cards
- Green checkmarks (✓) appear within 2 seconds of granting permissions
- Button text changes from "Open System Settings" to "✓ Granted"
- Disabled Next button until both permissions are granted
- No scrolling required on any screen size

### API Keys Page
- Key emoji (🔑) replaces lightning emoji (⚡)
- **Pill-style toggle buttons** to choose between Groq and OpenAI
- Bold text: "(you only need one)" to eliminate confusion
- Progressive disclosure - only one input field visible at a time
- Smooth 300ms fade transitions when switching providers
- Your provider choice is remembered across sessions
- Eye button (👁️) to show/hide API keys

### Under the Hood
- Real-time permission polling (checks every 2 seconds)
- Backend API methods for permission status checking
- localStorage persistence for provider preference
- Comprehensive testing guide added

## 📋 Full Changelog

### Features
- Redesign API keys page with provider selection pills
- Add real-time permission status polling
- Add permissions page grid layout and status styles
- Restructure permissions page HTML with grid layout
- Add macOS permission checking API methods
- Add comprehensive testing guide

### Fixes
- Fix blank screen bug - call checkOnboarding() on app initialization
- Add NSLocalNetworkUsageDescription to reduce permission popup concern
- Experiment: Strip Fn flag instead of suppressing to prevent language popup

### Documentation
- Add wizard UX improvements design spec
- Add TESTING-GUIDE.md with manual test procedures

## 🙏 Credits

Built with [Claude Code](https://claude.com/claude-code) using subagent-driven development.

---

**Full Diff:** https://github.com/jbf-tars/waffler/compare/v3.0.8...v3.0.9
