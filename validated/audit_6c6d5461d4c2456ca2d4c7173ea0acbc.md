All cited code has been verified against the actual repository. Every claim in the submission is confirmed:

- `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `sender` is the first argument passed from the pool. [1](#0-0) 
- `MetricOmmPool.swap` passes `msg.sender` (the direct caller) as `sender` to `_beforeSwap`. [2](#0-1) 
- `ExtensionCalling._beforeSwap` forwards that `sender` unchanged as the first argument to the extension. [3](#0-2) 
- `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly, making the router `msg.sender` of the pool call. [4](#0-3) 
- `DepositAllowlistExtension.beforeAddLiquidity` correctly checks `owner` (a separate explicit parameter), not `sender`, avoiding the same flaw. [5](#0-4) 

---

Audit Report

## Title
`SwapAllowlistExtension` checks the router address instead of the originating user, allowing any disallowed user to bypass the per-user swap allowlist via `MetricOmmSimpleRouter` - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of `MetricOmmPool.swap`. When a swap is routed through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap` is the router contract, not the originating user. Any pool admin who allowlists the router (the only way to support router-mediated swaps for their intended users) inadvertently grants every unprivileged user the ability to bypass the per-user allowlist by calling through the router.

## Finding Description
**Root cause:** `MetricOmmPool.swap` passes `msg.sender` as `sender` to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // = router when called via MetricOmmSimpleRouter
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards this unchanged as the first argument to the extension. `SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` = pool, `sender` = router. The check is `allowedSwapper[pool][router]`.

**Exploit flow:**
1. Pool is deployed with `SwapAllowlistExtension`.
2. Admin allowlists `alice` and the router (required for alice to use the standard periphery): `allowedSwapper[pool][router] = true`.
3. `bob` (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle(pool=X, ...)`.
4. Router calls `pool.swap(recipient=bob, ...)` — pool sees `msg.sender = router`.
5. Extension checks `allowedSwapper[pool][router] == true` → swap succeeds.
6. `bob`, a disallowed user, has successfully swapped on a curated pool.

**Why existing guards fail:** There is no mechanism in the pool's `swap` signature to separately identify the originating user. The pool only exposes `msg.sender` (the direct caller) and `recipient` (the output recipient). The extension has no way to distinguish a legitimate allowlisted user calling through the router from a disallowed user doing the same. The `DepositAllowlistExtension` avoids this problem because `addLiquidity` accepts an explicit `owner` parameter that is the economic beneficiary regardless of who calls the function; no equivalent parameter exists in `swap`.

## Impact Explanation
Any user disallowed by the pool admin can bypass `SwapAllowlistExtension` by calling any of the four router entry points (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`) against a curated pool that has allowlisted the router. The pool's LP funds are exposed to swaps from unauthorized actors, breaking the core invariant of the allowlist extension. Curated pools (KYC-gated, institutional, or compliance-restricted) are rendered unenforceable. This constitutes broken core pool functionality causing potential loss of funds to LPs who rely on the allowlist for compliance or risk management.

## Likelihood Explanation
The pool admin must allowlist the router for the bypass to work. However, allowlisting the router is the natural and expected action for any pool admin who wants their permitted users to access the pool through the standard periphery interface. `MetricOmmSimpleRouter` is the primary user-facing swap entrypoint. Any pool that intends to support router-mediated swaps for its allowlisted users is vulnerable. The attack requires no special privileges, no capital at risk beyond the swap input, and is repeatable by any address.

## Recommendation
The extension must gate the actual economic actor, not the intermediary. The simplest safe fix without interface changes: require the router to encode `msg.sender` (the originating user) into `extensionData`, and update `SwapAllowlistExtension.beforeSwap` to decode and check that address when `extensionData` is non-empty. Alternatively, add an explicit `originator` field to the pool's swap hook signature so extensions can always check the true initiating user. Until a fix is deployed, document that `SwapAllowlistExtension` only works correctly for direct pool calls (not router-mediated) and do not allowlist the router on pools using this extension.

## Proof of Concept
```solidity
// Setup:
// 1. Pool deployed with SwapAllowlistExtension
// 2. Admin allowlists alice and the router (required for alice to use router)
extension.setAllowedToSwap(pool, alice, true);
extension.setAllowedToSwap(pool, address(router), true);
// 3. bob is NOT allowlisted

// Attack: bob calls the router directly
// pool.swap sees msg.sender = router → extension checks allowedSwapper[pool][router] == true → passes
router.exactInputSingle(ExactInputSingleParams({
    pool: address(pool),
    tokenIn: token0,
    tokenOut: token1,
    zeroForOne: true,
    amountIn: 10_000e6,
    amountOutMinimum: 0,
    recipient: bob,
    deadline: block.timestamp + 1,
    priceLimitX64: 0,
    extensionData: ""
}));
// bob successfully swaps on a pool he is not allowlisted for
```

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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
