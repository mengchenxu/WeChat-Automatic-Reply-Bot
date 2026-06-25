# Issue Tracker Configuration

**Provider**: GitHub Issues
**Repository**: `mengchenxu/-`

## Label Mapping

| Role | GitHub Label |
|------|-------------|
| Bug | `bug` |
| Enhancement | `enhancement` |
| Needs Triage | `needs-triage` |
| Needs Info | `needs-info` |
| Ready for Agent | `ready-for-agent` |
| Ready for Human | `ready-for-human` |
| Wontfix | `wontfix` |

## PR Policy

External PRs are also triaged as issues. An external PR is one whose author is not `mengchenxu`.

## Issue Creation

Use `gh issue create --repo mengchenxu/-` with appropriate labels.

When publishing from `/to-issues`: apply `ready-for-agent` label.
When publishing from `/triage`: apply the recommended category + state labels.
