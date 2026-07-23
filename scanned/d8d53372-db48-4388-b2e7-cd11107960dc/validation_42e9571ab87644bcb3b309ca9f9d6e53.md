### Title
`SwapAllowlistExtension` gates the router address instead of the end-user, allowing any user to bypass a curated pool's swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the value `MetricOmmPool.swap` passes as its first argument to the hook — which is `msg.sender` of the pool call, i.e. the **router contract address**, not the end-user. When the pool admin allowlists the router (the canonical entry point), every unprivileged user can bypass the curated swap gate by routing through `MetricOmmSimpleRouter`.

---

### Finding Description

**Call chain:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
         → pool.swap(recipient, …)          // msg.sender = router
              → _beforeSwap(msg.sender, …)  // sender arg = router
                   → SwapAllowlistExtension.beforeSwap(sender=router, …)
                        → allowedSwapper[pool][router]  ← checked, NOT the user
```

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
  msg.sender,   // ← router address when called via router
  recipient,
  …
);
```

`ExtensionCalling._beforeSwap` forwards it verbatim:

```solidity
// ExtensionCalling.sol line 95-98
abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, …))
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// SwapAllowlistExtension.sol line 37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool; `sender` is the router. The check resolves to `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

**Two concrete failure modes:**

1. **Allowlist bypass (high impact):** Pool admin allowlists the router as the canonical entry point. Because `sender` is always the router for any user who calls `exactInputSingle` / `exactInput` / `exactOutput`, every unprivileged user passes the check and can swap on a pool that was intended to be curated.

2. **Legitimate user locked out:** Pool admin allowlists individual user EOAs but not the router. Those users cannot swap through the router even though they are explicitly permitted — they must call the pool directly, breaking the expected UX and any integrator that relies on the router.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a known set of counterparties is fully bypassed. Any user can execute swaps against the pool's liquidity at oracle-derived prices, draining LP value through adverse selection or simply trading on a pool that was meant to be private. This is a direct loss of LP principal and a broken core pool invariant (curation policy).

---

### Likelihood Explanation

The router is the primary public entry point documented and used by all periphery tests and integrators. A pool admin who configures a swap allowlist and allowlists the router (the natural setup) immediately opens the bypass to every user. The attacker needs no special privilege, no malicious setup, and no non-standard token — a single `exactInputSingle` call suffices.

---

### Recommendation

`SwapAllowlistExtension.beforeSwap` must gate the **end-user**, not the intermediary. The pool already passes both `sender` (the direct caller, i.e. the router) and `recipient` to the hook. The correct actor to gate is the economic initiator. Two options:

**Option A — check `recipient` instead of `sender`** (works when the user is always the recipient, which is the common case for `exactInputSingle`/`exactOutputSingle` but not multi-hop).

**Option B — require the router to forward the originating user** by adding a `payer`/`originator` field to `extensionData` and having the extension decode and verify it. This is the robust solution for multi-hop paths.

At minimum, the NatSpec for `addLiquidity` and `swap` must document that `sender` is the direct caller (router), not the end-user, so extension authors are not misled.

---

### Proof of Concept

```solidity
// Pool admin sets up a curated pool:
swapExtension.setAllowedToSwap(address(pool), address(router), true);
// Intends: only the router is allowed. But this means ALL users are allowed.

// Attacker (not individually allowlisted) calls:
router.exactInputSingle(ExactInputSingleParams({
    pool: address(pool),
    tokenIn: token0,
    tokenOut: token1,
    zeroForOne: true,
    amountIn: 1_000e18,
    amountOutMinimum: 0,
    recipient: attacker,
    deadline: block.timestamp,
    priceLimitX64: 0,
    extensionData: ""
}));
// Succeeds: extension sees sender=router, allowedSwapper[pool][router]=true → no revert.
```

The `beforeSwap` hook receives `sender = address(router)`, passes the check, and the attacker's swap executes against the curated pool's liquidity. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
