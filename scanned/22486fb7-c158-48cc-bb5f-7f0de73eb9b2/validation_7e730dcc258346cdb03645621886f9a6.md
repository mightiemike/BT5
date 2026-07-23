### Title
`SwapAllowlistExtension` checks the router address instead of the actual user, enabling full allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `sender`, which the pool populates with `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender`, so the extension checks the **router's** address against the allowlist, not the actual user's address. A pool admin who allowlists the router to enable router-based swaps inadvertently opens the pool to every user on the internet.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist: [1](#0-0) 

`ExtensionCalling._beforeSwap` populates that `sender` slot with whatever `sender` the pool received internally: [2](#0-1) 

The pool's `swap` function takes `recipient` as its only explicit address parameter; `sender` is therefore `msg.sender` of the `pool.swap()` call. When `MetricOmmSimpleRouter.exactInputSingle` executes, it calls the pool with the user-supplied `params.recipient` as the first argument and the router itself as `msg.sender`: [3](#0-2) 

The same pattern holds for `exactOutputSingle` and every hop of `exactInput`/`exactOutput`: [4](#0-3) 

Consequently, the extension always sees `sender = address(router)`, never the real user. The existing test suite only exercises `TestCaller` contracts that call the pool directly, so this path is never exercised: [5](#0-4) 

---

### Impact Explanation

Two fund-impacting outcomes follow directly:

**Outcome A — Full allowlist bypass (High):** A pool admin who wants allowlisted users to be able to use the router must add the router to the allowlist (`setAllowedToSwap(pool, router, true)`). Once the router is allowlisted, every address on the network can call `router.exactInputSingle` and swap in the curated pool, because the extension only checks whether the router is allowed. The allowlist is completely defeated.

**Outcome B — Allowlisted users permanently blocked from the router (Medium):** If the admin allowlists individual users but not the router, those users cannot use `MetricOmmSimpleRouter` at all — every router-mediated swap reverts with `NotAllowedToSwap`. The supported periphery path is unusable for the intended participants.

Both outcomes break the core invariant that a curated pool enforces the same access policy regardless of which supported public entrypoint reaches it.

---

### Likelihood Explanation

Likelihood is high. `MetricOmmSimpleRouter` is the primary user-facing swap entrypoint documented in the periphery. Any pool admin who deploys a `SwapAllowlistExtension`-gated pool and expects users to interact through the router will encounter this immediately. No special permissions, flash loans, or unusual token behavior are required — a standard `exactInputSingle` call is sufficient.

---

### Recommendation

Pass the economically relevant actor — the address that initiated the swap and will pay for it — through the pool's `swap` call so the extension can check it. Two concrete approaches:

1. **Explicit `payer` parameter:** Add a `payer` (or `originator`) field to the pool's `swap` signature. The router sets it to `msg.sender` before forwarding to the pool. The pool passes it to `_beforeSwap` alongside `recipient`. The extension checks `payer` instead of `sender`.

2. **Extension-data forwarding:** The router encodes `msg.sender` into `extensionData` and the extension decodes and verifies it. This requires the extension to trust the router's encoding, which is weaker than option 1.

Option 1 is preferred because it makes the actor binding explicit and verifiable at the protocol level without trusting peripheral encoding.

---

### Proof of Concept

```
Setup
─────
1. Deploy MetricOmmPool with SwapAllowlistExtension configured as beforeSwap hook.
2. Pool admin calls swapExtension.setAllowedToSwap(pool, router, true)
   (necessary so that allowlisted users can reach the pool through the router).
3. Pool admin does NOT call setAllowedToSwap(pool, attacker, true).
   attacker is explicitly not allowlisted.

Attack
──────
4. attacker calls:
     router.exactInputSingle(ExactInputSingleParams{
       pool:          pool,
       recipient:     attacker,
       zeroForOne:    false,
       amountIn:      X,
       ...
     })

5. Router executes:
     pool.swap(attacker /*recipient*/, false, X, priceLimit, "", extensionData)
   msg.sender of this call = address(router).

6. Pool calls _beforeSwap(sender=router, recipient=attacker, ...).

7. SwapAllowlistExtension.beforeSwap receives sender=router.
   Checks: allowedSwapper[pool][router] → true  ✓
   Hook passes.

8. Swap executes. attacker receives output tokens.
   The allowlist was never consulted for attacker's address.

Expected result without the bug
────────────────────────────────
Step 7 should check the actual initiating user (attacker), find it not allowlisted,
and revert with NotAllowedToSwap.
``` [1](#0-0) [6](#0-5) [2](#0-1)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
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

**File:** metric-periphery/test/extensions/FullMetricExtension.t.sol (L68-74)
```text
  function test_allowedSwapSucceeds() public {
    depositExtension.setAllowedToDeposit(address(pool), _getCallerAddress(0), true);
    swapExtension.setAllowedToSwap(address(pool), address(callers[0]), true);

    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
  }
```
