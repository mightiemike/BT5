### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the Real Swapper, Allowing Any User to Bypass a Curated Pool's Swap Allowlist — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` of `pool.swap()`, so the extension checks whether the **router** is allowlisted — not the actual user. Any non-allowlisted user can bypass a curated pool's swap gate by routing through the public router.

---

### Finding Description

The call chain is:

1. User calls `MetricOmmSimpleRouter.exactInputSingle(params)`.
2. The router calls `IMetricOmmPoolActions(params.pool).swap(params.recipient, ...)`.
   At this point `msg.sender` inside `MetricOmmPool.swap` is the **router address**.
3. `MetricOmmPool.swap` calls `_beforeSwap(msg.sender, recipient, ...)`, forwarding the router address as `sender`.
4. `ExtensionCalling._beforeSwap` ABI-encodes `sender = router` and dispatches to the extension.
5. `SwapAllowlistExtension.beforeSwap` executes:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` = pool (correct), but `sender` = **router address**, not the originating user. The check therefore reads `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

Two exploitable outcomes follow:

- **Bypass**: If the pool admin allowlists the router (a natural operational choice so that normal users can trade), every address in the world can swap through the router and pass the gate, defeating the curation entirely.
- **Lockout**: If the pool admin does not allowlist the router, every allowlisted user who uses the router is incorrectly blocked, breaking the intended user experience.

The same structural flaw applies to `exactInput` (multi-hop) and `exactOutput` / `exactOutputSingle`, because in every case the router is the direct caller of `pool.swap()`. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

---

### Impact Explanation

A curated pool that deploys `SwapAllowlistExtension` to restrict trading to a known set of addresses loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. The attacker pays no special cost: they simply call the public router with the target pool address. Tokens flow out of the pool at oracle-derived prices to an address that the pool admin never intended to allow. This is a direct loss of the pool's curation invariant and constitutes a policy bypass with fund-impacting consequences (unauthorized swaps drain LP-owned token reserves at live oracle prices).

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing swap interface for the protocol. Any user who reads the docs or inspects the deployed contracts will find it. No privileged access, no special setup, and no front-running is required. The bypass is reachable in a single transaction by any EOA or contract. Likelihood is **high**.

---

### Recommendation

The extension must gate the **originating user**, not the immediate caller of `pool.swap()`. Two approaches:

1. **Pass `tx.origin` as an additional field** — fragile and incompatible with smart-contract wallets; not recommended.

2. **Require the router to forward the real user identity in `extensionData`** and have the extension decode and verify it. The pool admin would allowlist users, and the router would embed `msg.sender` in the extension payload. The extension would verify the payload is signed or otherwise unforgeable.

3. **Preferred — check `recipient` instead of `sender`** for swap allowlists, since `recipient` is the address that receives the output tokens and is the economically relevant actor. The pool already passes `recipient` as the second argument to `beforeSwap`. Alternatively, redesign the allowlist to key on `recipient` when the intent is to gate who receives tokens.

4. **Alternatively**, document that `SwapAllowlistExtension` is incompatible with router-mediated flows and require pools using it to only accept direct `pool.swap()` calls (enforced by checking that `sender` is not a known router, or by requiring `sender == recipient`).

---

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension; only `alice` is allowlisted.
// allowedSwapper[pool][alice] = true
// allowedSwapper[pool][router] = false  (router not allowlisted)

// Bob (not allowlisted) calls the router:
router.exactInputSingle(ExactInputSingleParams({
    pool: curated_pool,
    recipient: bob,          // bob receives output
    zeroForOne: true,
    amountIn: 1000e6,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    deadline: block.timestamp,
    tokenIn: token0,
    extensionData: ""
}));

// Inside pool.swap():
//   msg.sender = router
//   _beforeSwap(sender=router, recipient=bob, ...)
//
// Inside SwapAllowlistExtension.beforeSwap(sender=router, ...):
//   allowedSwapper[pool][router] == false  → should revert
//
// BUT: if admin allowlisted the router for normal users to trade:
//   allowedSwapper[pool][router] = true
//   → check passes for Bob even though Bob is not allowlisted
//   → Bob's swap executes at oracle price, draining LP reserves
``` [5](#0-4) [6](#0-5) [7](#0-6)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L224-241)
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
