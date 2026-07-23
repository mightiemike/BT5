Looking at the codebase, I need to trace the swap allowlist guard through the router path to find the analog to the FraxlendPair bypass.

Key files examined:
- `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`
- `metric-periphery/contracts/MetricOmmSimpleRouter.sol`
- `metric-core/contracts/MetricOmmPool.sol`
- `metric-core/contracts/ExtensionCalling.sol`

---

### Title
`SwapAllowlistExtension.beforeSwap` gates the router address instead of the originating user, allowing any actor to bypass a curated pool's swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension` is documented as "Gates `swap` by swapper address, per pool." The hook checks `allowedSwapper[msg.sender][sender]` where `sender` is the first argument forwarded by the pool — which is `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` of `pool.swap()`, so `sender = router`. The allowlist lookup therefore checks the router's address, not the originating user. A pool admin who adds the router to the allowlist (the only way to let allowlisted users trade through the supported periphery) simultaneously opens the gate to every user on the network.

### Finding Description

**Actor binding in `SwapAllowlistExtension.beforeSwap`:**

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`msg.sender` here is the pool (the extension is called by the pool). `sender` is the value the pool passes as the first argument to `beforeSwap`.

**How the pool populates `sender`:**

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap()
    recipient,
    ...
);
```

`msg.sender` of `pool.swap()` is whoever called the pool. When a user calls `router.exactInputSingle(...)`, the router calls `pool.swap(...)`:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
        params.extensionData
    );
```

The router is `msg.sender` of `pool.swap()`, so `sender = router` in the extension. The allowlist lookup becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

**The impossible choice forced on the pool admin:**

| Router on allowlist? | Effect |
|---|---|
| No | Allowlisted users cannot use `MetricOmmSimpleRouter` at all — their swaps revert `NotAllowedToSwap` |
| Yes | Every user on the network can bypass the per-user allowlist by routing through the router |

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users. The guard is structurally misbound.

**Contrast with `DepositAllowlistExtension`**, which correctly gates the economically relevant actor:

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol L32-42
function beforeAddLiquidity(address, address owner, ...)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
```

`owner` is the position owner passed through from the pool, not the direct caller. The liquidity adder preserves `owner` correctly. The swap extension has no equivalent — it checks `sender` which collapses to the router.

### Impact Explanation

A pool deployed with `SwapAllowlistExtension` to restrict trading to a curated set of counterparties (e.g., KYC'd addresses, institutional partners, or whitelisted market makers) loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. The disallowed user receives the full output token amount from the pool. This is a direct bypass of a core access-control mechanism with fund-impacting consequences: the pool's LP assets are exposed to unrestricted swap flow that the pool admin explicitly intended to gate.

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary supported swap interface for end users. A pool admin who configures a swap allowlist will inevitably need to decide whether to add the router. The natural operational action — add the router so allowlisted users can trade through the standard interface — is the exact trigger that opens the bypass. No malicious setup is required; the admin acts in good faith and the invariant breaks silently.

### Recommendation

The `beforeSwap` hook must gate the originating user, not the direct caller of `pool.swap()`. Two approaches:

1. **Pass the originating user in `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. Requires a trusted encoding convention.
2. **Check `recipient` as a proxy**: For single-hop swaps the recipient is often the originating user, but this is not reliable for multi-hop or third-party recipient flows.
3. **Require direct pool interaction for allowlisted pools**: Document that pools using `SwapAllowlistExtension` must not add the router to the allowlist and must require direct `pool.swap()` calls. Enforce this with a clear revert if `msg.sender` (the pool) detects a known router address as `sender`.

The cleanest fix is to have the router forward the originating `msg.sender` in a standardized `extensionData` field and have the extension verify it, similar to how `DepositAllowlistExtension` uses the `owner` parameter.

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true   (alice is the only allowed swapper)
  allowedSwapper[pool][router] = true  (admin adds router so alice can use it)

Attack (bob, a disallowed user):
  bob calls router.exactInputSingle({pool: pool, recipient: bob, ...})
  router calls pool.swap(bob, ...)          // msg.sender = router
  pool calls extension.beforeSwap(router, bob, ...)
  extension checks allowedSwapper[pool][router] == true  ✓
  swap executes; bob receives output tokens

Result:
  bob bypassed the allowlist entirely.
  allowedSwapper[pool][bob] was never set to true.
  The pool's curated access control is silently defeated.
``` [1](#0-0) 
<cite repo="Oyahkilomeikhide/2026-07-metric-dev-oyakhil-main--024" path="metric-core/contracts/MetricOmmPool.sol" start="230" end

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-41)
```text
  function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
  }
```
