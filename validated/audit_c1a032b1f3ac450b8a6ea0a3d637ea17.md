The complete call chain is now fully traced. Here is the definitive assessment.

---

## Full Call-Chain Trace

**Step 1 — Public entrypoint**

`MetricOmmSimpleRouter.exactInputSingle` (and every other `exact*` function) calls `pool.swap()` directly: [1](#0-0) 

The router is `msg.sender` to the pool at this call site.

**Step 2 — Pool passes `msg.sender` as `sender`**

`MetricOmmPool.swap` passes `msg.sender` (= the router) as the `sender` argument to `_beforeSwap`: [2](#0-1) 

**Step 3 — Extension receives the router address as `sender`**

`ExtensionCalling._beforeSwap` ABI-encodes and forwards that same `sender` to every configured extension: [3](#0-2) 

**Step 4 — `SwapAllowlistExtension.beforeSwap` checks the router, not the original user**

```solidity
// msg.sender here = pool address (used as the pool key)
// sender here     = router address (the direct caller of pool.swap)
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [4](#0-3) 

The lookup key is `allowedSwapper[pool][router]`, **not** `allowedSwapper[pool][originalUser]`.

---

## Invariant Verdict

The invariant **ALLOWLIST_GATES_ORIGINAL_USER_NOT_ROUTER is violated**. The extension always gates the direct caller of `pool.swap()`. When the router is the direct caller, the original user's allowlist entry is never consulted.

This produces two concrete, distinct failure modes:

| Scenario | What happens | Impact |
|---|---|---|
| Admin allowlists `userA`; `userA` swaps via router | `allowedSwapper[pool][router]` = false → revert | Allowlisted user is permanently blocked from using the router |
| Admin allowlists the router (to fix the above) | `allowedSwapper[pool][router]` = true → any user passes | Per-user gate is fully bypassed; disallowed users swap freely |

Scenario 1 is reachable with zero admin error — it is triggered by any allowlisted user who uses the public router. Scenario 2 is the natural "fix" a pool admin would attempt, which converts the gate into a complete bypass.

---

### Title
`SwapAllowlistExtension` gates the router address instead of the original user, breaking the per-user swap allowlist for all router-mediated swaps — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
`SwapAllowlistExtension.beforeSwap` receives `sender` = `msg.sender` of `pool.swap()`. When a user swaps through `MetricOmmSimpleRouter`, that value is the router address, not the original user. The allowlist lookup therefore checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`, making the per-user gate either permanently broken (allowlisted users cannot use the router) or trivially bypassable (if the router is allowlisted, all users pass).

### Finding Description
`MetricOmmPool.swap` passes `msg.sender` as `sender` to `_beforeSwap`, which forwards it unchanged to every configured extension. `SwapAllowlistExtension.beforeSwap` uses that `sender` as the swapper identity to check against `allowedSwapper[pool][sender]`. When the call originates from `MetricOmmSimpleRouter.exactInputSingle` (or any `exact*` variant), `msg.sender` at the pool boundary is the router contract, so the extension checks `allowedSwapper[pool][router]`.

The pool admin has no way to express "allow `userA` to swap via the router" without also allowing every other user to swap via the same router. The two outcomes are mutually exclusive: either allowlisted users are blocked from the router, or the router is allowlisted and the gate is open to everyone.

### Impact Explanation
- **Broken core user flow (Medium/High):** Any pool that deploys `SwapAllowlistExtension` to restrict swaps to specific users will silently block those users from using the official router, which is the primary public swap interface.
- **Allowlist bypass (Medium):** A pool admin who allowlists the router address (the natural workaround) inadvertently opens the gate to all users, defeating the purpose of the extension entirely.

### Likelihood Explanation
The router is the standard public swap path. Any pool that uses `SwapAllowlistExtension` and expects users to swap via `MetricOmmSimpleRouter` will hit this immediately. No special attacker setup is required; the broken behavior is triggered by normal usage.

### Recommendation
Pass the original user's identity through the call chain. One approach: add a `swapper` parameter to the `beforeSwap` hook signature that the pool populates separately from `sender` (e.g., via a `msg.sender` captured before the router call, or via `extensionData`). Alternatively, `SwapAllowlistExtension` could check `recipient` instead of `sender` if the pool's design guarantees the recipient is the economic beneficiary, but the cleanest fix is to thread the original user address explicitly.

### Proof of Concept
```solidity
// Pool has SwapAllowlistExtension; only userA is allowlisted.
swapExtension.setAllowedToSwap(address(pool), userA, true);

// userA swaps directly → passes (sender = userA)
vm.prank(userA);
pool.swap(userA, false, 1000, type(uint128).max, "", "");

// userA swaps via router → REVERTS (sender = router, not allowlisted)
vm.prank(userA);
router.exactInputSingle(ExactInputSingleParams({
    pool: address(pool),
    recipient: userA,
    ...
}));
// ↑ reverts with NotAllowedToSwap even though userA is allowlisted

// Now admin allowlists the router to "fix" it:
swapExtension.setAllowedToSwap(address(pool), address(router), true);

// userB (NOT allowlisted) can now bypass the gate via router:
vm.prank(userB);
router.exactInputSingle(...); // succeeds — allowlist fully bypassed
```

### Citations

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
