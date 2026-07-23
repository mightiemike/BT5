### Title
`SwapAllowlistExtension` gates the router address instead of the end-user, allowing any unprivileged caller to bypass the swap allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router contract**, not the end-user. If the router is allowlisted (which is required for any router-mediated swap to succeed), every user — including those not on the allowlist — can bypass the gate by routing through the router.

---

### Finding Description

**Call path:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
         → IMetricOmmPoolActions(pool).swap(recipient, ...)   // msg.sender in pool = router
              → ExtensionCalling._beforeSwap(msg.sender=router, ...)
                   → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                        → allowedSwapper[pool][router]  ← checked, NOT the user
```

In `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` directly: [1](#0-0) 

The pool's `_afterSwap` (and symmetrically `_beforeSwap`) passes `msg.sender` — the router — as the `sender` argument to every extension hook: [2](#0-1) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension: [3](#0-2) 

`SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [4](#0-3) 

Here `msg.sender` is the pool and `sender` is the router. The check resolves to `allowedSwapper[pool][router]`. The actual end-user identity is never consulted.

**Two broken scenarios result:**

| Router allowlist state | Effect |
|---|---|
| Router **is** allowlisted | Every user — including those not on the allowlist — can swap by going through the router |
| Router **is not** allowlisted | Individually allowlisted users cannot swap through the router at all |

The first scenario is the fund-impacting one: a pool configured with a swap allowlist to restrict access to specific counterparties is fully open to any caller who uses the public router.

---

### Impact Explanation

A pool admin deploys a pool with `SwapAllowlistExtension` to restrict swaps to a curated set of addresses (e.g., institutional counterparties, KYC'd users, or whitelisted market makers). Any unprivileged user can bypass this gate entirely by calling `MetricOmmSimpleRouter.exactInputSingle` or `exactInput`. The allowlist invariant — "only approved addresses may swap" — is broken. Unauthorized users can consume pool liquidity at oracle-derived prices, causing LP losses and violating the pool's intended access model.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary public swap interface. Any user who discovers that a pool uses `SwapAllowlistExtension` can trivially route through the router. No special privileges, flash loans, or complex setup are required — a single `exactInputSingle` call suffices. The bypass is unconditional whenever the router is allowlisted for the pool.

---

### Recommendation

The extension must gate the **end-user**, not the intermediary. Two approaches:

**Option A — Pass the original user through `extensionData`:** The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires the extension to trust the router's encoding, which introduces a separate trust assumption.

**Option B — Check `sender` only for direct pool calls; require the router to forward the real user as `recipient` and check that instead:** Architecturally cleaner but requires a protocol-level convention.

**Option C (recommended) — Add a `realSender` field to the extension interface or use a dedicated forwarding pattern:** The pool should pass the original initiator (stored in transient storage by the router, similar to how `_getPayer()` works in the callback) so extensions always see the true end-user.

At minimum, document that `SwapAllowlistExtension` only gates direct pool callers and is bypassed by any intermediary contract, so pool admins do not deploy it expecting end-user gating.

---

### Proof of Concept

```solidity
// Pool is configured with SwapAllowlistExtension.
// Pool admin allowlists the router so that normal users can swap through it.
// Alice is NOT on the individual allowlist.

// Alice calls the router directly:
router.exactInputSingle(ExactInputSingleParams({
    pool: allowlistedPool,
    recipient: alice,
    zeroForOne: true,
    amountIn: 1_000e18,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    tokenIn: token0,
    deadline: block.timestamp,
    extensionData: ""
}));

// Inside the pool, msg.sender = router.
// SwapAllowlistExtension checks allowedSwapper[pool][router] → true (router is allowlisted).
// Alice's swap succeeds despite not being on the allowlist.
```

The `allowedSwapper` mapping is keyed by `[pool][sender]` where `sender` is always the router for router-mediated swaps, so the per-user allowlist entries set by the pool admin are never evaluated. [5](#0-4) [6](#0-5)

### Citations

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

**File:** metric-core/contracts/MetricOmmPool.sol (L281-295)
```text
    _afterSwap(
      msg.sender,
      recipient,
      zeroForOne,
      amountSpecified,
      priceLimitX64,
      packedSlot0Initial,
      packedSlot0Final,
      bidPriceX64,
      askPriceX64,
      amount0Delta.toInt128(),
      amount1Delta.toInt128(),
      protocolFeeAmount,
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L11-41)
```text
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;

  constructor(address factory_) BaseMetricExtension(factory_) {}

  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
  }

  function setAllowAllSwappers(address pool_, bool allowed) external onlyPoolAdmin(pool_) {
    allowAllSwappers[pool_] = allowed;
    emit AllowAllSwappersSet(pool_, allowed);
  }

  function isAllowedToSwap(address pool_, address swapper) external view returns (bool) {
    return allowAllSwappers[pool_] || allowedSwapper[pool_][swapper];
  }

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
