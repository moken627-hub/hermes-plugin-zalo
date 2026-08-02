# Zalo Plugin Feature Comparison: Hermes Plugin vs OpenClaw Zalo

## Overview

| Aspect | hermes-plugin-zalo (v2.0) | openclaw-zalo-mod | openclaw-zalo-bot | openclaw-zalouser |
|---|---|---|---|---|
| Approach | Bot API (official) | zca-js (reverse-engineered) | Bot API (official) | zca-js (reverse-engineered) |
| Language | Python | JavaScript/Node.js | TypeScript | TypeScript |
| License | MIT | MIT | MIT | MIT |
| Stars | 6 | 18+ | - | - |
| Risk | Low (official API) | High (Zalo ban risk) | Low (official API) | High (Zalo ban risk) |

## Feature Matrix

### Core Messaging
| Feature | Ours | openclaw-zalo-mod | openclaw-zalo-bot | openclaw-zalouser |
|---|---|---|---|---|
| Send/receive text | Yes | Yes | Yes | Yes |
| Send photos from URL | Yes | Yes | Yes | Yes |
| Typing indicator | Yes | Yes | Yes | Yes |
| Long-polling | Yes | Yes | Yes | Yes |
| Webhook mode | Yes | Yes | Yes | Yes |
| DM Pairing approval | Yes | Yes | Yes | Yes |
| Allowlist/Open policies | Yes | Yes | Yes | Yes |
| Cron delivery | Yes | Yes | Yes | Yes |
| Setup wizard | Yes | No | No | No |
| Zero extra deps | Yes | No | No | No |

### Group Management
| Feature | Ours | openclaw-zalo-mod | openclaw-zalo-bot | openclaw-zalouser |
|---|---|---|---|---|
| Create group | Yes | Yes | Yes | Yes |
| Kick member | Yes | Yes | Yes | Yes |
| Promote/demote admin | Yes | Yes | Yes | Yes |
| Invite member | Yes | Yes | Yes | Yes |
| List members | Yes | Yes | Yes | Yes |
| Group info | Yes | Yes | Yes | Yes |
| Poll creation | Yes | Yes | No | No |
| Pin/unpin message | Yes | Yes | No | No |
| Announcement | Yes | Yes | No | No |
| Mute/Unmute group | No | Yes | No | No |
| Silent mode | No | Yes | No | No |
| Welcome message | No | Yes | No | No |
| Group tracking/follow | No | Yes | No | No |
| Group ID management | No | Yes | No | No |

### Moderation
| Feature | Ours | openclaw-zalo-mod | openclaw-zalo-bot | openclaw-zalouser |
|---|---|---|---|---|
| Anti-spam (rate limit) | Yes | Yes | No | No |
| Suspicious link detection | Yes | Yes | No | No |
| Warn system (3-strike) | Yes | Yes | No | No |
| Warn expiry | Yes (1 week) | Yes | No | No |
| Name triggers | No | Yes | No | No |
| Memory integration | No | Yes | No | No |
| Dashboard UI | No | Yes (local web) | No | No |
| Owner DM control | No | Yes | No | No |

### Slash Commands
| Feature | Ours | openclaw-zalo-mod | openclaw-zalo-bot | openclaw-zalouser |
|---|---|---|---|---|
| /menu | Yes | Yes | No | No |
| /rules | Yes | Yes | No | No |
| /huong-dan | Yes | Yes | No | No |
| /warn | Yes | Yes | No | No |
| /kick | Yes | Yes | No | No |
| /promote | Yes | Yes | No | No |
| /demote | Yes | Yes | No | No |
| /invite | Yes | No | No | No |
| /poll | Yes | No | No | No |
| /pin | Yes | No | No | No |
| /noi-quy | Yes | Yes | No | No |
| /report | Yes | Yes | No | No |
| /mute | No | Yes | No | No |
| /unmute | No | Yes | No | No |
| /silent | No | Yes | No | No |
| /welcome | No | Yes | No | No |
| /follow | No | Yes | No | No |
| /tracking | No | Yes | No | No |
| /memory | No | Yes | No | No |
| /history | No | Yes | No | No |
| /note | No | Yes | No | No |

### CRM & Advanced
| Feature | Ours | openclaw-zalo-mod | openclaw-zalo-bot | openclaw-zalouser |
|---|---|---|---|---|
| CRM contacts | Yes | Yes | No | Yes |
| CSV import/export | Yes | No | No | No |
| Search by phone | Yes | No | No | No |
| Chat history sync | Yes | Yes | No | No |
| History search | Yes | Yes | No | No |
| Scheduled reports | No | Yes | No | No |
| License system | No | Yes | No | No |
| Agent tool surface | No | Yes | No | No |
| Zalo Connect bridge | No | Yes (141 actions) | No | Yes |
| Per-group toggles | No | Yes | No | No |
| QR code login | No | Yes | No | Yes |
| Friend management | No | Yes | No | Yes |

## Compatibility Gaps

### Critical Gaps (blocks enterprise use)
1. Mute/Unmute group
2. Silent mode (only reply when @tagged)
3. Welcome message for new members
4. Group tracking/follow
5. Dashboard UI
6. Owner DM control panel
7. Per-group toggle settings

### Important Gaps (nice-to-have for SMB)
8. Scheduled reports (digest/group)
9. Name triggers (auto-reply when mentioned)
10. Memory integration (save/recall context)
11. CRM pipeline (leads, tasks, audit)
12. License system (trial/PRO/TEAM)
13. Agent tool surface (zalo_mod_* tools)

### Already Covered (our advantage)
14. Bot API approach - no Zalo ban risk
15. Poll creation
16. CSV import/export
17. Search by phone
18. Setup wizard
19. Zero extra deps

## Recommendation

Short-term (v2.x): Fill critical gaps via Bot API: mute/unmute, silent mode, welcome message, per-group toggles, name triggers.

Medium-term (v3.x): Scheduled reports, memory integration via Gbrain, agent tool surface, CRM pipeline.

Long-term (v4.x): Dashboard UI, owner DM control, license system.

Never: Do NOT adopt zca-js - Bot API is the right choice for stability.
