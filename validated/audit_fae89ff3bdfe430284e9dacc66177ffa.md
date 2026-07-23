The complete call chain is now clear. Let me trace it precisely.

**Call chain for a router swap:**

1. User → `MetricOmmSimpleRouter.exactInputSingle()` — `msg.sender` = user
2. Router → `IMetricOmmPoolActions(pool).swap(...)` — `msg.sender` in pool = **router address**
3. Pool → `_beforeSwap(msg.sender=router, ...)` [1](#0-0) 
4. Pool → `abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender=router, ...))` [2](#0-1) 
5. Extension checks: `allowedSwapper[pool][router]` — **not** `allowedSwapper[pool][original_user]` [3](#0-2) 

---

### Title
Router Collapses Per-User Swap Allowlist to a Single Router-Address Check — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps on `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the immediate caller of `pool.swap()`. When any swap is routed through `MetricOmmSimpleRouter`, `sender` is always the router contract address — never the original user. This collapses the per-user allowlist into a single binary question: "is the router allowlisted?" Any user can then bypass the allowlist by routing through the router.

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument:

```solidity
function beforeSwap(address sender, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
``` [4](#0-3) 

`sender` is populated from `msg.sender` inside `MetricOmmPool.swap()`:

```solidity
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient, ...
);
``` [1](#0-0) 

When the router calls `pool.swap()`, `msg.sender` inside the pool is the **router**, not the original user. This is true for every function — `exactInputSingle`, `exactOutputSingle`, `exactInput` (all hops), and `exactOutput` (all hops):

```solidity
// exactInputSingle — router calls pool directly
IMetricOmmPoolActions(params.pool).swap(params.recipient, ..., params.extensionData);
``` [5](#0-4) 

```solidity
// exactInput — every hop, including hop 0
IMetricOmmPoolActions(pool).swap(i == last ? params.recipient : address(this), ...);
``` [6](#0-5) 

The payer identity (`msg.sender` of the router call) is stored only in transient storage for the payment callback — it is never forwarded to the pool or the extension as the `sender` argument.

**Exploit scenario:**

A pool admin deploys a pool with `SwapAllowlistExtension` to restrict swaps to a curated set of addresses (e.g., a whitelist of institutional traders). The admin also allowlists the router (`allowedSwapper[pool][router] = true`) so that those traders can use the standard router UI. Because the extension sees `sender = router` for every router-mediated swap, the check `allowedSwapper[pool][router]` passes for **any** user who calls the router — the per-user allowlist is completely bypassed.

Alternatively, if the admin does not allowlist the router, then all allowlisted users are blocked from using the router, breaking the intended UX and forcing direct pool interaction.

### Impact Explanation

Any unprivileged user can swap in a pool that the admin intended to restrict to specific addresses, by routing through `MetricOmmSimpleRouter`. The allowlist protection is entirely ineffective for router-mediated swaps. This constitutes a broken core access-control mechanism that can lead to unauthorized swaps executing at pool prices, which is a direct bypass of the configured protection.

### Likelihood Explanation

High. The router is the standard entry point for swaps. Any pool that uses `SwapAllowlistExtension` and also allowlists the router (a natural configuration) is immediately exploitable by any user. No special privileges or unusual conditions are required — only a standard router call.

### Recommendation

The extension must verify the **original** user, not the immediate caller. Two options:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData` and the extension decodes and verifies it. This requires the router to be trusted, which it is (it is a known, non-upgradeable contract).

2. **Check both the immediate caller and the original user**: If `sender` is a known router, extract the original user from `extensionData` and check `allowedSwapper[pool][originalUser]` instead.

3. **Disallow router-mediated swaps entirely** for allowlisted pools by checking that `sender == tx.origin` (not recommended in general, but valid for this specific use case).

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension.
2. Pool admin calls setAllowedToSwap(pool, alice, true)       // only alice is allowed
3. Pool admin calls setAllowedToSwap(pool, router, true)      // router is also allowed (for alice to use UI)
4. Bob (not allowlisted) calls router.exactInputSingle({pool: pool, ...})
5. Router calls pool.swap() → msg.sender in pool = router
6. beforeSwap(sender=router, ...) → allowedSwapper[pool][router] == true → PASSES
7. Bob's swap executes successfully despite not being on the allowlist.
```

The invariant "only allowlisted addresses may swap" is violated for every router-mediated swap when the router itself is allowlisted.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-240)
```text
    _beforeSwap(
      msg.sender,
      recipient,
      zeroForOne,
      amountSpecified,
      priceLimitX64,
      packedSlot0Initial,
      bidPriceX64,
      askPriceX64,
      extensionData
    );
```

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
```text
    _callExtensionsInOrder(
      BEFORE_SWAP_ORDER,
      abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (
          sender,
          recipient,
          zeroForOne,
          amountSpecified,
          priceLimitX64,
          packedSlot0Initial,
          bidPriceX64,
          askPriceX64,
          extensionData
        )
      )
    );
```

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
      );
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L104-112)
```text
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );
```
