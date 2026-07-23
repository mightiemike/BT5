### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the Actual User, Making the Allowlist Unenforceable for Router-Mediated Swaps — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool, which is always `msg.sender` of the `pool.swap()` call. When a user swaps through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. The extension therefore checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. This produces two fund-impacting failure modes: (1) allowlisted users are silently blocked from using the standard periphery path, and (2) if the router is allowlisted (a natural admin action to permit router-mediated swaps), the allowlist is completely bypassed for every user.

---

### Finding Description

**Call chain:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
         → pool.swap(recipient, ..., extensionData)   [msg.sender = Router]
              → ExtensionCalling._beforeSwap(msg.sender=Router, ...)
                   → SwapAllowlistExtension.beforeSwap(sender=Router, ...)
```

In `MetricOmmPool.swap`, the pool passes `msg.sender` as `sender` to every before-swap hook: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called the pool — the router, not the end user: [3](#0-2) 

The router calls `pool.swap()` directly with no mechanism to forward the original caller's identity: [4](#0-3) 

Because the router is a single shared contract, every user who routes through it presents the same `sender` address to the extension. The extension cannot distinguish between users at all on this path.

---

### Impact Explanation

**Failure mode A — allowlisted users blocked from the router (broken core functionality):**
If the router is not in the allowlist (the default state), every swap through `MetricOmmSimpleRouter` reverts with `NotAllowedToSwap`, even for users the pool admin explicitly allowlisted. Allowlisted users must call `pool.swap()` directly, bypassing the standard periphery entirely. This breaks the core swap flow for curated pools.

**Failure mode B — allowlist bypass (policy bypass / admin-boundary break):**
If the pool admin allowlists the router address — a natural action when the intent is "permit router-mediated swaps" — then `allowedSwapper[pool][router] = true` and every user, regardless of their individual allowlist status, can swap through the router. The curation policy is completely nullified. Any non-allowlisted user can bypass the guard by routing through `MetricOmmSimpleRouter`.

Both modes are fund-impacting: mode A prevents legitimate LPs from trading against their own pool; mode B allows unauthorized parties to drain curated pools that rely on the allowlist as a primary access-control boundary.

---

### Likelihood Explanation

The router is the primary user-facing interface documented and deployed for the protocol. Any allowlisted user who follows the standard periphery path immediately hits failure mode A. Failure mode B is triggered the moment a pool admin allowlists the router — a step any admin would consider when they want to support router-mediated swaps on a curated pool. Both modes are reachable by ordinary, unprivileged actions with no special setup beyond the normal pool configuration.

---

### Recommendation

The extension must gate the economically relevant actor — the end user — not the intermediary. Two viable approaches:

1. **Pass the real caller via `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a coordinated change to the router and the extension.

2. **Check `recipient` as a proxy**: For single-hop swaps the recipient is often the user, but this is not reliable for multi-hop or third-party recipient flows.

3. **Document the limitation**: If direct-pool-call-only enforcement is acceptable, the extension NatDoc must explicitly state it does not work for router-mediated swaps, and the factory should prevent pairing it with router-accessible pools.

The cleanest fix is approach 1: the router appends `abi.encode(msg.sender)` to `extensionData` before forwarding to the pool, and the extension decodes and checks that address when `msg.sender` (the pool) is a known factory pool.

---

### Proof of Concept

```
Setup:
  pool P configured with SwapAllowlistExtension E
  pool admin calls E.setAllowedToSwap(P, Alice, true)   // Alice is allowlisted
  router R is NOT in the allowlist

Step 1 — Failure mode A (allowlisted user blocked):
  Alice calls MetricOmmSimpleRouter.exactInputSingle({pool: P, ...})
  → Router calls P.swap(...) with msg.sender = Router
  → P calls E.beforeSwap(sender=Router, ...)
  → E checks allowedSwapper[P][Router] == false → revert NotAllowedToSwap
  Alice cannot use the router despite being allowlisted.

Step 2 — Failure mode B (bypass when router is allowlisted):
  pool admin calls E.setAllowedToSwap(P, Router, true)  // admin allows router
  Bob (not individually allowlisted) calls MetricOmmSimpleRouter.exactInputSingle({pool: P, ...})
  → Router calls P.swap(...) with msg.sender = Router
  → P calls E.beforeSwap(sender=Router, ...)
  → E checks allowedSwapper[P][Router] == true → swap proceeds
  Bob bypasses the allowlist entirely.
``` [3](#0-2) [5](#0-4) [6](#0-5)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L224-240)
```text
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
    require(amountSpecified != 0, InvalidAmount());

    uint256 packedSlot0Initial = Slot0Library.loadPackedSlot0();
    (uint128 bidPriceX64, uint128 askPriceX64) = _getBidAndAskPriceX64();

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```
