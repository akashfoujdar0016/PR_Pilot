# 🤖 PR Pilot — Autonomous GitHub Code Review Agent

> An AI-powered assistant built directly inside GitHub to automate PR workflows — zero server hosting required.

---

## 📋 Table of Contents

- [The Problem](#-the-problem)
- [The Solution](#-the-solution)
- [Core Features](#-core-features)
- [How It Works](#-how-it-works)
- [Architecture](#-architecture)
- [Tech Stack](#-tech-stack)
- [Getting Started](#-getting-started)
- [Contributing](#-contributing)

---

## 💡 The Problem

Manual engineering workflows slow down software development in four key ways:

| Pain Point | Description |
|---|---|
| **Human Bottleneck** | Senior developers spend hours manually reading code line-by-line to find simple bugs, syntax mistakes, and style inconsistencies. |
| **Slowing Down Deliveries** | Pull requests (PRs) sit waiting for human reviews, stalling project timelines and delaying product updates. |
| **Missed Compliance & Bad Tracking** | Teams frequently forget to update documentation or write test scripts when making changes, causing future code stability bugs. |
| **Tedious Admin Overhead** | Manually organizing, categorizing with labels, and writing release summaries (changelogs) is boring and prone to human error. |

---

## 🤖 The Solution

**PR Pilot** is an autonomous GitHub Code Review Agent — an AI-powered assistant built directly inside GitHub that automates PR workflows from the moment a developer opens a pull request.

It eliminates the manual bottlenecks above by combining the power of **GitHub Actions** and **Google Gemini 2.5 Flash** into a seamless, serverless pipeline.

---

## ✨ Core Features

### ⚡ Automated First Responder
An intelligent system that instantly reads code changes the moment a developer submits a pull request — no waiting for a human reviewer to find time.

### 🔍 Dual Focus System
Handles **low-level** style and syntax tasks automatically, while simultaneously scanning for **high-level** technical bugs and logic errors.

### 🛡️ Strict Quality Guardrails
Automatically verifies that every piece of modified code comes with a matching documentation update and test coverage — enforcing compliance without manual checklists.

### 📄 Zero-Effort Documentation
Tracks merged updates effortlessly and instantly drafts ready-to-publish changelogs for product updates — removing all admin overhead from the release cycle.

---

## ⚙️ How It Works

PR Pilot follows a 4-step automated pipeline:

```
Developer opens PR
       │
       ▼
┌─────────────────────────────┐
│  1. Event-Driven Triggering │  ← GitHub Actions wakes the agent
└─────────────────────────────┘
       │
       ▼
┌──────────────────────────────────┐
│  2. Deep Reasoning with Gemini   │  ← Raw diffs → Gemini 2.5 Flash
└──────────────────────────────────┘
       │
       ▼
┌─────────────────────────────────────┐
│  3. Structured Feedback Execution   │  ← Inline comments + status checks
└─────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────┐
│  4. Zero Infrastructure Management  │  ← Runs 100% on GitHub's cloud
└──────────────────────────────────────┘
```

### Step 1 — Event-Driven Triggering
Utilizes **GitHub Actions** to automatically wake up the agent when a pull request is:
- Opened
- Synchronized (new commits pushed)
- Merged

### Step 2 — Deep Reasoning with Gemini 2.5
Passes raw code changes (diffs) to **Google's Gemini 2.5 Flash** to extract high-context feedback using its vast input understanding and deep reasoning capabilities.

### Step 3 — Structured Feedback Execution
Converts AI feedback into:
- **Inline code comments** directly on the changed lines
- **Automated status checks** visible on the PR interface

### Step 4 — Zero Infrastructure Management
Runs entirely on **GitHub's cloud environment**, avoiding:
- External servers
- Backend development frameworks
- Hosting costs

---

## 🏗️ Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                        GitHub Repository                         │
│                                                                  │
│   Developer ──► Pull Request ──► GitHub Actions Workflow         │
│                                         │                        │
│                                         ▼                        │
│                              ┌─────────────────────┐            │
│                              │   PR Pilot Agent     │            │
│                              │                      │            │
│                              │  • Fetch PR Diffs    │            │
│                              │  • Call Gemini API   │            │
│                              │  • Post Comments     │            │
│                              │  • Set Check Status  │            │
│                              │  • Generate Changelog│            │
│                              └─────────────────────┘            │
│                                         │                        │
│                                         ▼                        │
│                              Google Gemini 2.5 Flash             │
│                              (AI Reasoning Engine)               │
└──────────────────────────────────────────────────────────────────┘
```

**Key Design Principles:**
- 🔒 **Lightweight & Secure** — no sensitive data leaves GitHub's environment
- 🌐 **Serverless** — zero external server or hosting required
- ⚡ **Real-time** — feedback posted within seconds of a PR being opened
- 🔄 **Event-Driven** — runs only when needed, no idle costs

---

## 🛠️ Tech Stack

| Layer | Technology |
|---|---|
| **CI/CD Automation** | GitHub Actions |
| **AI Reasoning Engine** | Google Gemini 2.5 Flash |
| **Code Review Interface** | GitHub Pull Request API |
| **Hosting / Runtime** | GitHub Actions Cloud (serverless) |
| **Changelog Generation** | Gemini AI + GitHub Events |

---

## 🚀 Getting Started

### Prerequisites
- A GitHub repository
- A Google Gemini API key (`GEMINI_API_KEY`)

### Setup

1. **Clone or navigate to your repository**
   ```bash
   git clone https://github.com/your-org/your-repo.git
   cd your-repo
   ```

2. **Add your Gemini API key as a GitHub Secret**
   - Go to **Settings → Secrets and variables → Actions**
   - Create a new secret: `GEMINI_API_KEY`

3. **Add the PR Pilot workflow file**
   ```bash
   mkdir -p .github/workflows
   # Copy the pr-pilot.yml workflow file into this directory
   ```

4. **Open a Pull Request** — PR Pilot will automatically trigger and post its review!

---

## 🤝 Contributing

Contributions are welcome! Please ensure that any pull requests include:
- [ ] A clear description of the change
- [ ] Updated documentation (if applicable)
- [ ] Test coverage for new functionality

> PR Pilot itself will review your PR automatically! 🚀

---

## 📄 License

This project is open source. See the [LICENSE](LICENSE) file for details.

---

*Built with ❤️ using GitHub Actions & Google Gemini 2.5 Flash*
