Looking at the key files, I need to trace the exact actor identity that `SwapAllowlistExtension.beforeSwap` checks versus what the pool actually passes when a swap is routed through `MetricOmmSimpleRouter`.

**Pool `swap` → extension `sender` binding:**

In `MetricOmmPool.swap`, the pool passes `msg.sender` as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that same `sender` to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then gates on that `sender`: [3](#0-2) 

**Router call path:**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly — making the router itself `msg.sender` to the pool: [4](#0-3) 

The original end-user (`msg.sender` of the router call) is stored only in transient callback context and is never forwarded to the pool or any extension.

**Contrast with `DepositAllowlistExtension`:**

The deposit allowlist correctly gates the economic actor by checking `owner` (explicitly passed through the liquidity adder as the position beneficiary), not `sender`: [5](#0-4) 

The liquidity adder passes the user-supplied `positionOwner` as `owner`: [6](#0-5) 

This asymmetry is the root cause.

---

### Title
`SwapAllowlistExtension.beforeSwap` checks the router's address instead of the end user's, allowing any user to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of the pool's `swap` call. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is the router's address, not the end user's. If the router is allowlisted, any user — regardless of their own allowlist status — can bypass the per-user restriction by routing through the router.

### Finding Description
`MetricOmmPool.swap` passes `msg.sender` as `sender` to `_beforeSwap`, which forwards it verbatim to every configured extension. `SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

where `msg.sender` is the pool and `sender` is the direct caller of `pool.swap`. When `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) is used, the direct caller is the router contract, so the extension evaluates `allowedSwapper[pool][router]`.

The router is a public, permissionless contract — any EOA can call it. If a pool admin allowlists the router (a natural step when they want their allowlisted users to be able to use the standard periphery), the allowlist becomes entirely ineffective: every user on-chain can bypass it by routing through `MetricOmmSimpleRouter`.

The `DepositAllowlistExtension` does not share this flaw because it checks `owner` — the position beneficiary explicitly passed through the liquidity adder — rather than `sender`. The swap path has no equivalent forwarded identity; the original user's address is stored only in the router's transient callback context and is never visible to the extension.

The exact corrupted invariant: `allowedSwapper[pool][user]` is the intended gate, but `allowedSwapper[pool][router]` is what is actually evaluated for every router-mediated swap.

### Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., KYC-verified users, whitelisted market makers, or regulated participants) loses that restriction entirely for router-mediated swaps once the router is allowlisted. Any non-allowlisted user can trade on the curated pool, defeating the access control the pool admin intended to enforce. This constitutes a broken core pool functionality (the allowlist guard) and an admin-boundary break where an unprivileged path bypasses a configured policy.

### Likelihood Explanation
The bypass requires the router to be allowlisted. A pool admin who wants their allowlisted users to interact via the standard periphery will naturally allowlist the router — the same pattern the `DepositAllowlistExtension` handles correctly by design. Because the deposit allowlist works correctly through the liquidity adder, admins have no reason to suspect the swap allowlist behaves differently through the router. The trigger is a valid, semi-trusted admin action taken in good faith, not a malicious setup.

### Recommendation
Mirror the `DepositAllowlistExtension` design: gate on the economic actor, not the direct caller. For swaps, the economic actor is the originating user. One concrete approach is to have `MetricOmmSimpleRouter` encode the original `msg.sender` into `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and verify that identity when `sender` is a known router. Alternatively, the pool interface could be extended to carry an explicit `originator` field analogous to `owner` in `addLiquidity`, so the extension always sees the true end user regardless of which periphery contract is used.

### Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension` as `extension1`, configured in `beforeSwapOrder`.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is allowed.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — router is allowlisted so Alice can use the standard periphery.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, recipient: bob, ...})`.
5. Router calls `pool.swap(bob, ...)` with `msg.sender = router`.
6. `SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[pool][router]` → `true`.
7. Bob's swap executes successfully despite not being on the allowlist.

The allowlist check that should have fired — `allowedSwapper[pool][bob]` — is never evaluated.

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

**File:** metric-core/contracts/ExtensionCalling.sol (L159-177)
```text
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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L194-196)
```text
    try IMetricOmmPoolActions(pool)
      .addLiquidity(positionOwner, salt, deltas, abi.encode(KIND_PAY), extensionData) returns (
      uint256 a0, uint256 a1
```
