# Desktop Remote Gateway — Implementation Spec

## Goal

Modify the Desktop Electron app to support connecting to a remote lex-hermes
gateway over WebSocket, while keeping local gateway process support for
offline/loopback use. Hybrid: each profile can be either local or remote.

## Architecture

```
Desktop App
  ├── Profile "default" → spawns local gateway process (unchanged)
  ├── Profile "lex-remote" → connects to ws://remote:9119/api/ws?token=xxx
  └── Profile "openclaw" → spawns local openclaw gateway (unchanged)
```

### Profile YAML Changes

Add `remote_url` and `api_key` fields:

```yaml
profiles:
  default:
    # Local profile — unchanged, spawns gateway process

  lex-remote:
    remote_url: "http://192.168.1.100:9119"
    api_key: "sk-xxx"
    # When remote_url is set:
    # 1. Skip local gateway process spawn
    # 2. GET {remote_url}/api/gateway/token with Bearer api_key
    # 3. Use returned ws_url to connect
```

## Implementation Steps

### Step 1 — Profile Config Schema

File: `apps/desktop/electron/profile-config.ts` (or equivalent)

Add fields to the profile type:
```typescript
interface ProfileConfig {
  // ... existing fields ...
  remote_url?: string;  // HTTP(S) base URL of remote lex-hermes
  api_key?: string;     // API key for token exchange
}
```

### Step 2 — Token Fetch

File: `apps/desktop/src/store/gateway.ts` (or new `remote-gateway.ts`)

```typescript
async function fetchRemoteToken(remoteUrl: string, apiKey: string): Promise<string> {
  const resp = await fetch(`${remoteUrl}/api/gateway/token`, {
    headers: { Authorization: `Bearer ${apiKey}` },
  });
  if (!resp.ok) throw new Error(`Gateway token fetch failed: ${resp.status}`);
  const data = await resp.json();
  return data.ws_url; // ws://host:9119/api/ws?token=xxx
}
```

### Step 3 — Modify getConnection

File: `apps/desktop/electron/main.ts` (or the `window.hermesDesktop` provider)

In the `getConnection(profile)` handler, check for `remote_url`:

```typescript
async function getConnection(profile: string) {
  const config = loadProfileConfig(profile);
  if (config.remote_url) {
    // Remote: fetch token, return WebSocket URL
    const wsUrl = await fetchRemoteToken(config.remote_url, config.api_key);
    return { type: "remote", wsUrl };
  }
  // Local: spawn gateway process (existing logic)
  return spawnLocalGateway(profile, config);
}
```

### Step 4 — Modify resolveGatewayWsUrl

File: `packages/shared/src/gateway-url.ts`

Handle the `type: "remote"` connection:

```typescript
export function resolveGatewayWsUrl(
  desktop: HermesDesktop,
  conn: ConnectionConfig,
): string {
  if (conn.type === "remote" && conn.wsUrl) {
    return conn.wsUrl; // Already has ?token=xxx appended
  }
  // Existing local resolution logic
  return resolveLocalWsUrl(desktop, conn);
}
```

### Step 5 — Profile UI (Optional Enhancement)

Add a "Remote Gateway" toggle in the profile settings panel.
When enabled, show URL and API key fields.

## Lex-Hermes Server Side (Already Implemented)

The lex-hermes gateway already supports remote Desktop connections:

- **Token endpoint**: `GET /api/gateway/token`
  - Auth: `Authorization: Bearer <api_key>`
  - Returns: `{ "token": "...", "ws_url": "ws://host:9119/api/ws?token=..." }`
  - API key config: env `HERMES_GATEWAY_API_KEY`

- **WebSocket endpoint**: `ws://host:9119/api/ws?token=...`
  - Protocol: newline-delimited JSON-RPC
  - Same protocol as stdio gateway (fully compatible)

## Testing

1. Start lex-hermes with `HERMES_GATEWAY_API_KEY=test-key-123`
2. Configure Desktop profile with `remote_url: "http://host:9119"` and `api_key: "test-key-123"`
3. Desktop should fetch token, open WebSocket, and function normally
4. Verify all chat, composer, tool calling, and agent features work

## Files to Modify

| File | Change |
|------|--------|
| `apps/desktop/electron/main.ts` | Add remote_url handling in getConnection |
| `apps/desktop/src/store/gateway.ts` | Add fetchRemoteToken, handle remote type |
| `packages/shared/src/gateway-url.ts` | Handle remote connection type |
| Profile config schema | Add remote_url, api_key fields |
