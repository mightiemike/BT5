The call chain is fully traceable. Here is the definitive analysis.

---

**Full call trace:**

1. User → `MetricOmmSimpleRouter::exactInputSingle` (or any `exact*`)
2. Router → `IMetricOmmPoolActions(params.pool).swap(...)` — at this point `msg.sender` inside the pool is the **router**
3. Pool → `_beforeSwap(msg.sender, ...)` — passes the router address as `sender`
4. `ExtensionCalling::_beforeSwap` → `abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender=router, ...))` → calls extension
5. `SwapAllowlistExtension::beforeSwap(sender=router, ...)` — `msg.sender` here is the pool; `sender` is the router

The check at line 37:
```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
```
resolves to: `allowedSwapper[pool][router]` — it checks whether the **router contract** is allowlisted, not the original EOA.

**The identity confusion is real and structural.** The `sender` forwarded through the hook chain is always `msg.sender` of the pool's `swap` call, which is the router when routing is used. There is no mechanism to pass the original user's address — the `extensionData` bytes are ignored by the hook entirely.

**Consequences:**
- If the router is allowlisted for a pool, **any user** can bypass the per-user allowlist by routing through it
- If individual users are allowlisted but the router is not, those users **cannot use the router at all**, breaking core swap functionality

**What the question gets wrong:** The framing about "timed-threshold manipulation," "alternating swap directions across consecutive blocks," "rolling observations," and "stale threshold state" is entirely fabricated — none of these constructs exist in `SwapAllowlistExtension`. The extension has no time-based thresholds, no observations, and no mutable state beyond the admin-set mappings. The "paused for swaps but LP withdrawals remain live" observation is technically accurate (`removeLiquidity` lacks `whenNotPaused`) but is irrelevant to the allowlist bypass.

**What the question gets right:** The core invariant it names — "the exact swapper identity checked by the hook and whether router-mediated swaps preserve that identity" — is the real issue.

---

### Title
Router-Mediated Swaps Pass Router Address as `sender` to `SwapAllowlistExtension`, Bypassing Per-User Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
`SwapAllowlistExtension::beforeSwap` receives `sender` as the immediate caller of `MetricOmmPool::swap`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router, so the hook checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][originalUser]`. This makes the per-user allowlist ineffective for router-mediated swaps.

### Finding Description
In `MetricOmmPool::swap`, the pool passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling::_beforeSwap` forwards this value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension::beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called the pool — the router when routing is used: [3](#0-2) 

The router calls the pool directly without forwarding the original user's identity: [4](#0-3) 

The `extensionData` bytes that could theoretically carry the original user's address are ignored by the hook (the last `bytes calldata` parameter is unnamed and unused).

### Impact Explanation
**Scenario A — Router is allowlisted:** A pool admin allowlists the router address so that users can trade through it. Any unprivileged user can now call `exactInputSingle` and the hook passes, because `allowedSwapper[pool][router] == true`. The per-user allowlist is completely bypassed; the pool's intended access restriction is nullified.

**Scenario B — Individual users are allowlisted, router is not:** Allowlisted users cannot trade through the router at all, because the hook sees the router address and rejects it. Core swap functionality is broken for the intended users.

Both scenarios represent broken core pool functionality. Scenario A is the higher-impact path: an unprivileged attacker can execute swaps in a pool that was designed to restrict trading to specific counterparties (e.g., a private institutional pool or a loss-prevention-gated pool).

### Likelihood Explanation
Any pool that deploys `SwapAllowlistExtension` and also expects users to use the router faces this issue. Allowlisting the router is the natural configuration for a pool that wants to restrict trading to a set of users while still supporting the standard periphery. The misconfiguration is not obvious from the extension's interface.

### Recommendation
The `beforeSwap` hook should accept the original user's address through a verified channel. One approach: the router encodes the original `msg.sender` into `extensionData`, and the hook reads and verifies it. A more robust approach: the pool passes an additional `origin` parameter (analogous to Uniswap v4's `hookData` pattern) that the router populates with the original caller. At minimum, the `SwapAllowlistExtension` documentation must explicitly warn that allowlisting the router grants access to all router users.

### Proof of Concept
```
1. Deploy pool with SwapAllowlistExtension configured.
2. Pool admin calls setAllowedToSwap(pool, router, true)  // allowlist the router
3. Pool admin does NOT allowlist attacker EOA
4. Attacker calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
5. Pool.swap is called with msg.sender = router
6. beforeSwap receives sender = router
7. allowedSwapper[pool][router] == true → check passes
8. Attacker's swap executes despite not being individually allowlisted
```

Direct call (no router) by the same attacker would revert: `allowedSwapper[pool][attacker] == false`.

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

**File:** metric-core/contracts/ExtensionCalling.sol (L149-177)
```text
  function _beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) internal {
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
  }
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
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
